"""Offline checks for the configured current-theme job and its journal."""
import importlib.util
import io
import json
import signal
import subprocess
import sys
from contextlib import redirect_stdout
from datetime import UTC, datetime, time, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
spec = importlib.util.spec_from_file_location("theme_scheduler", Path(__file__).parents[1] / "theme_scheduler.py")
scheduler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scheduler)
EVENING = datetime(2026, 11, 18, 12, 15, tzinfo=UTC)
EARLY = datetime(2026, 11, 18, 3, 30, tzinfo=UTC)


@pytest.fixture
def state(tmp_path):
    (tmp_path / 'deployment.json').write_text(json.dumps({'project': 'paper-test'}))
    scheduler.initialize_journal(tmp_path)
    return tmp_path


def receipt(board='BK0800', name='人工智能', received='2026-11-18T12:12:00+00:00'):
    return {'status': 'observed', 'board_code': board, 'board_name': name,
            'receipt_id': 'a' * 32, 'received_at': received, 'source_hash': 'b' * 64,
            'member_count': 2, 'source': 'eastmoney', 'membership_semantics': 'current_observation',
            'published_at': None, 'historical_membership_certified': False}


def outcome(board, name, status='ingested', **extra):
    result = {'board_code': board, 'board_name': name, 'status': status}
    if status in {'ingested', 'skip_already_observed'}:
        result.update(receipt(board, name))
        result['status'] = status
        result.update(evidence_status='verified', attempted_network=status == 'ingested')
    return {**result, **extra}


def child(request, snapshots, *, now=EVENING, ingest_error=None):
    """Execute the actual container program with only DB/provider boundaries replaced."""
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    date_module = ModuleType('datetime')
    date_module.datetime, date_module.time, date_module.timezone = Frozen, time, timezone
    store = Mock()
    store.snapshot.side_effect = snapshots
    ingest = Mock(return_value={'receipt_id': 'a' * 32}, side_effect=ingest_error)
    themes = ModuleType('app.ashare.themes')
    themes.ThemeStore = Mock(return_value=store)
    themes.ingest_theme = ingest
    schema = ModuleType('app.ashare.schema')
    schema.get_database_url = Mock(return_value='fixture-private-dsn')
    database = ModuleType('psycopg2')
    database.connect = Mock(return_value=Mock())
    output = io.StringIO()
    with patch.dict(sys.modules, {'datetime': date_module, 'psycopg2': database,
                                 'app.ashare.themes': themes, 'app.ashare.schema': schema}), \
            patch.object(sys, 'stdin', io.StringIO(json.dumps(request))), redirect_stdout(output), pytest.raises(SystemExit) as exited:
        exec(compile(scheduler.CONTAINER_SCRIPT, '<theme-job>', 'exec'), {})  # noqa: S102 - execute our static job source against fake boundaries.
    assert 'fixture-private-dsn' not in output.getvalue()
    return exited.value.code, json.loads(output.getvalue()), ingest, store, database


def test_exact_configured_scope_and_version():
    assert scheduler.CONFIGURED_THEMES == (('BK0800', '人工智能'), ('BK1090', '机器人概念'), ('BK0493', '新能源'))
    assert scheduler.CONFIG_VERSION == 'eastmoney-configured-themes-20261118-v1'


def test_before_window_no_process_and_no_journal_overwrite(state):
    journal = scheduler.load_journal(state)
    journal.update(status='failed', window_date='2026-11-17', completed_at='2026-11-17T12:20:00+00:00')
    scheduler.atomic_state(state / scheduler.STATE_NAME, journal)
    before = (state / scheduler.STATE_NAME).read_bytes()
    with patch.object(scheduler, 'utc_now', return_value=EARLY), patch.object(scheduler.subprocess, 'run') as run:
        assert scheduler.sync(state) == 0
    run.assert_not_called()
    assert (state / scheduler.STATE_NAME).read_bytes() == before


def test_missing_or_corrupt_journal_fails_closed_even_before_window(state):
    (state / scheduler.STATE_NAME).write_text('{corrupt')
    with patch.object(scheduler, 'utc_now', return_value=EARLY), patch.object(scheduler.subprocess, 'run') as run, pytest.raises(ValueError):
        scheduler.sync(state)
    run.assert_not_called()
    (state / scheduler.STATE_NAME).unlink()
    with pytest.raises(ValueError):
        scheduler.sync(state)
    assert not (state / scheduler.STATE_NAME).exists()


def test_live_mode_or_scope_corruption_rejected(state):
    journal = scheduler.load_journal(state)
    journal['mode'] = 'live'
    scheduler.atomic_state(state / scheduler.STATE_NAME, journal)
    with pytest.raises(ValueError):
        scheduler.load_journal(state)


