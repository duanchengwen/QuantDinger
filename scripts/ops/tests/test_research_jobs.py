"""Finite scheduler tests use fake process boundaries only."""
import base64
import hashlib
import importlib.util
import io
import json
import signal
import subprocess
import sys
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
spec = importlib.util.spec_from_file_location('research_jobs', Path(__file__).parents[1]/'research_jobs.py')
jobs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jobs)


@pytest.fixture
def state(tmp_path):
    (tmp_path/'deployment.json').write_text(json.dumps({'project': 'paper-test'}))
    return tmp_path


def test_timeout_cleans_only_named_container_and_no_retry(state, capsys):
    with patch.object(jobs.ops, 'compose', side_effect=lambda state,*args:list(args)), \
         patch.object(jobs.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixture', 150)) as run, \
         patch.object(jobs, 'cleanup_container', return_value=True) as clean:
        assert jobs.run_job(state, 'work') == 1
    run.assert_called_once()
    command = run.call_args.args[0]
    assert run.call_args.kwargs['timeout'] == 150
    name = command[command.index('--name')+1]
    clean.assert_called_once_with(name)
    assert name.startswith('quantdinger-research-work-')
    assert '--rm' in command and '--no-deps' in command
    assert json.loads(capsys.readouterr().out)['error'] == 'research_job_timeout'


def test_uncertain_cleanup_blocks_future_jobs(state):
    with patch.object(jobs.ops, 'compose', return_value=['fixture']), \
         patch.object(jobs.subprocess, 'run', side_effect=OSError('private-token')), \
         patch.object(jobs, 'cleanup_container', return_value=False):
        assert jobs.run_job(state, 'work') == 1
    marker = state/jobs.CLEANUP_MARKER
    assert marker.stat().st_mode & 0o777 == 0o600
    assert 'private-token' not in marker.read_text()
    with patch.object(jobs.subprocess, 'run') as run, pytest.raises(ValueError): jobs.run_job(state, 'work')
    run.assert_not_called()


def test_sigterm_still_cleans_container(state):
    with patch.object(jobs.ops, 'compose', return_value=['fixture']), \
         patch.object(jobs.subprocess, 'run', side_effect=SystemExit(128+signal.SIGTERM)), \
         patch.object(jobs, 'cleanup_container', return_value=True) as clean:
        with pytest.raises(SystemExit): jobs.run_job(state, 'work')
    clean.assert_called_once()


def test_existing_operation_lock_prevents_overlapping_jobs(state):
    with jobs.ops.operation_lock(state), patch.object(jobs, 'LOCK_WAIT_SECONDS', 0), \
            patch.object(jobs.subprocess, 'run') as run, pytest.raises(RuntimeError):
        jobs.run_job(state, 'news')
    run.assert_not_called()


def test_daily_request_waits_for_short_lock_collision_before_enqueuing_once(state, capsys):
    response = SimpleNamespace(stdout=json.dumps({
        'job': 'request', 'status': 'queued', 'id': 'a' * 32, 'execution_allowed': False,
    }), returncode=0)
    with ExitStack() as active_job:
        active_job.enter_context(jobs.ops.operation_lock(state))
        with patch.object(jobs.ops.time, 'sleep', side_effect=lambda _: active_job.close()) as wait, \
                patch.object(jobs.subprocess, 'run', return_value=response) as run, \
                patch.object(jobs, 'cleanup_container', return_value=True):
            assert jobs.run_job(state, 'request', tenant_id='user:7', daily=True) == 0
    wait.assert_called_once()
    run.assert_called_once()
    assert json.loads(capsys.readouterr().out)['status'] == 'queued'


@pytest.mark.parametrize('payload', [
    {'job':'work','status':'completed','execution_allowed':True},
    {'job':'work','status':'arbitrary','execution_allowed':False},
    {'job':'work','status':'completed','execution_allowed':False,'id':'private-token'},
    {'job':'news','status':'ingested','execution_allowed':False,'receipt_id':'a'*32,'source_hash':'b'*64,'item_count':True},
])
def test_invalid_outputs_are_rejected(payload):
    with pytest.raises(ValueError):
        jobs._safe_result(SimpleNamespace(stdout=json.dumps(payload),returncode=0), payload['job'])


def test_output_strips_unexpected_provider_and_secret_fields():
    payload = {'job':'work','status':'completed','execution_allowed':False,'id':'a'*32,'secret':'do-not-expose','raw_response':'private'}
    safe = jobs._safe_result(SimpleNamespace(stdout=json.dumps(payload),returncode=0),'work')
    assert set(safe) == {'job','status','execution_allowed','id'}


def test_templates_are_inert_explicit_account_and_bounded(state, tmp_path):
    folder = tmp_path/'units'
    with patch.object(jobs.subprocess, 'run') as run:
        assert jobs.templates(state, folder, 'user_7', 'paper') == 0
    run.assert_not_called()
    assert len(list(folder.iterdir())) == 6
    news = (folder/'quantdinger-research-news-paper-test.timer').read_text()
    work = (folder/'quantdinger-research-work-paper-test.timer').read_text()
    request = (folder/'quantdinger-research-request-paper-test.service').read_text()
    worker = (folder/'quantdinger-research-work-paper-test.service').read_text()
    assert '07..22:00/30:00 Asia/Shanghai' in news
    assert 'OnUnitActiveSec=2min' in work
    assert 'user_7' in request and '--daily' in request and '--account-id' in request
    assert '"--tenant-id" "user_7" "--account-id" "paper"' in worker
    assert 'After=quantdinger-research-news-paper-test.service\n' in request
    assert 'After=quantdinger-research-news-paper-test.service quantdinger-research-request-paper-test.service\n' in worker
    for path in folder.glob('*.service'):
        assert 'Restart=no' in path.read_text() and 'TimeoutStartSec=' in path.read_text()
        assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError): jobs.templates(state, folder, 'user_7', 'paper')


