"""Real-database operational checks for durable paper-only execution."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest

from app.ashare.calendar_rules import market_session
from app.ashare.managed_worker import ManagedWorker
from app.ashare.operations import complete_daily_reports, list_daily_reports, read_health, reconcile_account, write_heartbeat
from tests.ashare.test_managed_worker import account, approve, instant, quote  # noqa: F401


def worker_for(connection, path, now, **kwargs):
    return ManagedWorker(connection, path, fetcher=lambda *a, **k: [quote(now)], notifications_enabled=False, **kwargs)


def test_close_releases_same_day_reservations_and_report_is_restart_idempotent(account):
    connection, store, path = account
    approve(store)
    store.broker.place_order('day', 'sz.000001', 'buy', 200, '10', instant(), instant().date())
    q = quote(instant(second=6))
    q['ask_size'] = 100
    store.broker.on_quote(q, store.state()['rules']['sz.000001'], now=instant(second=6))
    assert store.broker.snapshot()['account']['reserved_cash'] > 0
    now = instant(hour=7)
    first = worker_for(connection, path, now).process_account(store.tenant_id, now=now)
    assert len(first['expired_day_orders']) == 1
    assert store.broker.snapshot()['account']['reserved_cash'] == 0
    reports = list_daily_reports(connection, store.tenant_id)
    assert len(reports) == 1
    report = reports[0]['report']
    assert report['day_fill_count'] == 1
    assert report['cash_end'] == '8994.99'
    assert report['positions_end'] == [{'symbol': 'sz.000001', 'quantity': 100}]
    assert report['equity_end'] is None
    assert report['reconciliation']['ok'] is True
    worker_for(connection, path, now).process_account(store.tenant_id, now=now)
    assert list_daily_reports(connection, store.tenant_id) == reports


def test_offline_backfill_reconstructs_past_positions_not_present_positions(account):
    connection, store, path = account
    day13 = instant(day=13)
    approve(store, 13)
    worker_for(connection, path, day13).process_account(store.tenant_id, now=day13)
    store.broker.place_order('friday', 'sz.000001', 'buy', 100, '10', day13, day13.date())
    store.broker.on_quote(quote(instant(day=13, second=6)), store.state()['rules']['sz.000001'], now=instant(day=13, second=6))
    approve(store, 16)
    day16 = instant(day=16)
    store.broker.place_order('monday', 'sz.000001', 'sell', 100, '9.99', day16, day16.date())
    store.broker.on_quote(quote(instant(day=16, second=6)), store.state()['rules']['sz.000001'], now=instant(day=16, second=6))
    assert store.broker.snapshot()['positions'] == []
    now = instant(day=17)
    created = complete_daily_reports(connection, store.tenant_id, 'paper', now, market_session(now))
    assert [r['trading_day'] for r in created] == ['2026-11-13', '2026-11-16']
    assert created[0]['positions_end'] == [{'symbol': 'sz.000001', 'quantity': 100}]
    assert created[1]['positions_end'] == []
    assert Decimal(created[0]['cash_end']) == Decimal('8994.99')
    assert Decimal(created[1]['cash_end']) == store.broker.snapshot()['account']['cash']
    assert all(r['late_generation'] and r['reconciliation']['ok'] for r in created)
    assert list_daily_reports(connection, 'other-tenant') == []
    assert complete_daily_reports(connection, store.tenant_id, 'paper', now, market_session(now)) == []


def test_reconciliation_failure_blocks_fills_until_actual_ledger_repaired(account):
    connection, store, path = account
    approve(store)
    store.broker.place_order('order', 'sz.000001', 'buy', 100, '10', instant(), instant().date())
    with connection, connection.cursor() as cursor:
        cursor.execute('UPDATE qd_ashare_accounts SET cash=cash+1 WHERE tenant_id=%s', (store.tenant_id,))
    source = Mock(return_value=[quote(instant(second=6))])
    worker = ManagedWorker(connection, path, fetcher=source, notifications_enabled=False)
    assert worker.process_account(store.tenant_id, now=instant(second=6))['state'] == 'reconciliation_failed'
    assert worker.process_account(store.tenant_id, now=instant(second=7))['state'] == 'reconciliation_failed'
    source.assert_not_called()
    assert store.broker.snapshot()['fills'] == []
    with connection, connection.cursor() as cursor:
        cursor.execute('UPDATE qd_ashare_accounts SET cash=cash-1 WHERE tenant_id=%s', (store.tenant_id,))
    assert reconcile_account(connection, store.tenant_id, 'paper')['ok']
    assert worker.process_account(store.tenant_id, now=instant(second=8))['state'] == 'healthy'
    assert len(store.broker.snapshot()['fills']) == 1


def test_low_disk_pauses_ingestion_and_matching_without_deleting_evidence(account):
    connection, store, path = account
    approve(store)
    sentinel = path / 'existing-evidence'
    sentinel.write_text('retain')
    source = Mock(return_value=[quote(instant())])
    worker = ManagedWorker(connection, path, fetcher=source, notifications_enabled=False, min_free_disk_bytes=100, disk_free=lambda _: 99)
    assert worker.process_account(store.tenant_id, now=instant())['state'] == 'storage_low'
    source.assert_not_called()
    assert sentinel.read_text() == 'retain'
    worker.disk_free = lambda _: 101
    assert worker.process_account(store.tenant_id, now=instant(second=1))['state'] == 'healthy'


def test_closed_session_throttles_polling_but_retains_heartbeat(account):
    connection, store, path = account
    now = instant(hour=8)
    source = Mock(return_value=[quote(now)])
    worker = ManagedWorker(connection, path, fetcher=source, notifications_enabled=False)
    worker.claim()
    for offset in (0, 1, 30, 120, 299):
        worker.cycle(now=now + timedelta(seconds=offset))
    assert source.call_count == 1
    health = read_health(connection, now + timedelta(seconds=299))
    assert health['healthy'] is True
    assert health['details']['ready_accounts'] == 0
    assert health['details']['states'] == ['closed']
    worker.cycle(now=now + timedelta(seconds=300))
    assert source.call_count == 2


def test_process_health_exists_with_zero_accounts_and_rejects_stale_future_or_stopped(account):
    connection, store, path = account
    with connection, connection.cursor() as cursor:
        cursor.execute('DELETE FROM qd_ashare_web')
    worker = worker_for(connection, path, instant())
    worker.claim()
    assert worker.cycle(now=instant()) == []
    assert read_health(connection, instant())['healthy'] is True
    assert read_health(connection, instant() + timedelta(seconds=121))['healthy'] is False
    assert read_health(connection, instant() - timedelta(seconds=1))['healthy'] is False
    write_heartbeat(connection, worker.instance_id, instant(), {'status': 'stopped'})
    assert read_health(connection, instant())['healthy'] is False


def test_unknown_calendar_does_not_fabricate_daily_reports(account):
    connection, store, path = account
    now = instant().replace(year=2030)
    result = worker_for(connection, path, now).process_account(store.tenant_id, now=now)
    assert result['state'] == 'calendar_unknown'
    assert list_daily_reports(connection, store.tenant_id) == []


def test_healthcheck_failure_redacts_connection_details(monkeypatch, capsys):
    import psycopg2
    from app.ashare import healthcheck
    monkeypatch.setenv('ASHARE_DATABASE_URL', 'postgres://secret:password@private.invalid/db')
    def fail(*args, **kwargs):
        raise psycopg2.OperationalError('sensitive-password-dsn')
    monkeypatch.setattr(healthcheck.psycopg2, 'connect', fail)
    assert healthcheck.main([]) == 1
    result = capsys.readouterr().out
    assert 'database_or_schema_unavailable' in result
    assert 'password' not in result and 'private' not in result


def test_report_limit_is_bounded(account):
    connection, store, _ = account
    with pytest.raises(ValueError, match='invalid_report_limit'):
        list_daily_reports(connection, store.tenant_id, limit=0)


def test_report_observations_distinguish_controlled_time_and_missing_days(account):
    connection, store, path = account
    from app.ashare.operations import observation_summary, record_poll
    now = instant(day=13)
    worker = worker_for(connection, path, now)
    worker.process_account(store.tenant_id, now=now)
    record_poll(connection, store.tenant_id, 'paper', instant(day=13, second=10), [quote(now)], 'awaiting_review', 'wall_clock')
    stats = observation_summary(connection, store.tenant_id, 'paper', now.date())
    assert stats['poll_count'] == 2
    assert stats['controlled_time_polls'] == stats['wall_clock_polls'] == 1
    assert stats['coverage'] == 'sampled_not_continuous_uptime_proof'
    assert stats['max_source_age_seconds'] == 10
    resumed = instant(day=17)
    reports = complete_daily_reports(connection, store.tenant_id, 'paper', resumed, market_session(resumed))
    assert reports[0]['observation_summary']['poll_count'] == 2
    assert reports[1]['observation_summary']['status'] == 'not_observed'


def test_observation_tracks_source_failures_staleness_and_traceability(account):
    connection, store, path = account
    from app.ashare.operations import observation_summary, record_poll
    now = instant()
    record_poll(connection, store.tenant_id, 'paper', now + timedelta(seconds=30), [quote(now)], 'degraded', 'controlled_time')
    record_poll(connection, store.tenant_id, 'paper', now + timedelta(seconds=40), [], 'failed', 'controlled_time')
    stats = observation_summary(connection, store.tenant_id, 'paper', now.date())
    assert stats['failed_polls'] == 1
    assert stats['stale_quote_polls'] == 1
    assert stats['max_source_age_seconds'] == 30
    assert stats['last_details']['quote_count'] == 0


def test_worker_cli_rejects_empty_database_configuration(monkeypatch, capsys):
    from app.ashare import managed_worker
    monkeypatch.setattr(managed_worker, 'get_database_url', lambda: '')
    with pytest.raises(SystemExit) as exit_info:
        managed_worker.main(['--cycles', '1'])
    assert exit_info.value.code == 2
    assert 'ASHARE_DATABASE_URL or DATABASE_URL required' in capsys.readouterr().err


def test_normal_bounded_worker_exit_persists_stopped_health(account):
    import os
    import psycopg2
    from app.ashare.managed_worker import run_forever
    connection, store, path = account
    with connection, connection.cursor() as cursor:
        cursor.execute('DELETE FROM qd_ashare_web')
        cursor.execute('SELECT current_schema()')
        schema = cursor.fetchone()[0]
    def connect(*args, **kwargs):
        return psycopg2.connect(os.environ['TEST_ASHARE_DATABASE_URL'], options='-c search_path=' + schema)
    assert run_forever('injected-connection', path, cycles=1, connect=connect) == 0
    from app.ashare.runtime import utc_now
    health = read_health(connection, utc_now())
    assert health['healthy'] is False
    assert health['details']['status'] == 'stopped'