def test_dangling_marker_cannot_be_reinitialized(tmp_path):
    (tmp_path / 'deployment.json').write_text(json.dumps({'project': 'paper-test'}))
    (tmp_path / scheduler.MARKER_NAME).symlink_to(tmp_path / 'missing')
    with pytest.raises(ValueError):
        scheduler.initialize_journal(tmp_path)
    assert (tmp_path / scheduler.MARKER_NAME).is_symlink()


def test_partial_failures_continue_and_persist_attempt_before_subprocess(state):
    calls = []
    def fake(state, board, name, allowed, day, deadline, container):
        journal = scheduler.load_journal(state)
        assert journal['active_container'] == {'name': container, 'board_code': board, 'window_date': day}
        recorded = journal['attempts'][board]
        assert recorded['status'] == 'started' and recorded['window_date'] == '2026-11-18'
        assert allowed is True
        calls.append(board)
        return outcome(board, name, 'failed', error='theme_ingest_failed') if board == 'BK1090' else outcome(board, name)
    with patch.object(scheduler, 'utc_now', return_value=EVENING), patch.object(scheduler, 'run_board', side_effect=fake):
        assert scheduler.sync(state) == 1
    assert calls == ['BK0800', 'BK1090', 'BK0493']
    saved = scheduler.load_journal(state)
    assert saved['status'] == 'partial'
    assert saved['attempts']['BK1090']['status'] == 'failed'
    assert saved['approvals_created'] == 0


def test_same_window_failure_or_unknown_never_retries_ingest(state):
    def fail(state, board, name, allowed, day, deadline, container):
        return outcome(board, name, 'unknown', error='theme_job_timeout')
    with patch.object(scheduler, 'utc_now', return_value=EVENING), patch.object(scheduler, 'run_board', side_effect=fail):
        assert scheduler.sync(state) == 1
    def readonly(state, board, name, allowed, day, deadline, container):
        assert allowed is False
        return outcome(board, name, 'skip_attempted', error='window_attempt_already_recorded')
    with patch.object(scheduler, 'utc_now', return_value=EVENING), patch.object(scheduler, 'run_board', side_effect=readonly):
        assert scheduler.sync(state) == 1
    assert all(item['status'] == 'unknown' for item in scheduler.load_journal(state)['attempts'].values())


def test_next_evening_is_new_observation_not_historical_backfill(state):
    with patch.object(scheduler, 'utc_now', return_value=EVENING), patch.object(scheduler, 'run_board', side_effect=lambda st, b, n, *args: outcome(b, n, 'failed', error='theme_ingest_failed')):
        assert scheduler.sync(state) == 1
    tomorrow = datetime(2026, 11, 19, 12, 20, tzinfo=UTC)
    def observe(st, board, name, allowed, day, deadline, container):
        assert allowed and day == '2026-11-19'
        return outcome(board, name)
    with patch.object(scheduler, 'utc_now', return_value=tomorrow), patch.object(scheduler, 'run_board', side_effect=observe):
        assert scheduler.sync(state) == 0
    assert all(item['window_date'] == '2026-11-19' for item in scheduler.load_journal(state)['attempts'].values())


def test_existing_operation_lock_rejects_job(state):
    with scheduler.ops.operation_lock(state), patch.object(scheduler.subprocess, 'run') as run, pytest.raises(RuntimeError):
        scheduler.sync(state)
    run.assert_not_called()


def test_child_before_window_does_not_query_or_fetch():
    code, result, ingest, store, db = child({'board_code': 'BK0800', 'board_name': '人工智能', 'allow_ingest': True}, [], now=EARLY)
    assert code == 1 and result['status'] == 'skipped_before_window'
    ingest.assert_not_called()
    db.connect.assert_not_called()
    store.snapshot.assert_not_called()


def test_child_current_receipt_is_verified_then_skipped():
    code, result, ingest, store, _ = child({'board_code': 'BK0800', 'board_name': '人工智能', 'allow_ingest': True}, [receipt()])
    assert code == 0 and result['status'] == 'skip_already_observed'
    assert result['evidence_status'] == 'verified'
    ingest.assert_not_called()
    store.snapshot.assert_called_once_with('BK0800', limit=1)


def test_child_early_receipt_does_not_replace_evening_observation():
    code, result, ingest, store, _ = child({'board_code': 'BK0800', 'board_name': '人工智能', 'allow_ingest': True},
                                         [receipt(received='2026-11-18T00:00:00+00:00'), receipt()])
    assert code == 0 and result['status'] == 'ingested'
    assert ingest.call_args.kwargs == {'board_code': 'BK0800', 'expected_name': '人工智能', 'timeout': 30}
    assert store.snapshot.call_count == 2