def test_excerpt_template_changes_only_the_research_worker(state):
    plain = state / 'plain-units'
    enabled = state / 'excerpt-units'
    directory = state / 'compiled-excerpts'
    jobs.templates(state, plain, 'user_7', 'paper')
    jobs.templates(state, enabled, 'user_7', 'paper', excerpt_dir=directory)
    worker_name = 'quantdinger-research-work-paper-test.service'
    for expected in plain.iterdir():
        actual = (enabled / expected.name).read_text()
        if expected.name == worker_name:
            assert '"--excerpt-dir"' in actual and str(directory) in actual
            assert 'quantdinger-research-news-pdf-paper-test.service' in actual
        else:
            assert actual == expected.read_text()


def test_legacy_non_owner_schedule_still_runs_research_only(state):
    units = state / 'legacy-units'
    jobs.templates(state, units, 'user:7', 'paper')
    request = (units / 'quantdinger-research-request-paper-test.service').read_text()
    worker = (units / 'quantdinger-research-work-paper-test.service').read_text()
    assert '"--tenant-id" "user:7"' in request
    assert '--tenant-id' not in worker


def test_scheduler_template_opt_in_changes_only_worker_command(state):
    plain = state / 'plain-units'
    enabled = state / 'proposal-units'
    template = state / 'owner-only.json'
    jobs.templates(state, plain, 'user_7', 'paper')
    jobs.templates(state, enabled, 'user_7', 'paper', proposal_template=template)
    worker_name = 'quantdinger-research-work-paper-test.service'
    for original in plain.iterdir():
        changed = (enabled / original.name).read_text()
        if original.name == worker_name:
            assert f'"--proposal-template" "{template}"' in changed
        else:
            assert changed == original.read_text()


@pytest.mark.parametrize('source', ['private', 'public', 'symlink', 'large', 'missing'])
def test_proposal_template_is_private_and_sent_only_via_stdin(state, source):
    original = state / 'owner-only.json'
    template = original
    if source != 'missing':
        jobs.ops.private_write(original, 'fixture private value')
    if source == 'public':
        original.chmod(0o644)
    if source == 'symlink':
        template = state / 'redirect'
        template.symlink_to(original)
    if source == 'large':
        original.write_bytes(b'x' * (jobs.MAX_PROPOSAL_BYTES + 1))
    idle = SimpleNamespace(stdout='{"job":"work","status":"idle","execution_allowed":false}', returncode=0)
    with (
        patch.object(jobs.ops, 'compose', return_value=['fixture']),
        patch.object(jobs, 'cleanup_container', return_value=True),
        patch.object(jobs.subprocess, 'run', return_value=idle) as run,
    ):
        assert jobs.run_job(state, 'work', tenant_id='user_7', proposal_template=template) == 0
    payload = json.loads(run.call_args.kwargs['input'])
    summary = jobs.load_job_status(state)['jobs']['work']
    assert run.call_args.args[0] == ['fixture']
    assert 'fixture private value' not in (state / jobs.STATUS_NAME).read_text()
    if source == 'private':
        assert base64.b64decode(payload['proposal_template']) == b'fixture private value'
        assert payload['proposal_scope'] == ['user_7', 'paper']
        assert 'proposal' not in summary
    else:
        assert payload == {'job': 'work', 'proposal_scope': ['user_7', 'paper'], 'proposal_template': ''}
        assert summary['proposal'] == {'status': 'invalid_template'}


