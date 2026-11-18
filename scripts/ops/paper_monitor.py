#!/usr/bin/env python3
"""Read-only local paper-stack evidence. Never submit orders or approve rules."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import paper_stack

# Execute inside the existing API container so no host DB port or copied secret
# is needed. Every database statement runs in a bounded read-only transaction.
COLLECTOR = r'''
from datetime import datetime, timezone
import hashlib, json, os, shutil, signal
from pathlib import Path
import psycopg2
from psycopg2.extras import RealDictCursor
from app.ashare.calendar_rules import market_session, trading_day_status
from app.ashare.schema import get_database_url

connection = psycopg2.connect(get_database_url(), connect_timeout=5,
    options='-c statement_timeout=10000 -c lock_timeout=1000 -c application_name=paper_readonly_monitor')
connection.set_session(readonly=True, isolation_level='REPEATABLE READ')
accounts, observations, reports, receipts = [], [], [], []
theme_receipts, research_runs = [], []
news_observation = {'status': 'not_installed'}
def evidence_check(path_value, expected):
    try:
        path = Path(path_value).resolve()
        if Path('/app/data') not in path.parents or not path.is_file() or path.stat().st_size > 16*1024*1024:
            return 'unavailable_or_outside_data_volume'
        return 'verified' if hashlib.sha256(path.read_bytes()).hexdigest() == expected else 'hash_mismatch'
    except (OSError, TypeError, ValueError):
        return 'unavailable_or_outside_data_volume'
with connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
    cursor.execute("SELECT to_regclass('qd_ashare_news_receipts') IS NOT NULL AND to_regclass('qd_ashare_news_items') IS NOT NULL AS news_present, to_regclass('qd_ashare_research_runs') IS NOT NULL AS research_present")
    schema = cursor.fetchone()
    news_schema_present, research_schema_present = schema['news_present'], schema['research_present']
    cursor.execute('SELECT process_name, observed_at, details FROM qd_ashare_process_health')
    heartbeats = [dict(row) for row in cursor.fetchall()]
    # The transaction snapshot is already established, including any newly committed heartbeats.
    now = datetime.now(timezone.utc)
    cursor.execute('SELECT tenant_id,account_id,quotes,worker,notifications_encrypted IS NOT NULL AS bot_configured FROM qd_ashare_web ORDER BY tenant_id,account_id')
    web = list(cursor.fetchall())
    for row in web:
        key = (row['tenant_id'], row['account_id'])
        account_ref = hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16]
        cursor.execute("""SELECT a.cash=a.initial_cash+COALESCE((SELECT SUM(CASE WHEN f.side='buy' THEN -f.price*f.quantity-f.fee ELSE f.price*f.quantity-f.fee END) FROM qd_ashare_fills f WHERE f.tenant_id=a.tenant_id AND f.account_id=a.account_id),0) AS cash_ok,
            a.reserved_cash=COALESCE((SELECT SUM(o.reserved_cash) FROM qd_ashare_orders o WHERE o.tenant_id=a.tenant_id AND o.account_id=a.account_id AND o.status IN ('open','partial')),0) AS reservations_ok
            FROM qd_ashare_accounts a WHERE a.tenant_id=%s AND a.account_id=%s""", key)
        ledger = cursor.fetchone()
        if research_schema_present:
            cursor.execute('SELECT id,status,created_at,started_at,completed_at,execution_allowed,approval_status FROM qd_ashare_research_runs WHERE tenant_id=%s AND account_id=%s ORDER BY created_at DESC,id DESC LIMIT 1', key)
            latest = cursor.fetchone()
            if latest:
                research_runs.append({'account_ref': account_ref, **dict(latest)})
        cursor.execute("""WITH expected AS (SELECT symbol,SUM(CASE WHEN side='buy' THEN quantity ELSE -quantity END) AS quantity FROM qd_ashare_fills WHERE tenant_id=%s AND account_id=%s GROUP BY symbol), held AS (SELECT symbol,SUM(quantity) AS quantity FROM qd_ashare_lots WHERE tenant_id=%s AND account_id=%s GROUP BY symbol)
            SELECT COUNT(*) AS mismatches FROM expected FULL JOIN held USING(symbol) WHERE COALESCE(expected.quantity,0)<>COALESCE(held.quantity,0)""", (*key, *key))
        shares_ok = cursor.fetchone()['mismatches'] == 0
        cursor.execute("""SELECT COUNT(*) AS mismatches FROM qd_ashare_orders o LEFT JOIN (SELECT order_id,SUM(quantity) AS quantity,SUM(price*quantity) AS notional,SUM(fee) AS fee FROM qd_ashare_fills WHERE tenant_id=%s AND account_id=%s GROUP BY order_id) f ON f.order_id=o.id WHERE o.tenant_id=%s AND o.account_id=%s AND (o.filled<>COALESCE(f.quantity,0) OR o.notional<>COALESCE(f.notional,0) OR o.commission+o.transfer_fee+o.sell_tax<>COALESCE(f.fee,0) OR (o.status NOT IN ('open','partial') AND o.reserved_cash<>0))""", (*key, *key))
        orders_ok = cursor.fetchone()['mismatches'] == 0
        quotes = [{**{k: quote.get(k) for k in ('symbol','source','exchange_time','received_at','evidence_sha256')},
            'raw_evidence_status': evidence_check(quote.get('evidence_path'), quote.get('evidence_sha256'))} for quote in row['quotes']]
        worker = row['worker']
        accounts.append({'account_ref':account_ref,'state':worker.get('state'),
            'observed_at':worker.get('observed_at'),'error_type':worker.get('error_type'),
            'ledger_reconciled': bool(ledger and all(ledger.values()) and shares_ok and orders_ok),
            'quotes':quotes,'bot_configured':row['bot_configured']})
        cursor.execute('SELECT trading_day,first_observed_at,last_observed_at,poll_count,wall_clock_polls,controlled_time_polls,failed_polls,ready_polls,stale_quote_polls,last_details FROM qd_ashare_day_observations WHERE tenant_id=%s AND account_id=%s ORDER BY trading_day DESC LIMIT 90',key)
        observations.extend({'account_ref':account_ref,**dict(item),
            'is_trading_day':trading_day_status(item['trading_day'])['is_trading_day']} for item in cursor.fetchall())
        cursor.execute("SELECT trading_day,generated_at,report->'reconciliation' AS reconciliation,report->>'provenance' AS provenance FROM qd_ashare_daily_reports WHERE tenant_id=%s AND account_id=%s ORDER BY trading_day DESC LIMIT 90", key)
        reports.extend({'account_ref':account_ref,**dict(item)} for item in cursor.fetchall())
    cursor.execute("SELECT to_regclass('qd_ashare_security_receipts') IS NOT NULL AS present")
    reference_schema_present = cursor.fetchone()['present']
    if reference_schema_present:
        cursor.execute('SELECT trading_day,received_at,source,source_hash,evidence_path,universe_count,status_count FROM qd_ashare_security_receipts ORDER BY received_at DESC LIMIT 10')
        for item in cursor.fetchall():
            receipt = dict(item)
            receipt['raw_evidence_status'] = evidence_check(receipt.pop('evidence_path'), receipt['source_hash'])
            receipt['known_at_semantics'] = 'received_at_not_original_publication_time'
            receipts.append(receipt)
    cursor.execute("SELECT to_regclass('qd_ashare_theme_receipts') IS NOT NULL AS present")
    if cursor.fetchone()['present']:
        cursor.execute("""SELECT DISTINCT ON (board_code) id,board_code,board_name,received_at,
            source_hash,member_count,provider_total,excluded_count
            FROM qd_ashare_theme_receipts ORDER BY board_code,received_at DESC,id DESC LIMIT 10""")
        theme_receipts = [dict(row) for row in cursor.fetchall()]
if theme_receipts:
    from app.ashare.themes import ThemeStore
    for receipt in theme_receipts:
        try:
            verified = ThemeStore(connection).snapshot(receipt['board_code'], known_at=receipt['received_at'], limit=1)
            if verified.get('receipt_id') != receipt['id'] or verified.get('source_hash') != receipt['source_hash']:
                raise ValueError('theme_monitor_receipt_mismatch')
            receipt['evidence_status'] = 'verified'
        except (ValueError, OSError):
            receipt['evidence_status'] = 'invalid_or_unavailable'
        receipt['known_at_semantics'] = 'current_members_observed_at_receipt_not_historical_backfill'
if news_schema_present:
    def news_budget_expired(*_):
        raise TimeoutError('news_monitor_budget_expired')
    previous_alarm = signal.signal(signal.SIGALRM, news_budget_expired)
    signal.setitimer(signal.ITIMER_REAL, 15)
    try:
        from app.ashare.news import NewsStore, MAX_RECEIPTS
        if MAX_RECEIPTS > 20:
            raise ValueError('news_monitor_budget_exceeded')
        verified = NewsStore(connection).list_items(topics=('ai', 'robotics', 'new_energy'), known_at=now, limit=1)
        latest = verified['receipts'][0] if verified['receipts'] else None
        news_observation = {'status': 'verified' if latest else 'not_observed',
                            'verified_receipt_count': len(verified['receipts']), 'item_limit': 1,
                            'coverage': 'limited_recent_sample'}
        if latest:
            news_observation.update({key: latest[key] for key in ('receipt_id', 'received_at', 'source_hash')})
    except ImportError:
        news_observation = {'status': 'module_unavailable'}
    except (ValueError, OSError, KeyError, TypeError, psycopg2.Error):
        news_observation = {'status': 'invalid_or_unavailable'}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_alarm)
connection.close()
safe_flags={key:os.getenv(key,'').lower()=='false' for key in ('LIVE_TRADING_ENABLED','AGENT_LIVE_TRADING_ENABLED','AI_AUTO_PROMOTION_ENABLED')}
print(json.dumps({'observed_at':now.isoformat(),'session':market_session(now),
    'heartbeats':heartbeats,'accounts':accounts,'observations':observations,
    'daily_reports':reports,'safety_flags_disabled':safe_flags,
    'data_free_bytes':shutil.disk_usage('/app/data').free,
    'reference_schema_present':reference_schema_present,'reference_receipts':receipts,
    'theme_receipts':theme_receipts,'news_schema_present':news_schema_present,
    'research_schema_present':research_schema_present,'news_observation':news_observation,
    'research_runs':research_runs},default=str))
'''


def collect(state):
    raw = subprocess.check_output(
        paper_stack.compose(state, 'exec', '-T', 'api', 'python', '-'),
        input=COLLECTOR, text=True, stderr=subprocess.PIPE, timeout=60,
    )
    payload = json.loads(raw)
    raw = subprocess.check_output(
        paper_stack.compose(state, 'ps', '--format', 'json'),
        text=True, stderr=subprocess.PIPE, timeout=15,
    )
    # Compose supports both JSON arrays and newline-delimited objects.
    rows = json.loads(raw) if raw.lstrip().startswith('[') else [json.loads(line) for line in raw.splitlines() if line.strip()]
    payload['containers'] = [{key: row.get(key) for key in ('Service', 'State', 'Health')} for row in rows]
    project = json.loads((state / 'deployment.json').read_text())['project']
    paper_stack.check_project(project)
    for key, prefix in (('reference_sync_job', 'quantdinger-securities-'),
                        ('theme_sync_job', 'quantdinger-themes-'),
                        ('research_news_job', 'quantdinger-research-news-'),
                        ('research_work_job', 'quantdinger-research-work-'),
                        ('research_request_job', 'quantdinger-research-request-')):
        try:
            unit = subprocess.run(
                ['systemctl', '--user', 'show', prefix + project + '.service',
                 '--property=LoadState,ActiveState,Result'], capture_output=True, text=True, timeout=5, check=False,
            )
            fields = dict(line.split('=', 1) for line in unit.stdout.splitlines() if '=' in line)
            if unit.returncode == 0 and fields.get('LoadState') == 'loaded':
                payload[key] = fields
            elif unit.returncode != 0 and fields.get('LoadState') != 'not-found':
                payload[key] = {'LoadState': 'monitor_unavailable'}
        except (OSError, subprocess.SubprocessError):
            payload[key] = {'LoadState': 'monitor_unavailable'}
    if payload.get('theme_sync_job', {}).get('LoadState') == 'loaded':
        payload['theme_sync_status'] = theme_sync_status(state)
    if any(payload.get('research_' + job + '_job', {}).get('LoadState') == 'loaded' for job in ('news', 'work', 'request')):
        payload['research_job_status'] = research_job_status(state)
    payload['jobs_observed_at'] = datetime.now(timezone.utc).isoformat()
    return payload


def theme_sync_status(state):
    """Expose only validated operational fields, never arbitrary state contents."""
    try:
        from theme_scheduler import load_journal
        # Use the runner's marker/project/attempt validation as well as the
        # public summary schema; a missing marker must not look completed.
        value = load_journal(state)
        allowed = {'completed', 'partial', 'failed', 'skipped_before_window', 'running', 'initialized'}
        if (value.get('schema_version') != 1 or value.get('mode') != 'paper'
                or value.get('config_version') != 'eastmoney-configured-themes-20261118-v1'
                or value.get('status') not in allowed):
            raise ValueError('invalid theme job schema')
        result = {'status': value['status'], 'config_version': value['config_version']}
        for key in ('started_at', 'completed_at'):
            if value.get(key) is not None:
                result[key] = timestamp(value[key]).isoformat()
        if value.get('window_date') is not None:
            result['window_date'] = date.fromisoformat(value['window_date']).isoformat()
        return result
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return {'status': 'invalid_or_unavailable'}



def research_job_status(state):
    """Read validated job summaries only; never expose request or model payloads."""
    path = state / 'research-job-status.json'
    if not path.exists() and not path.is_symlink():
        return {'status': 'not_observed', 'jobs': {}}
    try:
        from research_jobs import load_job_status
        value = load_job_status(state)
        jobs = {}
        for name in ('news', 'work', 'request'):
            item = value.get('jobs', {}).get(name)
            if item is not None:
                jobs[name] = {key: item[key] for key in ('job', 'status', 'started_at', 'updated_at',
                    'execution_allowed', 'id', 'receipt_id', 'item_count', 'source_hash', 'error') if key in item}
        return {'status': 'observed', 'updated_at': value['updated_at'], 'jobs': jobs}
    except (ImportError, OSError, ValueError, TypeError, AttributeError, KeyError):
        return {'status': 'invalid_or_unavailable', 'jobs': {}}


def assess_research(payload, now, jobs_now, issues, pending):
    """Additional evidence stays optional for deployments predating these jobs."""
    summary = payload.get('research_job_status', {})
    for name in ('news', 'work', 'request'):
        prefix = 'research_' + name + '_job'
        unit = payload.get(prefix, {})
        if unit.get('Result') not in (None, '', 'success'):
            issues.append(prefix + '_failed')
        if unit.get('LoadState') == 'monitor_unavailable':
            pending.append(prefix + '_health_unavailable')
        if unit.get('LoadState') != 'loaded':
            continue
        if summary.get('status') == 'invalid_or_unavailable':
            issues.append('research_job_status_invalid_or_unavailable')
            continue
        item = summary.get('jobs', {}).get(name)
        if not item:
            pending.append(prefix + '_not_observed')
            continue
        status = item.get('status')
        if item.get('execution_allowed') is not False:
            issues.append(prefix + '_safety_changed')
        if status in ('failed', 'unknown'):
            issues.append(prefix + '_state_' + status)
        elif (name, status) == ('work', 'waiting_for_excerpt'):
            pending.append(prefix + '_waiting_for_excerpt')
        elif status in ('needs_model_configuration', 'no_evidence', 'skipped_before_window', 'skipped_active', 'skipped_daily_limit', 'queued', 'started'):
            # The request unit's queued acknowledgement never tracks the run lifecycle.
            if (name, status) != ('request', 'queued'):
                pending.append(prefix + '_' + status)
        elif status not in ('ingested', 'idle', 'completed'):
            issues.append(prefix + '_state_invalid')
        try:
            age = (jobs_now - timestamp(item['updated_at'])).total_seconds()
            started_age = (jobs_now - timestamp(item['started_at'])).total_seconds()
            if age < 0 or started_age < age:
                issues.append(prefix + '_time_invalid_or_future')
            else:
                local = jobs_now.astimezone(ZoneInfo('Asia/Shanghai'))
                # News intentionally sleeps overnight; allow its first morning run.
                limit = 90 * 60 if (7, 30) <= (local.hour, local.minute) <= (22, 59) else 12 * 3600
                if name != 'news':
                    limit = 10 * 60 if name == 'work' else 30 * 3600
                if age > limit:
                    issues.append(prefix + '_status_stale')
                if status == 'started' and started_age > 300:
                    issues.append(prefix + '_running_stale')
        except (KeyError, ValueError, TypeError):
            issues.append(prefix + '_time_invalid_or_future')
    news = payload.get('news_observation', {})
    if payload.get('news_schema_present'):
        if news.get('status') in ('not_observed', 'module_unavailable'):
            pending.append('news_evidence_' + news['status'])
        elif news.get('status') != 'verified':
            issues.append('news_evidence_invalid_or_unavailable')
        else:
            try:
                if timestamp(news['received_at']) > now:
                    issues.append('news_evidence_received_at_in_future')
            except (KeyError, ValueError, TypeError):
                issues.append('news_evidence_received_at_invalid')
    if payload.get('research_schema_present') and not payload.get('research_runs'):
        pending.append('research_not_yet_observed')
    for run in payload.get('research_runs', []):
        ref = run['account_ref']
        if run.get('execution_allowed') is not False or run.get('approval_status') != 'not_submitted':
            issues.append('research_safety_changed:' + ref)
        status = run.get('status')
        if status == 'failed':
            issues.append('research_latest_run_failed:' + ref)
        elif status in ('needs_model_configuration', 'no_evidence', 'queued', 'started'):
            pending.append('research_' + status + ':' + ref)
        elif status != 'completed':
            issues.append('research_latest_run_state_invalid:' + ref)
        try:
            created = timestamp(run['created_at'])
            times = [timestamp(run[key]) for key in ('started_at', 'completed_at') if run.get(key)]
            if created > now or any(value < created or value > now for value in times):
                issues.append('research_latest_run_time_invalid:' + ref)
            if status in ('queued', 'started') and (now - created).total_seconds() > 10 * 60:
                issues.append('research_latest_run_stale:' + ref)
        except (KeyError, ValueError, TypeError):
            issues.append('research_latest_run_time_invalid:' + ref)
    if any(payload.get('research_' + name + '_job', {}).get('LoadState') == 'loaded' for name in ('news', 'work', 'request')) or payload.get('research_schema_present'):
        pending.append('research_results_not_approved_for_strategy_execution')


def backup_free_bytes(root):
    path = root.expanduser().resolve()
    while not path.exists():
        path = path.parent
    return shutil.disk_usage(path).free


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError('timezone required')
    return parsed


def backup_status(root, now):
    candidates = []
    if root.exists():
        for path in root.glob('*/manifest.json'):
            try:
                manifest = json.loads(path.read_text())
                candidates.append((timestamp(manifest['created_at']), path.parent))
            except (OSError, ValueError, KeyError, TypeError):
                continue
    if not candidates:
        return {'status': 'missing', 'verified': False}
    created, source = max(candidates)
    try:
        paper_stack.verify_backup(source)
    except (OSError, ValueError, KeyError, tarfile.TarError):
        return {'status': 'invalid', 'verified': False, 'created_at': created.isoformat()}
    age = (now - created).total_seconds()
    return {'status': 'current' if 0 <= age <= 30 * 3600 else 'stale_or_future',
            'verified': True, 'created_at': created.isoformat(), 'age_seconds': age}


def assess(payload, backup, *, target_days=5):
    now = timestamp(payload['observed_at'])
    jobs_now = timestamp(payload.get('jobs_observed_at', payload['observed_at']))
    issues, pending = [], []
    containers = {row['Service']: row for row in payload['containers']}
    for service in ('api', 'worker', 'db', 'web'):
        row = containers.get(service, {})
        if row.get('State') != 'running' or row.get('Health') != 'healthy':
            issues.append('service_not_healthy:' + service)
    heartbeat = next((row for row in payload['heartbeats'] if row['process_name'] == 'managed_paper_worker'), None)
    try:
        age = (now - timestamp(heartbeat['observed_at'])).total_seconds()
        if not 0 <= age <= 120 or heartbeat['details'].get('status') not in ('running', 'starting'):
            issues.append('worker_heartbeat_stale_or_stopped')
    except (ValueError, TypeError, KeyError):
        issues.append('worker_heartbeat_missing_or_invalid')
    if not payload['safety_flags_disabled'] or not all(payload['safety_flags_disabled'].values()):
        issues.append('paper_safety_environment_changed')
    continuous = payload['session'].get('can_match') is True
    for account in payload['accounts']:
        ref = account['account_ref']
        if not account['ledger_reconciled']:
            issues.append('ledger_reconciliation_failed:' + ref)
        for quote in account['quotes']:
            if quote.get('raw_evidence_status') != 'verified':
                issues.append('quote_raw_evidence_unverified:' + ref + ':' + quote.get('symbol', 'unknown'))
        if account['state'] in ('failed', 'reconciliation_failed', 'storage_low', 'calendar_unknown'):
            issues.append('worker_attention:' + ref + ':' + account['state'])
        if continuous:
            if account['state'] == 'awaiting_review':
                pending.append('human_daily_review:' + ref)
            if not account['quotes']:
                issues.append('continuous_session_no_quotes:' + ref)
            for quote in account['quotes']:
                try:
                    source_age = (now - timestamp(quote['exchange_time'])).total_seconds()
                    received_age = (now - timestamp(quote['received_at'])).total_seconds()
                    if not 0 <= source_age <= 15 or not 0 <= received_age <= 15:
                        issues.append('continuous_session_quote_stale:' + ref + ':' + quote['symbol'])
                except (ValueError, KeyError, TypeError):
                    issues.append('continuous_session_quote_time_invalid:' + ref)
                if not quote.get('evidence_sha256'):
                    issues.append('quote_evidence_missing:' + ref)
    if backup['status'] != 'current':
        issues.append('backup_' + backup['status'])
    job = payload.get('reference_sync_job', {})
    if job.get('Result') not in (None, '', 'success'):
        issues.append('reference_sync_job_failed')
    if job.get('LoadState') == 'monitor_unavailable':
        pending.append('reference_sync_job_health_unavailable')
    theme_job = payload.get('theme_sync_job', {})
    if theme_job.get('Result') not in (None, '', 'success'):
        issues.append('theme_sync_job_failed')
    if theme_job.get('LoadState') == 'monitor_unavailable':
        pending.append('theme_sync_job_health_unavailable')
    theme_status = payload.get('theme_sync_status', {})
    if theme_job.get('LoadState') == 'loaded':
        status = theme_status.get('status', 'not_observed')
        if status in ('not_observed', 'initialized', 'skipped_before_window'):
            pending.append('theme_sync_first_window_not_completed')
        elif status in ('partial', 'failed', 'invalid_or_unavailable'):
            issues.append('theme_sync_job_state_' + status)
        for key in ('started_at', 'completed_at'):
            if theme_status.get(key):
                try:
                    if timestamp(theme_status[key]) > jobs_now:
                        issues.append('theme_sync_job_time_in_future')
                except (TypeError, ValueError):
                    issues.append('theme_sync_job_time_invalid')
        if status == 'completed':
            try:
                if (jobs_now - timestamp(theme_status['completed_at'])).total_seconds() > 30 * 3600:
                    issues.append('theme_sync_job_completion_stale')
            except (KeyError, TypeError, ValueError):
                issues.append('theme_sync_job_time_invalid')
        if status == 'running':
            try:
                if (jobs_now - timestamp(theme_status['started_at'])).total_seconds() > 300:
                    issues.append('theme_sync_job_running_stale')
            except (KeyError, TypeError, ValueError):
                issues.append('theme_sync_job_time_invalid')
    assess_research(payload, now, jobs_now, issues, pending)
    if payload.get('data_free_bytes', 0) < 100 * 1024 * 1024:
        issues.append('data_volume_capacity_low')
    if backup.get('free_bytes', 0) < 500 * 1024 * 1024:
        issues.append('backup_volume_capacity_low')
    receipts = payload.get('reference_receipts', [])
    if not receipts:
        pending.append('historical_reference_not_ingested')
    for receipt in receipts:
        if receipt.get('raw_evidence_status') != 'verified':
            issues.append('security_reference_raw_evidence_unverified')
        try:
            if timestamp(receipt['received_at']) > now:
                issues.append('security_reference_received_at_in_future')
        except (ValueError, TypeError, KeyError):
            issues.append('security_reference_received_at_invalid')
    theme_receipts = payload.get('theme_receipts', [])
    for receipt in theme_receipts:
        if receipt.get('evidence_status') != 'verified':
            issues.append('theme_reference_evidence_unverified')
        try:
            if timestamp(receipt['received_at']) > now:
                issues.append('theme_reference_received_at_in_future')
        except (ValueError, TypeError, KeyError):
            issues.append('theme_reference_received_at_invalid')
    # Count dates carrying real-clock samples; never count controlled replay.
    days = sorted({row['trading_day'] for row in payload['observations']
                   if row['wall_clock_polls'] > 0 and row.get('is_trading_day') is True})
    failed_reports = [row for row in payload['daily_reports'] if row['reconciliation'].get('ok') is not True]
    if failed_reports:
        issues.append('daily_report_reconciliation_failed')
    if len(days) < target_days:
        pending.append('natural_sample_days_below_target')
    pending.extend(['intraday_gap_and_full_session_coverage_not_proven', 'remaining_backlog_requires_separate_acceptance'])
    return {'schema_version': 1, 'mode': 'paper', 'observed_at': payload['observed_at'],
            'jobs_observed_at': jobs_now.isoformat(),
            'status': 'attention' if issues else 'operational', 'issues': sorted(set(issues)),
            'pending_acceptance': sorted(set(pending)), 'session': payload['session'],
            'natural_sample_days': days, 'target_natural_sample_days': target_days,
            'natural_multiday_complete': False,
            'coverage': 'sampled_not_continuous_uptime_proof',
            'completed_backlog': False, 'backup': backup, 'reference_sync_job': job,
            'theme_sync_job': theme_job,
            'theme_sync_status': theme_status,
            'research_jobs': {name: payload.get('research_' + name + '_job', {}) for name in ('news', 'work', 'request')},
            'research_job_status': payload.get('research_job_status', {}),
            'news_schema_present': payload.get('news_schema_present', False),
            'research_schema_present': payload.get('research_schema_present', False),
            'news_observation': payload.get('news_observation', {}),
            'research_runs': payload.get('research_runs', []),
            'daily_report_count': len(payload['daily_reports']),
            'data_free_bytes': payload.get('data_free_bytes'),
            'reference_schema_present': payload.get('reference_schema_present', False),
            'reference_receipts': receipts,
            'theme_receipts': theme_receipts,
            'accounts': payload['accounts'], 'observations': payload['observations'],
            'containers': payload['containers'], 'safety_flags_disabled': payload['safety_flags_disabled']}


def significant_fingerprint(report):
    stable = {key: report[key] for key in ('status', 'issues', 'pending_acceptance',
              'natural_sample_days', 'daily_report_count', 'safety_flags_disabled')}
    stable['backlog'] = report.get('backlog')
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()


def attach_backlog(report, path):
    """Expose explicitly maintained work states; never infer completion from uptime."""
    value = json.loads(path.read_text())
    items = value.get('items') if isinstance(value, dict) else None
    if not isinstance(items, list) or any(not isinstance(item, dict) or
            not isinstance(item.get('id'), str) or not isinstance(item.get('status'), str)
            for item in items):
        raise ValueError('invalid backlog schema')
    report['backlog'] = [{'id': item['id'], 'status': item['status'],
                          'acceptance': item.get('acceptance')} for item in items]
    report['pending_backlog_ids'] = [item['id'] for item in items
                                    if item['status'] not in ('complete', 'accepted', 'prohibited')]
    # An item labelled complete is only an explicit human-maintained assertion,
    # never independent evidence that all future workflow goals are delivered.
    report['completed_backlog'] = False


def private_report(path, value):
    path = path.expanduser()
    if path.is_symlink():
        raise ValueError('Refusing symlink output')
    path = path.resolve()
    if path == paper_stack.ROOT or paper_stack.ROOT in path.parents:
        raise ValueError('Operational reports must stay outside the Git checkout')
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, 'w') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--backup-root', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--quiet-unchanged', type=Path, metavar='BASELINE_JSON')
    parser.add_argument('--backlog', type=Path, help='Explicitly maintained JSON items with id/status/acceptance')
    parser.add_argument('--target-days', type=int, default=5, choices=range(2, 91))
    args = parser.parse_args()
    try:
        payload = collect(args.state_dir.expanduser().resolve())
        backup = backup_status(args.backup_root, timestamp(payload['observed_at']))
        backup['free_bytes'] = backup_free_bytes(args.backup_root)
        report = assess(payload, backup, target_days=args.target_days)
        if args.backlog:
            attach_backlog(report, args.backlog)
        if args.output:
            private_report(args.output, report)
        fingerprint = significant_fingerprint(report)
        old = None
        if args.quiet_unchanged:
            try:
                old = json.loads(args.quiet_unchanged.read_text()).get('fingerprint')
            except (OSError, ValueError, TypeError):
                pass
            private_report(args.quiet_unchanged, {'fingerprint': fingerprint, 'observed_at': report['observed_at']})
        if old != fingerprint:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report['issues'] else 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        # Docker stderr / DSNs / raw rows are intentionally excluded.
        print(json.dumps({'mode': 'paper', 'status': 'monitor_failed', 'error_type': type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
