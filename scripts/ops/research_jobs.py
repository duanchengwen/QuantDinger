#!/usr/bin/env python3
"""Bounded news/research jobs and reviewable user-systemd unit templates.

No installer, account discovery, strategy approval, or broker operation. Requests
require an explicit account. Unknown started model calls stay terminal in the DB.
"""
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import paper_stack as ops
from theme_scheduler import atomic_state, cleanup_container

TIMEOUTS = {'news': 90, 'work': 150, 'request': 30}
LOCK_WAIT_SECONDS = 15
MAX_OUTPUT_BYTES = 16384
MAX_EXCERPT_BYTES = 1024 * 1024
MAX_EXCERPT_WIRE_BYTES = 4 * ((MAX_EXCERPT_BYTES + 2) // 3)
MAX_PROPOSAL_BYTES = 16_384
MAX_PROPOSAL_WIRE_BYTES = 4 * ((MAX_PROPOSAL_BYTES + 2) // 3)
PROPOSAL_TENANT = r'user_[1-9][0-9]{0,18}'
EXCERPT_DERIVATION_SECONDS = 120
CLEANUP_MARKER = '.research-job-cleanup-unknown.json'
STATUS_NAME = 'research-job-status.json'
SAFE_RESEARCH_ERRORS = frozenset({
    'invalid_idempotency_key', 'paper_account_required', 'invalid_research_preferences',
    'research_already_active', 'research_daily_limit', 'invalid_research_account', 'invalid_research_limit',
    'model_not_configured', 'model_not_supported', 'model_endpoint_not_allowed', 'model_configuration_changed',
    'no_supported_topics', 'no_verified_theme_members', 'no_news_evidence', 'model_output_invalid',
    'model_request_failed', 'model_timeout', 'model_response_too_large', 'model_response_invalid',
    'research_input_too_large', 'research_lease_lost', 'evidence_unavailable', 'invalid_evidence_time',
    'research_failed', 'outcome_unknown', 'database_not_configured',
    'invalid_news_excerpt_evidence', 'news_excerpt_source_mismatch',
})
SAFE_HOST_ERRORS = frozenset({'research_job_failed', 'research_job_unknown', 'research_job_timeout',
                             'research_job_cleanup_unknown', 'prior_container_cleanup_requires_review'})
STATUSES = {'ingested', 'idle', 'queued', 'started', 'completed', 'failed', 'no_evidence',
            'needs_model_configuration', 'skipped_before_window', 'skipped_active', 'skipped_daily_limit'}
PROPOSAL_DRAFT_STATUSES = {'draft_created', 'already_proposed'}
PROPOSAL_SKIP_STATUSES = {'inactive_template', 'invalid_template', 'proposal_failed', 'no_verified_trading_day',
                          'no_completed_scheduled_run', 'no_eligible_template_match'}
NOTICE_STATUSES = {'unconfigured', 'not_claimed', 'unknown', 'webhook_acknowledged', 'disabled', 'rejected', 'failed'}

CONTAINER_SCRIPT = (
    'SAFE_RESEARCH_ERRORS = frozenset(' + repr(sorted(SAFE_RESEARCH_ERRORS)) + ')\n'
    f'MAX_EXCERPT_WIRE_BYTES = {MAX_EXCERPT_WIRE_BYTES}\n'
    f'MAX_PROPOSAL_WIRE_BYTES = {MAX_PROPOSAL_WIRE_BYTES}\n'
    f'PROPOSAL_TENANT = {PROPOSAL_TENANT!r}\n'
) + r'''
import base64
import json
import re
import sys
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo
import psycopg2
from app.ashare.schema import get_database_url

payload = json.loads(sys.stdin.read(MAX_EXCERPT_WIRE_BYTES + MAX_PROPOSAL_WIRE_BYTES + 4096))
job = payload.get('job')
result = {'job': job, 'status': 'failed', 'error': 'research_job_failed', 'execution_allowed': False}
safe_exception = lambda error: 'research_job_failed'
def attach_run_error(result, run):
    if run.get('error_code') is not None:
        code = run['error_code']
        result['error'] = code if isinstance(code, str) and code in SAFE_RESEARCH_ERRORS else 'research_job_failed'
try:
    if job not in ('news', 'work', 'request'):
        raise ValueError()
    fields = {'job', 'tenant_id', 'account_id', 'idempotency_key', 'daily'} if job == 'request' else {'job'}
    if job == 'work' and 'excerpt' in payload:
        fields |= {'excerpt', 'excerpt_sha256'}
    if job == 'work' and 'proposal_template' in payload:
        fields.add('proposal_template')
    scope = None
    if job == 'work' and 'proposal_scope' in payload:
        fields.add('proposal_scope')
        values = payload['proposal_scope']
        if (not isinstance(values, list) or len(values) != 2 or not isinstance(values[0], str)
                or not re.fullmatch(PROPOSAL_TENANT, values[0]) or int(values[0][5:]) > 2**63 - 1
                or values[1] != 'paper'):
            raise ValueError()
        scope = tuple(values)
    if set(payload) != fields:
        raise ValueError()
    if job == 'request':
        if (any(not isinstance(payload[key], str) or not re.fullmatch(r'[A-Za-z0-9_:-]{1,200}', payload[key]) for key in ('tenant_id', 'account_id'))
                or type(payload['daily']) is not bool
                or (not payload['daily'] and (not isinstance(payload['idempotency_key'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', payload['idempotency_key'])))
                or (payload['daily'] and payload['idempotency_key'] is not None)):
            raise ValueError()
        local = datetime.now(ZoneInfo('Asia/Shanghai'))
        if payload['daily'] and (local.hour, local.minute) < (8,30):
            print(json.dumps({'job': job, 'status': 'skipped_before_window', 'execution_allowed': False}))
            raise SystemExit(0)
    with closing(psycopg2.connect(get_database_url(), connect_timeout=5,
            options='-c statement_timeout=5000 -c lock_timeout=2000')) as connection:
        if job == 'news':
            from app.ashare.news import ingest_news, NewsError, safe_error_code
            safe_exception = lambda error: safe_error_code(error) if isinstance(error, NewsError) else 'research_job_failed'
            receipt = ingest_news(connection, '/app/data/evidence/news', topics=('ai','robotics','new_energy'), timeout=45)
            result = {'job': job, 'status': 'ingested', 'receipt_id': receipt['receipt_id'],
                      'item_count': receipt['item_count'], 'source_hash': receipt['source_hash'], 'execution_allowed': False}
        elif job == 'work':
            from app.ashare.research import run_once, ResearchError
            safe_exception = lambda error: str(error) if isinstance(error, ResearchError) and str(error) in SAFE_RESEARCH_ERRORS else 'research_job_failed'
            if 'excerpt' in payload:
                from app.ashare.research_excerpts import verify_excerpt_artifact
                try:
                    encoded = payload['excerpt']
                    if not isinstance(encoded, str) or len(encoded) > MAX_EXCERPT_WIRE_BYTES:
                        raise ValueError()
                    receipt = verify_excerpt_artifact(base64.b64decode(encoded, validate=True), payload['excerpt_sha256'])
                except (ValueError, TypeError):
                    raise ResearchError('invalid_news_excerpt_evidence') from None
                run = run_once(connection, excerpt_receipt=receipt)
            else:
                run = run_once(connection)
            result = {'job': job, 'status': run['status'] if run else 'idle', 'execution_allowed': False}
            if run:
                result['id'] = run['id']
                attach_run_error(result, run)
            if (scope is not None or 'proposal_template' in payload) and result['status'] in ('completed', 'idle'):
                from app.ashare.strategy_proposals import propose_and_notify
                try:
                    template = None
                    if 'proposal_template' in payload:
                        encoded = payload['proposal_template']
                        if not isinstance(encoded, str) or len(encoded) > MAX_PROPOSAL_WIRE_BYTES:
                            raise ValueError()
                        template = base64.b64decode(encoded, validate=True)
                except (ValueError, TypeError):
                    result['proposal'] = {'status': 'invalid_template'}
                else:
                    result['proposal'] = propose_and_notify(connection, template, scope=scope)
        else:
            from app.ashare.research import ResearchStore, ResearchError
            safe_exception = lambda error: str(error) if isinstance(error, ResearchError) and str(error) in SAFE_RESEARCH_ERRORS else 'research_job_failed'
            try:
                research = ResearchStore(connection, payload['tenant_id'], payload['account_id'])
                run = (research.request_scheduled_run(local.date()) if payload['daily']
                       else research.request_run(payload['idempotency_key']))
                result = {'job': job, 'status': run['status'], 'id': run['id'], 'execution_allowed': False}
                attach_run_error(result, run)
            except ResearchError as exc:
                if str(exc) not in ('research_already_active', 'research_daily_limit'):
                    raise
                result = {'job': job, 'status': 'skipped_active' if str(exc) == 'research_already_active' else 'skipped_daily_limit',
                          'execution_allowed': False, 'error': str(exc)}
except Exception as exc:
    result = {'job': job, 'status': 'failed', 'error': safe_exception(exc), 'execution_allowed': False}
print(json.dumps(result))
raise SystemExit(1 if result['status'] == 'failed' else 0)
'''


def _news_errors():
    """Read the backend's static allowlist without importing DB/HTTP dependencies."""
    source = ops.ROOT / 'backend_api_python/app/ashare/news.py'
    for node in ast.parse(source.read_text(encoding='utf-8')).body:
        if (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'SAFE_NEWS_ERRORS' for target in node.targets)
                and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                and node.value.func.id == 'frozenset' and len(node.value.args) == 1):
            codes = ast.literal_eval(node.value.args[0])
            if isinstance(codes, set) and all(isinstance(code, str) and re.fullmatch('[a-z_]{1,100}', code) for code in codes):
                return frozenset(codes)
    raise ValueError('news_error_contract_unavailable')


def safe_job_error(job, code):
    allowed = (_news_errors() if job == 'news' else SAFE_RESEARCH_ERRORS) | SAFE_HOST_ERRORS
    return code if isinstance(code, str) and code in allowed else 'research_job_failed'


def load_job_status(state):
    """Read only validated safe summaries; never query sources, models or accounts."""
    path = state / STATUS_NAME
    if not path.exists() and not path.is_symlink():
        return {'schema_version': 1, 'updated_at': None, 'jobs': {}}
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
        raise ValueError('research_job_status_invalid')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if set(data) != {'schema_version', 'updated_at', 'jobs'} or data['schema_version'] != 1 or not isinstance(data['jobs'], dict):
            raise ValueError()
        stamps = [data['updated_at']]
        for job, item in data['jobs'].items():
            allowed = {'job','status','started_at','updated_at','execution_allowed','id','receipt_id','item_count','source_hash','error'}
            if job == 'work':
                allowed.add('proposal')
            valid_statuses = STATUSES | ({'unknown', 'waiting_for_excerpt'} if job == 'work' else {'unknown'})
            if (job not in TIMEOUTS or not isinstance(item, dict) or set(item) - allowed
                    or item.get('job') != job or item.get('status') not in valid_statuses
                    or item.get('execution_allowed') is not False):
                raise ValueError()
            stamps += [item['started_at'], item['updated_at']]
            if 'error' in item and safe_job_error(job, item['error']) != item['error']:
                raise ValueError()
            for field, length in [('id',32),('receipt_id',32),('source_hash',64)]:
                if field in item and (not isinstance(item[field],str) or not re.fullmatch('[a-f0-9]{'+str(length)+'}',item[field])):
                    raise ValueError()
            if 'item_count' in item and (type(item['item_count']) is not int or not 0 <= item['item_count'] <= 15):
                raise ValueError()
            if 'proposal' in item:
                _proposal_summary(item['proposal'], item['status'])
        if any(not isinstance(stamp,str) or datetime.fromisoformat(stamp).utcoffset() is None for stamp in stamps):
            raise ValueError()
        return data
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError('research_job_status_invalid') from None


def _record_job_status(state, outcome, started_at):
    data = load_job_status(state)
    stamp = datetime.now(UTC).isoformat()
    data['updated_at'] = stamp
    data['jobs'][outcome['job']] = {**outcome, 'started_at': started_at, 'updated_at': stamp}
    atomic_state(state / STATUS_NAME, data)


def _request(tenant_id, account_id, idempotency_key, daily):
    if (any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_:-]{1,200}', value) for value in (tenant_id, account_id))
            or type(daily) is not bool or (daily and idempotency_key is not None)
            or (not daily and (not isinstance(idempotency_key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', idempotency_key)))):
        raise ValueError('invalid_explicit_research_account_or_key')
    return {'job': 'request', 'tenant_id': tenant_id, 'account_id': account_id,
            'idempotency_key': idempotency_key, 'daily': daily}


def _proposal_scope(tenant_id, account_id):
    if (not isinstance(tenant_id, str) or not re.fullmatch(PROPOSAL_TENANT, tenant_id)
            or int(tenant_id[5:]) > 2**63 - 1 or account_id != 'paper'):
        return None
    return [tenant_id, account_id]


def _proposal_summary(proposal, research_status):
    if (research_status not in ('completed', 'idle') or not isinstance(proposal, dict)
            or not isinstance(proposal.get('status'), str)):
        raise ValueError('invalid_proposal_status')
    if proposal['status'] in PROPOSAL_SKIP_STATUSES and set(proposal) == {'status'}:
        return proposal
    if (proposal['status'] not in PROPOSAL_DRAFT_STATUSES
            or set(proposal) != {'status', 'version_id', 'notice'}
            or not isinstance(proposal['version_id'], str) or not re.fullmatch('[a-f0-9]{32}', proposal['version_id'])
            or not isinstance(proposal['notice'], str) or proposal['notice'] not in NOTICE_STATUSES):
        raise ValueError('invalid_proposal_status')
    return proposal


def _safe_result(result, job):
    if len(result.stdout.encode('utf-8')) > MAX_OUTPUT_BYTES:
        raise ValueError('job_response_invalid')
    value = json.loads(result.stdout)
    if (not isinstance(value, dict) or value.get('job') != job or value.get('status') not in STATUSES
            or value.get('execution_allowed') is not False
            or (result.returncode == 0) != (value['status'] != 'failed')):
        raise ValueError('job_response_invalid')
    safe = {key: value[key] for key in ('job', 'status', 'execution_allowed')}
    if 'id' in value:
        if not isinstance(value['id'], str) or not re.fullmatch(r'[a-f0-9]{32}', value['id']):
            raise ValueError('job_response_invalid')
        safe['id'] = value['id']
    if value['status'] == 'ingested':
        if (job != 'news' or not isinstance(value.get('receipt_id'), str)
                or not re.fullmatch(r'[a-f0-9]{32}', value['receipt_id'])
                or not isinstance(value.get('source_hash'), str) or not re.fullmatch(r'[a-f0-9]{64}', value['source_hash'])
                or type(value.get('item_count')) is not int or not 0 <= value['item_count'] <= 15):
            raise ValueError('job_response_invalid')
        safe.update({key: value[key] for key in ('receipt_id', 'item_count', 'source_hash')})
    if value.get('error') is not None or value['status'] == 'failed':
        safe['error'] = safe_job_error(job, value.get('error'))
    if 'proposal' in value:
        if job != 'work':
            raise ValueError('invalid_proposal_status')
        safe['proposal'] = _proposal_summary(value['proposal'], value['status'])
    return safe


def _read_proposal_template(path):
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('invalid_proposal_template')
        raw = stream.read(MAX_PROPOSAL_BYTES + 1)
    if not 0 < len(raw) <= MAX_PROPOSAL_BYTES:
        raise ValueError('invalid_proposal_template')
    return base64.b64encode(raw).decode('ascii')


def _work_excerpt_payload(path, digest):
    """Pass verified-size binary evidence through stdin without logs or mounts."""
    if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest) or not path.is_file():
        raise ValueError('invalid_news_excerpt_evidence')
    with path.open('rb') as stream:
        raw = stream.read(MAX_EXCERPT_BYTES + 1)
    if not 0 < len(raw) <= MAX_EXCERPT_BYTES or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('invalid_news_excerpt_evidence')
    return {'excerpt': base64.b64encode(raw).decode('ascii'), 'excerpt_sha256': digest}


def _latest_excerpt_payload(state, directory):
    """Return None during the bounded derivation window if proof is still pending."""
    job = load_job_status(state)['jobs'].get('news', {})
    if job.get('status') != 'ingested':
        return {}
    age = (datetime.now(UTC) - datetime.fromisoformat(job['updated_at'])).total_seconds()
    if age < 0:
        raise ValueError('invalid_news_excerpt_evidence')
    pending = None if age < EXCERPT_DERIVATION_SECONDS else {}
    receipt_id = job['receipt_id']
    path = directory / (receipt_id + '.json')
    if path.is_symlink():
        raise ValueError('invalid_news_excerpt_evidence')
    if not path.exists():
        return pending
    metadata = json.loads((state / 'deployment.json').read_bytes())
    project = metadata['project']
    ops.check_project(project)
    unit = f'quantdinger-research-news-pdf-{project}.service'
    try:
        journal = subprocess.run(['journalctl', '--user', '-u', unit, '-n', '40', '--no-pager', '-o', 'cat'],
                                 capture_output=True, text=True, timeout=8, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return pending
    if journal.returncode or len(journal.stdout.encode('utf-8')) > MAX_OUTPUT_BYTES:
        return pending
    digests = set()
    for line in journal.stdout.splitlines():
        if not line.startswith('{'):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if (isinstance(event, dict) and event.get('status') == 'extracted'
                and event.get('receipt_id') == receipt_id
                and event.get('pdfs') == job['item_count'] and event.get('execution_allowed') is False):
            digest = event.get('artifact_sha256')
            if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
                raise ValueError('invalid_news_excerpt_evidence')
            digests.add(digest)
    if not digests:
        return pending
    if len(digests) != 1:
        raise ValueError('invalid_news_excerpt_evidence')
    return _work_excerpt_payload(path, digests.pop())


def run_job(state, job, *, tenant_id=None, account_id='paper', idempotency_key=None, daily=False,
            excerpt=None, excerpt_sha256=None, excerpt_dir=None, proposal_template=None):
    if job not in TIMEOUTS or (proposal_template is not None and job != 'work'):
        raise ValueError('invalid_job')
    payload = _request(tenant_id, account_id, idempotency_key, daily) if job == 'request' else {'job': job}
    if job == 'work' and (tenant_id is not None or account_id != 'paper'):
        scope = _proposal_scope(tenant_id, account_id)
        if scope is None:
            raise ValueError('invalid_proposal_scope')
        payload['proposal_scope'] = scope
    if excerpt_dir is not None:
        if job != 'work' or excerpt is not None or excerpt_sha256 is not None:
            raise ValueError('invalid_news_excerpt_evidence')
    elif excerpt is not None or excerpt_sha256 is not None:
        if job != 'work' or excerpt is None:
            raise ValueError('invalid_news_excerpt_evidence')
        payload.update(_work_excerpt_payload(excerpt, excerpt_sha256))
    with ops.operation_lock(state, wait_seconds=LOCK_WAIT_SECONDS):
        started_at = datetime.now(UTC).isoformat()
        marker = state / CLEANUP_MARKER
        if marker.exists() or marker.is_symlink():
            _record_job_status(state, {'job':job, 'status':'failed', 'error':'prior_container_cleanup_requires_review', 'execution_allowed':False}, started_at)
            raise ValueError('prior_container_cleanup_requires_review')
        if excerpt_dir is not None:
            evidence = _latest_excerpt_payload(state, excerpt_dir)
            if evidence is None:
                outcome = {'job': job, 'status': 'waiting_for_excerpt', 'execution_allowed': False}
                _record_job_status(state, outcome, started_at)
                print(json.dumps(outcome))
                return 0
            payload.update(evidence)
        invalid_proposal_template = False
        if proposal_template is not None:
            try:
                payload['proposal_template'] = _read_proposal_template(proposal_template)
            except (ValueError, OSError):
                invalid_proposal_template = True
                payload.pop('proposal_scope', None)
        metadata = json.loads((state / 'deployment.json').read_text())
        ops.check_project(metadata['project'])
        container = 'quantdinger-research-' + job + '-' + uuid.uuid4().hex
        command = ops.compose(state, 'run', '--rm', '--no-deps', '-T', '--name', container,
                              'worker', 'python', '-c', CONTAINER_SCRIPT)
        outcome = {'job': job, 'status': 'unknown', 'error': 'research_job_unknown', 'execution_allowed': False}
        _record_job_status(state, {'job':job, 'status':'started', 'execution_allowed':False}, started_at)
        try:
            result = subprocess.run(command, input=json.dumps(payload), capture_output=True, text=True,
                                    timeout=TIMEOUTS[job], check=False)
            outcome = _safe_result(result, job)
            if invalid_proposal_template and outcome['status'] in ('completed', 'idle'):
                outcome['proposal'] = {'status': 'invalid_template'}
        except subprocess.TimeoutExpired:
            outcome['error'] = 'research_job_timeout'
        except (OSError, ValueError, TypeError, KeyError):
            pass
        finally:
            if not cleanup_container(container):
                ops.private_write(marker, json.dumps({'container': container, 'job': job, 'status': 'cleanup_unknown'}) + '\n')
                outcome = {'job': job, 'status': 'unknown', 'error': 'research_job_cleanup_unknown', 'execution_allowed': False}
            _record_job_status(state, outcome, started_at)
    print(json.dumps(outcome))
    return 1 if outcome['status'] in ('failed', 'unknown') else 0


def templates(state, output_dir, tenant_id, account_id, *, excerpt_dir=None, proposal_template=None):
    """Write inert unit files only; explicit account authorization is supplied by caller."""
    _request(tenant_id, account_id, None, True)
    owner_scope = _proposal_scope(tenant_id, account_id)
    project = json.loads((state / 'deployment.json').read_text())['project']
    ops.check_project(project)
    output_dir.mkdir(parents=True, exist_ok=True)
    units = {}
    schedules = {'news': 'OnCalendar=*-*-* 07..22:00/30:00 Asia/Shanghai\nPersistent=false',
                 'work': 'OnBootSec=2min\nOnUnitActiveSec=2min',
                 'request': 'OnCalendar=*-*-* 08:30:00 Asia/Shanghai\nPersistent=true'}
    start_after = {'news': (), 'request': ('news',), 'work': ('news', 'request')}
    for job, timeout in TIMEOUTS.items():
        unit = 'quantdinger-research-' + job + '-' + project
        args = [sys.executable, Path(__file__).resolve(), '--state-dir', state, job]
        if job == 'request' or (job == 'work' and owner_scope):
            args += ['--tenant-id', tenant_id, '--account-id', account_id]
        if job == 'request': args += ['--daily']
        if job == 'work' and excerpt_dir is not None:
            args += ['--excerpt-dir', excerpt_dir]
        if job == 'work' and proposal_template is not None:
            args += ['--proposal-template', proposal_template]
        command = ' '.join(ops.systemd_argument(value) for value in args)
        dependencies = ' '.join(f'quantdinger-research-{name}-{project}.service' for name in start_after[job])
        if job == 'work' and excerpt_dir is not None:
            dependencies += f' quantdinger-research-news-pdf-{project}.service'
        order = f'\nAfter={dependencies}' if dependencies else ''
        units[unit + '.service'] = f'''[Unit]
Description=Bounded paper research {job} ({project}){order}

[Service]
Type=oneshot
UMask=0077
TimeoutStartSec={timeout + LOCK_WAIT_SECONDS + 25}s
TimeoutStopSec=20s
Restart=no
ExecStart={command}
'''
        units[unit + '.timer'] = f'''[Unit]
Description=Paper research {job} schedule ({project})

[Timer]
{schedules[job]}
Unit={unit}.service

[Install]
WantedBy=timers.target
'''
    if any((output_dir / name).exists() or (output_dir / name).is_symlink() for name in units):
        raise ValueError('unit_template_already_exists')
    for name, content in units.items(): ops.private_write(output_dir / name, content)
    print(json.dumps({'status': 'templates_written_not_installed', 'files': sorted(units), 'execution_allowed': False}))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', required=True, type=Path)
    parser.add_argument('command', choices=(*TIMEOUTS, 'templates'))
    parser.add_argument('--tenant-id')
    parser.add_argument('--account-id', default='paper')
    parser.add_argument('--idempotency-key')
    parser.add_argument('--daily', action='store_true')
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--excerpt', type=Path)
    parser.add_argument('--excerpt-sha256')
    parser.add_argument('--excerpt-dir', type=Path)
    parser.add_argument('--proposal-template', type=Path)
    args = parser.parse_args(argv)
    state = args.state_dir.expanduser().resolve()
    if state == ops.ROOT or ops.ROOT in state.parents:
        parser.error('Keep deployment state outside the checkout')
    if bool(args.excerpt) != bool(args.excerpt_sha256) or (args.excerpt and args.command != 'work'):
        parser.error('--excerpt and --excerpt-sha256 require work')
    if args.excerpt_dir and (args.excerpt or args.command not in ('work', 'templates')):
        parser.error('--excerpt-dir requires work or templates without an explicit excerpt')
    if args.proposal_template and args.command not in ('work', 'templates'):
        parser.error('--proposal-template requires work or templates')
    excerpt_dir = args.excerpt_dir.expanduser().resolve() if args.excerpt_dir else None
    proposal_template = args.proposal_template.expanduser().absolute() if args.proposal_template else None
    if args.command == 'templates':
        if not args.output_dir or not args.tenant_id:
            parser.error('templates requires --output-dir and --tenant-id')
        return templates(state, args.output_dir.expanduser().resolve(), args.tenant_id, args.account_id,
                         excerpt_dir=excerpt_dir, proposal_template=proposal_template)
    if (args.output_dir or (args.command != 'request' and (args.idempotency_key or args.daily))
            or (args.command == 'news' and (args.tenant_id or args.account_id != 'paper'))):
        parser.error('Unexpected job options')
    return run_job(state, args.command, tenant_id=args.tenant_id, account_id=args.account_id,
                   idempotency_key=args.idempotency_key, daily=args.daily,
                   excerpt=args.excerpt.expanduser() if args.excerpt else None, excerpt_sha256=args.excerpt_sha256,
                   excerpt_dir=excerpt_dir, proposal_template=proposal_template)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, ops.stop_on_signal)
    signal.signal(signal.SIGINT, ops.stop_on_signal)
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError):
        print(json.dumps({'status': 'failed', 'error': 'research_job_failed_closed', 'execution_allowed': False}))
        raise SystemExit(1) from None