def test_scoped_work_reads_saved_template_without_operator_file(state):
    response = SimpleNamespace(stdout='{"job":"work","status":"idle","execution_allowed":false,'
                                      '"proposal":{"status":"inactive_template"}}', returncode=0)
    with (
        patch.object(jobs.ops, 'compose', return_value=['fixture']),
        patch.object(jobs, 'cleanup_container', return_value=True),
        patch.object(jobs.subprocess, 'run', return_value=response) as run,
    ):
        assert jobs.run_job(state, 'work', tenant_id='user_7') == 0
        with pytest.raises(ValueError, match='invalid_proposal_scope'):
            jobs.run_job(state, 'work', tenant_id='user_7', account_id='live')
    run.assert_called_once()
    assert json.loads(run.call_args.kwargs['input']) == {'job': 'work', 'proposal_scope': ['user_7', 'paper']}
    assert jobs.load_job_status(state)['jobs']['work']['proposal'] == {'status': 'inactive_template'}


@pytest.mark.parametrize('proposal', [
    {'status': 'draft_created', 'version_id': 'f' * 32, 'notice': 'secret-response'},
    {'status': 'draft_created', 'version_id': 'f' * 32},
    {'status': 'already_proposed', 'version_id': 'private-configuration', 'notice': 'unknown'},
    {'status': 'no_eligible_template_match', 'webhook': 'fixture-leak'},
])
def test_proposal_summary_rejects_untrusted_job_payloads(proposal):
    value = {'job': 'work', 'status': 'completed', 'execution_allowed': False, 'proposal': proposal}
    with pytest.raises(ValueError, match='invalid_proposal_status'):
        jobs._safe_result(SimpleNamespace(stdout=json.dumps(value), returncode=0), 'work')


def test_explicit_excerpt_is_given_to_worker_only_via_stdin(state):
    raw = '待核验的正文节选'.encode()
    artifact = state / 'excerpt.json'
    artifact.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    response = SimpleNamespace(stdout='{"job":"work","status":"idle","execution_allowed":false}', returncode=0)
    with (
        patch.object(jobs.ops, 'compose', return_value=['fixture']),
        patch.object(jobs, 'cleanup_container', return_value=True),
        patch.object(jobs.subprocess, 'run', return_value=response) as run,
    ):
        assert jobs.run_job(state, 'work', excerpt=artifact, excerpt_sha256=digest) == 0
        with pytest.raises(ValueError, match='invalid_news_excerpt_evidence'):
            jobs.run_job(state, 'work', excerpt=artifact, excerpt_sha256='0' * 64)
    run.assert_called_once()
    payload = json.loads(run.call_args.kwargs['input'])
    assert payload == {'job': 'work', 'excerpt': base64.b64encode(raw).decode(), 'excerpt_sha256': digest}
    assert run.call_args.args[0] == ['fixture']
    assert 'excerpt' not in jobs.load_job_status(state)['jobs']['work']