def test_child_corrupt_source_fails_without_fetch_or_retry():
    code, result, ingest, _, _ = child({'board_code': 'BK0800', 'board_name': '人工智能', 'allow_ingest': True}, [ValueError('private-provider-token')])
    assert code == 1 and result['error'] == 'theme_receipt_verification_failed'
    assert 'private-provider-token' not in json.dumps(result)
    ingest.assert_not_called()


def test_child_failed_attempt_can_only_read_existing_receipt():
    code, result, ingest, _, _ = child({'board_code': 'BK0800', 'board_name': '人工智能', 'allow_ingest': False}, [{'status': 'not_observed'}])
    assert code == 1 and result['status'] == 'skip_attempted'
    ingest.assert_not_called()


@pytest.mark.parametrize('payload', [
    {'board_code': 'BK0800', 'board_name': 'wrong', 'allow_ingest': True},
    {'board_code': 'BK0000', 'board_name': '人工智能', 'allow_ingest': True},
    {'board_code': 'BK0800', 'board_name': '人工智能', 'allow_ingest': True, 'date': '2020-01-01'}])
def test_child_rejects_identity_changes_and_backdate(payload):
    code, result, ingest, _, db = child(payload, [])
    assert code == 1 and result['error'] == 'invalid_theme_job_scope'
    ingest.assert_not_called()
    db.connect.assert_not_called()


def test_cli_has_no_backdate_or_scope_override(state):
    for option in ('--date', '--known-at', '--lookback', '--board-code', '--timeout'):
        with pytest.raises(SystemExit) as error:
            scheduler.main(['--state-dir', str(state), 'run', option, '2020-01-01'])
        assert error.value.code == 2


def test_timeout_bounded_and_only_own_container_removed(state):
    with patch.object(scheduler.ops, 'compose', side_effect=lambda state, *args: list(args)), patch.object(scheduler.subprocess, 'run') as run:
        run.side_effect = [subprocess.TimeoutExpired('fixture', 45), SimpleNamespace(returncode=0)]
        result = scheduler.run_board(state, 'BK0800', '人工智能', True, '2026-11-18',
                                     scheduler.time.monotonic() + 240, 'quantdinger-theme-fixture')
    command = run.call_args_list[0].args[0]
    container = command[command.index('--name') + 1]
    assert result['status'] == 'unknown' and result['error'] == 'theme_job_timeout'
    assert run.call_args_list[0].kwargs['timeout'] == 45
    assert run.call_args_list[1].args[0] == ['docker', 'rm', '-f', container]
    assert run.call_args_list[1].kwargs['timeout'] <= 10
    assert 'stop' not in command


def test_cleanup_failure_halts_remaining_fetches(state):
    with patch.object(scheduler, 'utc_now', return_value=EVENING), patch.object(scheduler, 'run_board', return_value=outcome('BK0800', '人工智能', 'unknown', error='theme_job_cleanup_unknown')) as job:
        assert scheduler.sync(state) == 1
    job.assert_called_once()
    assert len(scheduler.load_journal(state)['boards']) == 3
    assert scheduler.load_journal(state)['active_container']['board_code'] == 'BK0800'


def interrupted_board(state):
    journal = scheduler.load_journal(state)
    day, started = scheduler.window(EVENING)
    container = 'quantdinger-theme-' + journal['scheduler_id'] + '-' + '4' * 32
    journal['attempts']['BK0800'] = {'window_date': day, 'started_at': started.isoformat(), 'status': 'started'}
    journal['active_container'] = {'name': container, 'board_code': 'BK0800', 'window_date': day}
    scheduler.atomic_state(state / scheduler.STATE_NAME, journal)
    return container


def test_interrupted_container_cleanup_is_fenced_before_same_window_verification(state):
    original = interrupted_board(state)
    events = []

    def remove(name):
        events.append(('cleanup', name))
        return True

    def visit(state, board, name, allowed, day, deadline, container):
        assert scheduler.load_journal(state)['active_container']['name'] == container
        events.append((board, allowed))
        if allowed:
            return outcome(board, name)
        return outcome(board, name, 'skip_attempted', error='window_attempt_already_recorded')

    with patch.object(scheduler, 'utc_now', return_value=EVENING), \
            patch.object(scheduler, 'cleanup_container', side_effect=remove), \
            patch.object(scheduler, 'run_board', side_effect=visit):
        assert scheduler.sync(state) == 1
    assert events == [('cleanup', original), ('BK0800', False), ('BK1090', True), ('BK0493', True)]
    assert 'active_container' not in scheduler.load_journal(state)


