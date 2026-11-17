"""Full synthetic multi-day acceptance retains real PostgreSQL transactions."""
import json
import os

import pytest

from app.ashare.multiday_validation import Replay, ValidationFailure, main, run_validation


def test_seven_sessions_with_holiday_weekend_and_restarts(tmp_path):
    dsn = os.environ.get('TEST_ASHARE_DATABASE_URL')
    if not dsn:
        pytest.skip('TEST_ASHARE_DATABASE_URL required')
    report = run_validation(dsn, tmp_path / 'evidence')
    assert report['status'] == 'passed'
    assert report['naturally_observed_multiple_trading_days'] is False
    assert len(report['sessions']) == 7
    assert len(report['daily_reports']) == 7
    assert report['connection_restarts'] == 14
    assert report['abrupt_connection_restarts'] == 7
    assert all(row['passed'] for row in report['checks'])
    assert report['schema_cleaned_up'] is True


def test_cli_failure_report_does_not_expose_connection_details(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('REPLAY_TEST_CONNECTION', 'postgresql://private-user:private-password@invalid/db')
    def unavailable(*args):
        raise ConnectionError('private-password')
    monkeypatch.setattr('app.ashare.multiday_validation.run_validation', unavailable)
    output = tmp_path / 'report.json'
    assert main(['--output', str(output), '--database-env', 'REPLAY_TEST_CONNECTION']) == 1
    assert json.loads(output.read_text()) == {'status': 'failed', 'validation_kind': 'synthetic_multi_session_replay', 'error_type': 'ConnectionError'}
    assert 'private-' not in capsys.readouterr().out + output.read_text()


def test_failure_cleans_up_only_generated_schema(tmp_path):
    dsn = os.environ.get('TEST_ASHARE_DATABASE_URL')
    if not dsn:
        pytest.skip('TEST_ASHARE_DATABASE_URL required')
    replay = Replay(dsn, tmp_path / 'evidence')
    with pytest.raises(ValidationFailure, match='controlled_failure'):
        with replay:
            with replay.connection, replay.connection.cursor() as cur:
                cur.execute('SELECT current_schema()')
                assert cur.fetchone()[0] == replay.schema
            replay.check('controlled_failure', False)
    import psycopg2
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM pg_namespace WHERE nspname=%s', (replay.schema,))
        assert cur.fetchone()[0] == 0