@pytest.mark.parametrize('mode', ['valid', 'wrong_receipt', 'tampered', 'not_ingested', 'no_file'])
def test_latest_excerpt_requires_exact_successful_journal_proof(state, mode):
    receipt = 'a' * 32
    raw = b'local evidence sample'
    path = state / (receipt + '.json')
    if mode != 'no_file':
        path.write_bytes(raw if mode != 'tampered' else raw + b' tampered')
    result = {'job': 'news', 'status': 'ingested', 'receipt_id': receipt,
              'item_count': 1, 'source_hash': 'b' * 64, 'execution_allowed': False}
    if mode == 'not_ingested':
        result = {'job': 'news', 'status': 'started', 'execution_allowed': False}
    jobs._record_job_status(state, result, datetime.now(ZoneInfo('UTC')).isoformat())
    digest = hashlib.sha256(raw).hexdigest()
    journal_event = {'status': 'extracted', 'receipt_id': 'c' * 32 if mode == 'wrong_receipt' else receipt,
                     'artifact_sha256': digest, 'pdfs': 1, 'execution_allowed': False}
    with patch.object(jobs.subprocess, 'run', return_value=SimpleNamespace(
            returncode=0, stdout='Started\n' + json.dumps(journal_event) + '\n')) as journal:
        if mode == 'tampered':
            with pytest.raises(ValueError, match='invalid_news_excerpt_evidence'):
                jobs._latest_excerpt_payload(state, state)
        else:
            actual = jobs._latest_excerpt_payload(state, state)
            if mode == 'valid':
                assert actual == jobs._work_excerpt_payload(path, digest)
            elif mode == 'not_ingested':
                assert actual == {}
            else:
                assert actual is None
    if mode in ('no_file', 'not_ingested'):
        journal.assert_not_called()
    else:
        assert 'quantdinger-research-news-pdf-paper-test.service' in journal.call_args.args[0]
        assert journal.call_args.kwargs['timeout'] == 8


def test_work_reads_new_ingestion_under_lock_and_waits_without_starting_a_worker(state, capsys):
    receipt = 'a' * 32
    captured = {'job': 'news', 'status': 'ingested', 'receipt_id': receipt, 'item_count': 1,
                'source_hash': 'b' * 64, 'execution_allowed': False}
    stamp = datetime.now(jobs.UTC).isoformat()
    with ExitStack() as ingest_lock:
        ingest_lock.enter_context(jobs.ops.operation_lock(state))
        jobs._record_job_status(state, {'job': 'news', 'status': 'started', 'execution_allowed': False}, stamp)

        def finish_ingestion(_):
            jobs._record_job_status(state, captured, stamp)
            ingest_lock.close()

        with (
            patch.object(jobs.ops.time, 'sleep', side_effect=finish_ingestion) as wait,
            patch.object(jobs.ops, 'compose') as compose,
            patch.object(jobs.subprocess, 'run') as run,
        ):
            assert jobs.run_job(state, 'work', excerpt_dir=state) == 0
        wait.assert_called_once()
        compose.assert_not_called()
        run.assert_not_called()
    assert json.loads(capsys.readouterr().out)['status'] == 'waiting_for_excerpt'
    assert jobs.load_job_status(state)['jobs']['work']['status'] == 'waiting_for_excerpt'

    artifact = b'accepted excerpt evidence'
    (state / (receipt + '.json')).write_bytes(artifact)
    digest = hashlib.sha256(artifact).hexdigest()
    proof = {'status': 'extracted', 'receipt_id': receipt, 'artifact_sha256': digest, 'pdfs': 1,
             'execution_allowed': False}
    journal = SimpleNamespace(returncode=0, stdout=json.dumps(proof))
    idle = SimpleNamespace(returncode=0, stdout='{"job":"work","status":"idle","execution_allowed":false}')
    with (
        patch.object(jobs.ops, 'compose', return_value=['fixture']),
        patch.object(jobs, 'cleanup_container', return_value=True),
        patch.object(jobs.subprocess, 'run', side_effect=[journal, idle]) as run,
    ):
        assert jobs.run_job(state, 'work', excerpt_dir=state) == 0
    assert run.call_count == 2
    payload = json.loads(run.call_args.kwargs['input'])
    assert payload == {'job': 'work', 'excerpt': base64.b64encode(artifact).decode(), 'excerpt_sha256': digest}