def test_uncertain_recovered_container_blocks_new_work_and_preserves_handle(state):
    original = interrupted_board(state)
    with patch.object(scheduler, 'utc_now', return_value=EVENING), \
            patch.object(scheduler, 'cleanup_container', return_value=False) as remove, \
            patch.object(scheduler, 'run_board') as visit:
        assert scheduler.sync(state) == 1
    remove.assert_called_once_with(original)
    visit.assert_not_called()
    assert scheduler.load_journal(state)['active_container']['name'] == original


def test_foreign_container_name_cannot_be_removed_from_journal(state):
    interrupted_board(state)
    journal = scheduler.load_journal(state)
    journal['active_container']['name'] = 'quantdinger-theme-' + '0' * 32 + '-' + '4' * 32
    scheduler.atomic_state(state / scheduler.STATE_NAME, journal)
    with patch.object(scheduler, 'cleanup_container') as remove, pytest.raises(ValueError, match='active_container_invalid'):
        scheduler.sync(state)
    remove.assert_not_called()


def test_cleanup_return_one_requires_positive_absence_check():
    with patch.object(scheduler.subprocess, 'run') as run:
        run.side_effect = [SimpleNamespace(returncode=1), SimpleNamespace(returncode=1, stderr='Cannot connect to Docker daemon', stdout='')]
        assert scheduler.cleanup_container('quantdinger-theme-fixture') is False
    with patch.object(scheduler.subprocess, 'run') as run:
        run.side_effect = [SimpleNamespace(returncode=1), SimpleNamespace(returncode=1, stderr='Error: No such object: quantdinger-theme-fixture\n', stdout='[]\n')]
        assert scheduler.cleanup_container('quantdinger-theme-fixture') is True
    assert sum(call.kwargs['timeout'] for call in run.call_args_list) <= 10


def test_total_budget_stops_starting_subprocesses(state):
    ticks = iter([0, 241, 241, 241])
    with patch.object(scheduler, 'utc_now', return_value=EVENING), patch.object(scheduler.time, 'monotonic', side_effect=lambda: next(ticks)), patch.object(scheduler, 'run_board') as job:
        assert scheduler.sync(state) == 1
    job.assert_not_called()
    assert scheduler.TOTAL_TIMEOUT + scheduler.CLEANUP_TIMEOUT <= 300


def test_sigterm_preserves_started_journal_and_removes_own_container(state):
    with patch.object(scheduler, 'utc_now', return_value=EVENING), patch.object(scheduler.ops, 'compose', return_value=['job']), patch.object(scheduler.subprocess, 'run') as run:
        run.side_effect = [SystemExit(128 + signal.SIGTERM), SimpleNamespace(returncode=0)]
        with pytest.raises(SystemExit):
            scheduler.sync(state)
    assert scheduler.load_journal(state)['attempts']['BK0800']['status'] == 'started'
    assert run.call_args_list[-1].args[0][:3] == ['docker', 'rm', '-f']


def test_timer_initializes_once_and_only_enables_timer(tmp_path):
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'deployment.json').write_text(json.dumps({'project': 'paper-test'}))
    with patch.object(scheduler.Path, 'home', return_value=tmp_path), patch.object(scheduler.ops, 'run') as run:
        scheduler.install_timer(state)
    assert scheduler.load_journal(state)['status'] == 'initialized'
    assert (state / scheduler.STATE_NAME).stat().st_mode & 0o777 == 0o600
    units = tmp_path / '.config/systemd/user'
    timer = (units / 'quantdinger-themes-paper-test.timer').read_text()
    service = (units / 'quantdinger-themes-paper-test.service').read_text()
    assert '20:10:00 Asia/Shanghai' in timer and 'RandomizedDelaySec=60' in timer and 'Persistent=true' in timer
    assert 'TimeoutStartSec=270s' in service and 'TimeoutStopSec=30s' in service
    assert 'PASSWORD' not in service and 'SECRET' not in service
    assert run.call_args_list[-1].args[0][-1] == 'quantdinger-themes-paper-test.timer'
    assert not any('start' in call.args[0] for call in run.call_args_list)
    before = (state / scheduler.STATE_NAME).read_bytes()
    with patch.object(scheduler.Path, 'home', return_value=tmp_path), pytest.raises(ValueError):
        scheduler.install_timer(state)
    assert before == (state / scheduler.STATE_NAME).read_bytes()
