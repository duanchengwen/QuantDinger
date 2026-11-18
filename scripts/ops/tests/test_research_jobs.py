"""Finite scheduler tests use fake process boundaries only."""
import importlib.util
import io
import json
import signal
import subprocess
import sys
from contextlib import ExitStack, redirect_stdout
from datetime import datetime
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
        assert jobs.templates(state, folder, 'user:7', 'paper') == 0
    run.assert_not_called()
    assert len(list(folder.iterdir())) == 6
    news = (folder/'quantdinger-research-news-paper-test.timer').read_text()
    work = (folder/'quantdinger-research-work-paper-test.timer').read_text()
    request = (folder/'quantdinger-research-request-paper-test.service').read_text()
    worker = (folder/'quantdinger-research-work-paper-test.service').read_text()
    assert '07..22:00/30:00 Asia/Shanghai' in news
    assert 'OnUnitActiveSec=2min' in work
    assert 'user:7' in request and '--daily' in request and '--account-id' in request
    assert 'After=quantdinger-research-news-paper-test.service\n' in request
    assert 'After=quantdinger-research-news-paper-test.service quantdinger-research-request-paper-test.service\n' in worker
    for path in folder.glob('*.service'):
        assert 'Restart=no' in path.read_text() and 'TimeoutStartSec=' in path.read_text()
        assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError): jobs.templates(state, folder, 'user:7', 'paper')


def child(payload, hour=9, run_result=None, request_error=None, news_error=None, arbitrary_error=None):
    database = ModuleType('psycopg2'); database.connect = Mock(return_value=Mock())
    schema = ModuleType('app.ashare.schema'); schema.get_database_url = lambda:'private-test-dsn'
    research = ModuleType('app.ashare.research'); research.run_once = Mock(return_value=run_result)
    class Error(ValueError): pass
    research.ResearchError = Error
    store = Mock(); store.request_run.return_value = {'status':'queued','id':'a'*32}
    if request_error: store.request_run.side_effect = Error(request_error)
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
    stdout=io.StringIO()
    with patch.dict(sys.modules, {'psycopg2':database,'app.ashare.schema':schema,'app.ashare.research':research,'app.ashare.news':news,'datetime':dt}), \
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


def test_daily_request_explicit_scope_same_day_key():
    payload=jobs._request('user:7','paper',None,True)
    code,result,research,news,_=child(payload)
    assert code == 0 and result['status'] == 'queued'
    assert research.ResearchStore.call_args.args[1:] == ('user:7','paper')
    research.ResearchStore.return_value.request_run.assert_called_once_with('scheduled-20261118')
    research.run_once.assert_not_called();news.ingest_news.assert_not_called()


def test_daily_request_before_window_cannot_charge_or_query():
    code,result,research,news,database=child(jobs._request('user:7','paper',None,True),hour=7)
    assert code == 0 and result['status'] == 'skipped_before_window'
    database.connect.assert_not_called();research.run_once.assert_not_called()


@pytest.mark.parametrize('reason,status',[('research_already_active','skipped_active'),('research_daily_limit','skipped_daily_limit')])
def test_daily_request_budget_is_terminal_skip(reason,status):
    code,result,research,_,_=child(jobs._request('user:7','paper',None,True),request_error=reason)
    assert code == 0 and result['status'] == status
    research.ResearchStore.return_value.request_run.assert_called_once()


@pytest.mark.parametrize('payload',[{'job':'work','tenant_id':'user:7'},{'job':'request'},{'job':'news','date':'2020-01-01'}])
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