def test_work_uses_title_fallback_after_derivation_deadline(state, capsys):
    stamp = datetime.now(jobs.UTC)
    captured = {'job': 'news', 'status': 'ingested', 'receipt_id': 'a' * 32, 'item_count': 1,
                'source_hash': 'b' * 64, 'execution_allowed': False}
    jobs._record_job_status(state, captured, stamp.isoformat())
    idle = SimpleNamespace(returncode=0, stdout='{"job":"work","status":"idle","execution_allowed":false}')
    with (
        patch.object(jobs, 'datetime', wraps=datetime) as clock,
        patch.object(jobs.ops, 'compose', return_value=['fixture']),
        patch.object(jobs, 'cleanup_container', return_value=True),
        patch.object(jobs.subprocess, 'run', return_value=idle) as run,
    ):
        clock.now.return_value = stamp + timedelta(seconds=jobs.EXCERPT_DERIVATION_SECONDS + 1)
        assert jobs.run_job(state, 'work', excerpt_dir=state) == 0
    run.assert_called_once()
    assert json.loads(run.call_args.kwargs['input']) == {'job': 'work'}
    assert json.loads(capsys.readouterr().out)['status'] == 'idle'


def child(payload, hour=9, run_result=None, request_error=None, news_error=None, arbitrary_error=None,
          action_reminder=None):
    database = ModuleType('psycopg2'); database.connect = Mock(return_value=Mock())
    schema = ModuleType('app.ashare.schema'); schema.get_database_url = lambda:'private-test-dsn'
    research = ModuleType('app.ashare.research'); research.run_once = Mock(return_value=run_result)
    excerpts = ModuleType('app.ashare.research_excerpts')
    def verify(raw, expected):
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('unverified')
        return {'verified': True}
    excerpts.verify_excerpt_artifact = verify
    class Error(ValueError): pass
    research.ResearchError = Error
    store = Mock(); store.request_run.return_value = {'status':'queued','id':'a'*32}
    store.request_scheduled_run.return_value = store.request_run.return_value
    if request_error:
        store.request_run.side_effect = Error(request_error)
        store.request_scheduled_run.side_effect = Error(request_error)
    research.ResearchStore = Mock(return_value=store)
    news = ModuleType('app.ashare.news'); news.ingest_news = Mock(return_value={'receipt_id':'b'*32,'item_count':0,'source_hash':'c'*64})
    class NewsError(ValueError): pass
    news.NewsError = NewsError
    news.safe_error_code = lambda error: str(error) if isinstance(error, NewsError) and str(error) in jobs._news_errors() else 'news_operation_failed'
    if news_error: news.ingest_news.side_effect = NewsError(news_error)
    if arbitrary_error: news.ingest_news.side_effect = RuntimeError(arbitrary_error)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return datetime(2026,11,18,hour,0,tzinfo=ZoneInfo('Asia/Shanghai'))
    dt = ModuleType('datetime');dt.datetime = Clock
    action = ModuleType('app.ashare.action_reminders')
    action.send_due_reminder = action_reminder or Mock(return_value=None)
    proposals = ModuleType('app.ashare.strategy_proposals')
    proposals.propose_and_notify = Mock(return_value={'status': 'inactive_template'})
    stdout=io.StringIO()
    with patch.dict(sys.modules, {'psycopg2':database,'app.ashare.schema':schema,'app.ashare.research':research,
                                  'app.ashare.action_reminders':action,'app.ashare.strategy_proposals':proposals,
                                  'app.ashare.research_excerpts':excerpts,'app.ashare.news':news,'datetime':dt}), \
         patch.object(sys,'stdin',io.StringIO(json.dumps(payload))),redirect_stdout(stdout),pytest.raises(SystemExit) as exited:
        exec(compile(jobs.CONTAINER_SCRIPT,'<research-job>','exec'),{})
    assert 'private-test-dsn' not in stdout.getvalue()
    return exited.value.code,json.loads(stdout.getvalue()),research,news,database


def test_child_news_only_ingests_fixed_current_scope():
    code,result,research,news,_=child({'job':'news'})
    assert code == 0 and result['status'] == 'ingested'
    assert news.ingest_news.call_args.kwargs == {'topics':('ai','robotics','new_energy'),'timeout':45}
    research.run_once.assert_not_called(); research.ResearchStore.assert_not_called()


def test_child_work_calls_one_finite_worker_without_enqueue():
    code,result,research,news,_=child({'job':'work'},run_result={'status':'completed','id':'a'*32})
    assert code == 0 and result['status'] == 'completed'
    research.run_once.assert_called_once();research.ResearchStore.assert_not_called();news.ingest_news.assert_not_called()


@pytest.mark.parametrize('scoped', [True, False])
def test_child_checks_user_action_only_for_explicitly_scoped_worker(scoped):
    action = {'kind': 'review_daily_security_rules', 'version_id': 'b' * 32, 'notice': 'unknown'}
    check = Mock(return_value=action)
    payload = {'job': 'work', 'proposal_scope': ['user_7', 'paper']} if scoped else {'job': 'work'}
    code, result, _, _, db = child(
        payload, run_result={'status': 'no_evidence', 'id': 'a' * 32}, action_reminder=check,
    )
    assert code == 0 and result['status'] == 'no_evidence' and result['execution_allowed'] is False
    if scoped:
        check.assert_called_once_with(db.connect.return_value, ('user_7', 'paper'))
        assert result['action_reminder'] == action
        output = SimpleNamespace(stdout=json.dumps(result), returncode=0)
        assert jobs._safe_result(output, 'work')['action_reminder'] == action
        result['action_reminder']['notice'] = 'leaked-secret'
        with pytest.raises(ValueError, match='invalid_action_reminder_status'):
            jobs._safe_result(SimpleNamespace(stdout=json.dumps(result), returncode=0), 'work')
    else:
        check.assert_not_called()
        assert 'action_reminder' not in result


@pytest.mark.parametrize('valid', [True, False])
def test_child_verifies_excerpt_before_running_one_claim(valid):
    raw = b'fixture reference'
    payload = {'job': 'work', 'excerpt': base64.b64encode(raw).decode(),
               'excerpt_sha256': hashlib.sha256(raw).hexdigest() if valid else '0' * 64}
    code, result, research, news, _ = child(payload, run_result={'status': 'completed', 'id': 'a' * 32})
    assert result['execution_allowed'] is False
    if valid:
        assert code == 0 and result['status'] == 'completed'
        research.run_once.assert_called_once()
        assert research.run_once.call_args.kwargs == {'excerpt_receipt': {'verified': True}}
    else:
        assert code == 1 and result['error'] == 'invalid_news_excerpt_evidence'
        research.run_once.assert_not_called()
    research.ResearchStore.assert_not_called()
    news.ingest_news.assert_not_called()


def test_daily_request_explicit_scope_same_day_key():
    payload=jobs._request('user:7','paper',None,True)
    code,result,research,news,_=child(payload)
    assert code == 0 and result['status'] == 'queued'
    assert research.ResearchStore.call_args.args[1:] == ('user:7','paper')
    research.ResearchStore.return_value.request_scheduled_run.assert_called_once_with(datetime(2026, 11, 18).date())
    research.run_once.assert_not_called();news.ingest_news.assert_not_called()


def test_daily_request_before_window_cannot_charge_or_query():
    code,result,research,news,database=child(jobs._request('user:7','paper',None,True),hour=7)
    assert code == 0 and result['status'] == 'skipped_before_window'
    database.connect.assert_not_called();research.run_once.assert_not_called()


@pytest.mark.parametrize('reason,status',[('research_already_active','skipped_active'),('research_daily_limit','skipped_daily_limit')])
def test_daily_request_budget_is_terminal_skip(reason,status):
    code,result,research,_,_=child(jobs._request('user:7','paper',None,True),request_error=reason)
    assert code == 0 and result['status'] == status
    research.ResearchStore.return_value.request_scheduled_run.assert_called_once()


@pytest.mark.parametrize('payload', [
    {'job': 'work', 'tenant_id': 'user:7'}, {'job': 'request'}, {'job': 'news', 'date': '2020-01-01'},
    {'job': 'work', 'proposal_scope': ['user_7', 'live']},
    {'job': 'work', 'proposal_scope': ['user_9223372036854775808', 'paper']},
    {'job': 'work', 'proposal_scope': {'tenant_id': 'user_7', 'account_id': 'paper'}},
    {'job': 'request', 'proposal_scope': ['user_7', 'paper']},
])
def test_child_rejects_scope_injection(payload):
    code,result,research,news,db=child(payload)
    assert code == 1 and result['status'] == 'failed'
    db.connect.assert_not_called();research.run_once.assert_not_called();news.ingest_news.assert_not_called()


@pytest.mark.parametrize('error,expected',[('news_fetch_timeout','news_fetch_timeout'),('private-credential-value','news_operation_failed')])
def test_news_typed_errors_use_shared_strict_allowlist(error, expected):
    code,result,_,news,_=child({'job':'news'},news_error=error)
    assert code == 1 and result['error'] == expected
    assert jobs._safe_result(SimpleNamespace(stdout=json.dumps(result),returncode=1),'news')['error'] == expected
    news.ingest_news.assert_called_once()


def test_arbitrary_external_errors_never_disclose_even_when_text_matches_a_code():
    for message in ('secret-from-provider','news_fetch_timeout'):
        code,result,_,_,_=child({'job':'news'},arbitrary_error=message)
        assert code == 1 and result['error'] == 'research_job_failed'


@pytest.mark.parametrize('status,error',[('no_evidence','no_news_evidence'),('failed','model_timeout'),('needs_model_configuration','model_not_supported')])
def test_work_reports_stable_run_error_without_retry(status,error):
    code,result,research,_,_=child({'job':'work'},run_result={'status':status,'id':'a'*32,'error_code':error})
    assert code == (1 if status == 'failed' else 0)
    assert result['error'] == error
    assert jobs._safe_result(SimpleNamespace(stdout=json.dumps(result),returncode=code),'work')['error'] == error
    research.run_once.assert_called_once()


def test_untrusted_run_error_is_replaced():
    _,result,_,_,_=child({'job':'work'},run_result={'status':'failed','id':'a'*32,'error_code':'private-key'})
    assert result['error'] == 'research_job_failed'


def test_typed_request_error_is_safe_and_visible():
    _,result,_,_,_=child(jobs._request('user:7','paper','test-request',False),request_error='paper_account_required')
    assert result['error'] == 'paper_account_required'


def test_status_file_is_atomic_private_and_keeps_each_job(state):
    outcomes = [{'job':'news','status':'failed','error':'news_fetch_timeout','execution_allowed':False},
                {'job':'work','status':'no_evidence','id':'a'*32,'error':'no_news_evidence','execution_allowed':False}]
    with patch.object(jobs.ops,'compose',return_value=['fixture']), patch.object(jobs,'cleanup_container',return_value=True), \
         patch.object(jobs.subprocess,'run') as run:
        for outcome in outcomes:
            run.return_value=SimpleNamespace(stdout=json.dumps(outcome),returncode=1 if outcome['status']=='failed' else 0)
            jobs.run_job(state,outcome['job'])
    path=state/jobs.STATUS_NAME
    assert path.stat().st_mode & 0o777 == 0o600
    saved=jobs.load_job_status(state)
    assert saved['schema_version'] == 1 and saved['jobs']['news']['error'] == 'news_fetch_timeout'
    assert saved['jobs']['work']['error'] == 'no_news_evidence'
    assert saved['jobs']['work']['id'] == 'a'*32
    assert datetime.fromisoformat(saved['updated_at']).utcoffset() is not None
    assert not list(state.glob('.research-job-status.json.*'))
    assert 'raw_response' not in path.read_text()


def test_status_records_started_before_call_and_unknown_on_signal(state):
    def interrupted(*args,**kwargs):
        assert jobs.load_job_status(state)['jobs']['work']['status'] == 'started'
        raise SystemExit(143)
    with patch.object(jobs.ops,'compose',return_value=['fixture']), patch.object(jobs,'cleanup_container',return_value=True), \
         patch.object(jobs.subprocess,'run',side_effect=interrupted):
        with pytest.raises(SystemExit): jobs.run_job(state,'work')
    assert jobs.load_job_status(state)['jobs']['work']['error'] == 'research_job_unknown'


def test_bad_status_file_blocks_run_without_overwriting_evidence(state):
    path=state/jobs.STATUS_NAME
    path.write_text('{"secret":"private"}')
    with patch.object(jobs.subprocess,'run') as run, pytest.raises(ValueError): jobs.run_job(state,'work')
    run.assert_not_called()
    assert path.read_text() == '{"secret":"private"}'


def test_status_loader_rejects_arbitrary_error_and_extra_fields(state):
    started=datetime.now(ZoneInfo('UTC')).isoformat()
    jobs._record_job_status(state,{'job':'work','status':'idle','execution_allowed':False},started)
    value=json.loads((state/jobs.STATUS_NAME).read_text())
    value['jobs']['work']['error']='private-data'
    (state/jobs.STATUS_NAME).write_text(json.dumps(value))
    with pytest.raises(ValueError): jobs.load_job_status(state)
