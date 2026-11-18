"""Real PostgreSQL human API security and configuration tests."""
from contextlib import closing
import os
import uuid
from pathlib import Path
from flask import Flask
import pytest
import psycopg2
from app.ashare.web_api import blueprint
from app.ashare.web_store import WebStore

@pytest.fixture
def client(monkeypatch):
    dsn = os.getenv("TEST_ASHARE_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_ASHARE_DATABASE_URL required")
    monkeypatch.setenv("ASHARE_DATABASE_URL", dsn)
    monkeypatch.setenv("SECRET_KEY", "test-encryption-secret-longer-than-thirty-two")
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        from app.ashare.schema import MIGRATIONS
        for name in MIGRATIONS:
            cur.execute((Path(__file__).parents[2] / "migrations" / name).read_text())
    user = int(uuid.uuid4().hex[:8], 16)
    def verify(token):
        return {"user_id": user + (token == "other"), "_verified_user_role": "viewer" if token == "viewer" else "user", "_verified_username": "test"}
    monkeypatch.setattr("app.utils.auth.verify_token", verify)
    app = Flask(__name__)
    app.register_blueprint(blueprint)
    app.config["TESTING"] = True
    result = app.test_client()
    result.test_user = user
    return result

def call(client, path, data=None, token="user", method="POST"):
    return client.open("/api/ashare-paper" + path, method=method, json=data, headers={"Authorization": "Bearer " + token})

def test_auth_viewer_tenant_and_stale_worker(client):
    assert client.get("/api/ashare-paper/state").status_code == 401
    assert call(client, "/initialize", {"initial_cash": "10000"}, token="viewer").status_code == 403
    assert call(client, "/initialize", {"initial_cash": "10000"}).json["code"] == 1
    assert call(client, "/state", method="GET").json["data"]["account"]["cash"] == "10000.00"
    assert call(client, "/state", method="GET", token="other").json["data"]["account"] is None
    assert call(client, "/initialize", {"initial_cash": "10000", "tenant_id": "user_99"}).status_code == 400
    assert call(client, "/orders/1/cancel", {}, token="other").status_code == 400
    order = {"client_order_id":"one","symbol":"sz.000001","side":"buy","quantity":100,"limit_price":"10"}
    assert call(client, "/orders", order).json["msg"] == "paper_worker_stale"
    assert call(client, "/state", method="GET").json["data"]["orders"] == []

def test_settings_validation(client):
    call(client, "/initialize", {"initial_cash": "10000"})
    assert call(client, "/settings", {"symbols":["sh.688001"],"interval_seconds":5}, method="PUT").status_code == 400
    assert call(client, "/settings", {"symbols":["sz.000001"],"interval_seconds":5}, method="PUT").status_code == 200

@pytest.mark.parametrize("path", ["/daily-reports?limit=5", "/universe?trading_day=2026-11-16", "/rules/suggestions?"])
def test_official_frontend_get_cache_parameter(client, path):
    call(client, "/initialize", {"initial_cash": "10000"})
    path += "&" if "?" in path else "?"
    assert call(client, path + "_t=1794916800000", method="GET").status_code == 200
    assert call(client, path + "unexpected=yes", method="GET").status_code == 400

def test_notification_secrets_encrypted_and_redacted(client):
    call(client, "/initialize", {"initial_cash": "10000"})
    payload = {"webhook_url":"https://open.feishu.cn/open-apis/bot/v2/hook/test-secret-unique", "signing_secret":"never-echo-this"}
    assert call(client, "/notifications/config", payload).json["data"] == {"configured":True,"signing_enabled":True}
    response = call(client, "/state", method="GET")
    assert "test-secret-unique" not in response.text and "never-echo-this" not in response.text
    with closing(psycopg2.connect(os.environ["TEST_ASHARE_DATABASE_URL"])) as conn:
        store = WebStore(conn,"user_"+str(client.test_user))
        assert store.notification_credentials() == payload
        with conn.cursor() as cur:
            cur.execute("SELECT notifications_encrypted FROM qd_ashare_web WHERE tenant_id=%s",(store.tenant_id,))
            encrypted=cur.fetchone()[0]
            assert "test-secret-unique" not in encrypted and "never-echo-this" not in encrypted
    assert call(client,"/state",method="GET",token="other").json["data"]["notifications"]["configured"] is False
    assert call(client,"/notifications/config",{"webhook_url":""}).json["data"]["configured"] is False

def test_bad_webhook_rejected(client):
    call(client,"/initialize",{"initial_cash":"10000"})
    assert call(client,"/notifications/config",{"webhook_url":"https://evil.example/hook"}).status_code == 400

def test_fresh_order_review_idempotency_and_cross_user_cancel(client, monkeypatch):
    from datetime import datetime, timezone
    from app.ashare import web_api, web_store
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026,11,17,2,0,5,tzinfo=timezone.utc)
    monkeypatch.setattr(web_api,"datetime",Clock)
    monkeypatch.setattr(web_store,"datetime",Clock)
    call(client,"/initialize",{"initial_cash":"10000"})
    quote={"symbol":"sz.000001","name":"平安银行","exchange_time":"2026-11-17T02:00:04+00:00","received_at":"2026-11-17T02:00:04+00:00","last":"10","bid":"9.99","ask":"10","bid_size":100,"ask_size":100,"previous_close":"10","volume":1000,"open":"10","high":"10.1","low":"9.9","limit_up":"11","limit_down":"9","quote_id":"api-fixture","evidence_sha256":"a"*64}
    with closing(psycopg2.connect(os.environ["TEST_ASHARE_DATABASE_URL"])) as conn:
        WebStore(conn,"user_"+str(client.test_user)).observe([quote],{"state":"healthy","observed_at":"2026-11-17T02:00:04+00:00"})
    rule={"symbol":"sz.000001","trading_day":"2026-11-17","suspended":False,"st":False,"evidence":"synthetic test evidence","limit_up":"11","limit_down":"9"}
    assert call(client,"/rules/review",{**rule,"limit_up":"12"}).json["msg"] == "daily_price_band_mismatch"
    reviewed=call(client,"/rules/review",rule)
    assert reviewed.status_code == 200
    assert reviewed.json["data"]["quote_evidence"]["evidence_sha256"] == "a"*64
    assert reviewed.json["data"]["reviewed_by"] == client.test_user
    audit=call(client,"/state",method="GET").json["data"]["audit"]
    assert any(event["event"] == "daily_rules_reviewed" for event in audit)
    order={"client_order_id":"safe-id","symbol":"sz.000001","side":"buy","quantity":100,"limit_price":"10"}
    response=call(client,"/orders",order)
    assert response.status_code == 200,response.json
    order_id=response.json["data"]["id"]
    assert call(client,"/settings",{"symbols":["sh.600000"],"interval_seconds":5},method="PUT").json["msg"] == "cannot_remove_active_symbols"
    assert call(client,"/orders",order).json["data"]["id"] == order_id
    assert call(client,"/orders",{**order,"quantity":200}).status_code == 400
    call(client,"/initialize",{"initial_cash":"10000"},token="other")
    assert call(client,f"/orders/{order_id}/cancel",{},token="other").status_code == 400
    assert call(client,f"/orders/{order_id}/cancel",{}).json["data"]["status"] == "cancelled"
    assert call(client,"/state",method="GET").json["data"]["account"]["reserved_cash"] == "0.00"

def test_documented_routes_match_registered_routes():
    import json
    spec=json.loads((Path(__file__).parents[3]/"docs/downstream/paper-web-openapi.json").read_text())
    app=Flask(__name__)
    app.register_blueprint(blueprint)
    actual={(rule.rule.replace("/api/ashare-paper", "").replace("<int:order_id>","{order_id}").replace("<board_code>","{board_code}").replace("<run_id>","{run_id}").replace("<version_id>","{version_id}"),method.lower()) for rule in app.url_map.iter_rules() if rule.rule.startswith("/api/ashare-paper") for method in rule.methods if method not in {"HEAD","OPTIONS"}}
    documented={(path,method) for path,operations in spec["paths"].items() for method in operations}
    assert actual == documented


def test_future_or_failed_worker_blocks_new_orders(client):
    from datetime import datetime, timezone, timedelta
    call(client,"/initialize",{"initial_cash":"10000"})
    order={"client_order_id":"worker-gate","symbol":"sz.000001","side":"buy","quantity":100,"limit_price":"10"}
    with closing(psycopg2.connect(os.environ["TEST_ASHARE_DATABASE_URL"])) as conn:
        store=WebStore(conn,"user_"+str(client.test_user))
        store.observe([],{"state":"healthy","observed_at":(datetime.now(timezone.utc)+timedelta(seconds=5)).isoformat()})
        assert call(client,"/orders",order).json["msg"] == "paper_worker_stale"
        store.observe([],{"state":"failed","observed_at":datetime.now(timezone.utc).isoformat()})
        assert call(client,"/orders",order).json["msg"] == "paper_worker_not_healthy"


def test_daily_reports_are_read_only_bounded_and_tenant_scoped(client):
    call(client, '/initialize', {'initial_cash': '10000'})
    with psycopg2.connect(os.environ['TEST_ASHARE_DATABASE_URL']) as conn, conn.cursor() as cur:
        cur.execute((Path(__file__).parents[2] / 'migrations/20261117_ashare_operations.sql').read_text())
        cur.execute("INSERT INTO qd_ashare_daily_reports(tenant_id,account_id,trading_day,generated_at,report) VALUES(%s,'paper','2026-11-16',now(),%s::jsonb)",
                    ('user_' + str(client.test_user), '{"cash_end":"10000.00","equity_end":null,"reconciliation":{"ok":true}}'))
    result = call(client, '/daily-reports', method='GET')
    assert result.status_code == 200 and len(result.json['data']) == 1
    assert result.json['data'][0]['report']['equity_end'] is None
    assert call(client, '/daily-reports', method='GET', token='viewer').status_code == 200
    assert call(client, '/daily-reports', method='GET', token='other').json['data'] == []
    for query in ['limit=0', 'limit=91', 'limit=bad', 'tenant_id=user_1']:
        assert call(client, '/daily-reports?' + query, method='GET').status_code == 400
    assert client.get('/api/ashare-paper/daily-reports').status_code == 401


def test_account_lock_timeout_returns_service_error_then_recovers(client, monkeypatch):
    call(client, '/initialize', {'initial_cash': '10000'})
    connect = psycopg2.connect
    def bounded_connect(dsn, **kwargs):
        assert 'lock_timeout=3000' in kwargs['options']
        return connect(dsn, connect_timeout=5, options='-c statement_timeout=1000 -c lock_timeout=30')
    with closing(connect(os.environ['TEST_ASHARE_DATABASE_URL'])) as blocker:
        with blocker.cursor() as cur:
            cur.execute('SELECT * FROM qd_ashare_accounts WHERE tenant_id=%s FOR UPDATE', ('user_' + str(client.test_user),))
        monkeypatch.setattr(psycopg2, 'connect', bounded_connect)
        response = call(client, '/state', method='GET')
        assert response.status_code == 503 and response.json['msg'] == 'paper_database_unavailable'
        blocker.rollback()
        response = call(client, '/state', method='GET')
        assert response.status_code == 200 and response.json['data']['account']['cash'] == '10000.00'


def seed_review_day(client, monkeypatch):
    from datetime import datetime, timezone
    from app.ashare import web_api, web_store
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 17, 2, 0, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(web_api, 'datetime', Clock)
    monkeypatch.setattr(web_store, 'datetime', Clock)
    call(client, '/initialize', {'initial_cash': '10000'})
    call(client, '/settings', {'symbols': ['sz.000001', 'sh.600000'], 'interval_seconds': 5}, method='PUT')
    quotes = [{'symbol': symbol, 'name': 'fixture', 'exchange_time': '2026-11-17T02:00:04+00:00',
               'received_at': '2026-11-17T02:00:04+00:00', 'limit_down': '9', 'limit_up': '11',
               'evidence_sha256': 'a' * 64} for symbol in ['sz.000001', 'sh.600000']]
    with closing(psycopg2.connect(os.environ['TEST_ASHARE_DATABASE_URL'])) as conn:
        WebStore(conn, 'user_' + str(client.test_user)).observe(quotes, {'state': 'healthy', 'observed_at': '2026-11-17T02:00:04+00:00'})
    return [{'symbol': quote['symbol'], 'trading_day': '2026-11-17', 'suspended': False,
             'st': False, 'evidence': 'Synthetic dated operator source 2026-11-17',
             'limit_up': '11', 'limit_down': '9'} for quote in quotes]


def test_review_suggestions_keep_unknowns_and_never_write_rules(client, monkeypatch):
    seed_review_day(client, monkeypatch)
    monkeypatch.setattr('app.ashare.review_assistance.dated_status', lambda *args: None)
    response = call(client, '/rules/suggestions', method='GET')
    assert response.status_code == 200
    data = response.json['data']
    assert data['requires_human_confirmation'] is True and data['auto_approval'] is False
    assert len(data['rows']) == 2
    assert data['rows'][0]['st'] is None and data['rows'][0]['suspended'] is None
    assert set(data['rows'][0]['unknown_fields']) == {'st', 'suspended'}
    assert call(client, '/state', method='GET').json['data']['rules'] == {}
    assert call(client, '/rules/suggestions?tenant_id=user_1', method='GET').status_code == 400
    assert client.get('/api/ashare-paper/rules/suggestions').status_code == 401
    assert call(client, '/rules/suggestions', method='GET', token='other').json['data']['rows'][0]['quote_evidence'] is None


def test_batch_review_is_atomic_explicit_and_per_tenant(client, monkeypatch):
    rules = seed_review_day(client, monkeypatch)
    monkeypatch.setattr('app.ashare.review_assistance.dated_status', lambda *args: None)
    assert call(client, '/rules/review-batch', {'rules': rules}).status_code == 400
    assert call(client, '/rules/review-batch', {'rules': rules, 'confirmed': 'true'}).status_code == 400
    assert call(client, '/rules/review-batch', {'rules': rules, 'confirmed': True}, token='viewer').status_code == 403
    bad = [rules[0], {**rules[1], 'limit_up': '12'}]
    assert call(client, '/rules/review-batch', {'rules': bad, 'confirmed': True}).json['msg'] == 'daily_price_band_mismatch'
    assert call(client, '/state', method='GET').json['data']['rules'] == {}
    assert call(client, '/rules/review-batch', {'rules': [rules[0], rules[0]], 'confirmed': True}).status_code == 400
    result = call(client, '/rules/review-batch', {'rules': rules, 'confirmed': True})
    assert result.status_code == 200 and len(result.json['data']) == 2
    assert all(row['approval_method'] == 'explicit_human_review' for row in result.json['data'])
    assert len(call(client, '/state', method='GET').json['data']['rules']) == 2
    assert call(client, '/state', method='GET', token='other').json['data']['rules'] == {}
    previous = [{**rule, 'trading_day': '2026-11-16'} for rule in rules]
    assert call(client, '/rules/review-batch', {'rules': previous, 'confirmed': True}).status_code == 400


def test_negative_dated_source_cannot_be_overridden_by_human_false(client, monkeypatch):
    rules = seed_review_day(client, monkeypatch)
    monkeypatch.setattr('app.ashare.review_assistance.dated_status', lambda *args: {'st': True, 'suspended': False})
    candidates = call(client, '/rules/suggestions', method='GET').json['data']['rows']
    assert all('source_reports_ineligible_security' in row['blockers'] for row in candidates)
    assert call(client, '/rules/review', rules[0]).json['msg'] == 'source_reports_ineligible_security'
    assert call(client, '/rules/review-batch', {'rules': rules, 'confirmed': True}).json['msg'] == 'source_reports_ineligible_security'
    assert call(client, '/state', method='GET').json['data']['rules'] == {}


def test_universe_request_bounds_and_authentication(client, monkeypatch):
    from app.ashare.securities import SecuritiesStore
    seen = []
    def universe(self, day, **kwargs):
        seen.append((day.isoformat(), kwargs))
        return {'trading_day': day.isoformat(), 'members': [], 'status': 'not_observed', 'total': 0}
    monkeypatch.setattr(SecuritiesStore, 'universe', universe)
    result = call(client, '/universe?trading_day=2026-11-16&limit=100&offset=100', method='GET', token='viewer')
    assert result.status_code == 200 and result.json['data']['status'] == 'not_observed'
    assert seen[0][1]['limit'] == 100 and seen[0][1]['offset'] == 100
    for query in ['trading_day=2099-01-01', 'trading_day=2026-11-16&limit=501', 'trading_day=2026-11-16&offset=-1', 'trading_day=2026-11-16&tenant_id=x', 'trading_day=bad']:
        assert call(client, '/universe?' + query, method='GET').status_code == 400
    assert client.get('/api/ashare-paper/universe?trading_day=2026-11-16').status_code == 401


def test_research_preferences_persist_per_account_without_activating_strategy(client):
    call(client, '/initialize', {'initial_cash': '10000'})
    settings = {'symbols': ['sz.000001'], 'interval_seconds': 5,
                'research_preferences': {'topics': ['ai', 'robotics', 'new_energy'], 'follow_current_events': True,
                                         'custom_topics': 'Battery storage'}}
    assert call(client, '/settings', settings, method='PUT').status_code == 200
    saved = call(client, '/state', method='GET').json['data']
    assert saved['settings']['research_preferences'] == settings['research_preferences']
    assert 'strategy' not in saved['settings']
    assert call(client, '/state', method='GET', token='other').json['data']['settings'].get('research_preferences') is None
    assert call(client, '/settings', {key: value for key, value in settings.items() if key != 'research_preferences'}, method='PUT').json['data']['research_preferences'] == settings['research_preferences']
    for preferences in [{'topics': ['fake']}, {'topics': ['ai', 'ai']}, {'follow_current_events': 'true'}, {'custom_topics': 'x' * 241}, {'auto_approve': True}]:
        assert call(client, '/settings', {**settings, 'research_preferences': preferences}, method='PUT').status_code == 400


def test_corrupt_source_stays_blocked_without_fallback(client, monkeypatch):
    rules = seed_review_day(client, monkeypatch)
    def broken_source(*args):
        raise ValueError('security_evidence_hash_mismatch')
    monkeypatch.setattr('app.ashare.review_assistance.dated_status', broken_source)
    candidates = call(client, '/rules/suggestions', method='GET').json['data']['rows']
    assert all('security_evidence_unavailable' in row['blockers'] for row in candidates)
    assert call(client, '/rules/review', rules[0]).status_code == 400
    assert call(client, '/state', method='GET').json['data']['rules'] == {}


def test_research_http_auth_isolation_idempotency_and_no_execution(client, monkeypatch):
    from app.ashare import research
    monkeypatch.setattr(research, '_configuration', lambda: {
        'model_configured': False, 'provider': 'deepseek', 'model': 'deepseek-flash',
        'blocked_reason': 'needs_model_configuration'})
    paths = ['/news', '/research/status', '/research/runs', '/research/runs/' + 'a'*32]
    for path in paths:
        assert client.get('/api/ashare-paper' + path).status_code == 401
    assert call(client, '/research/runs', {'idempotency_key': 'test_request'}, token='viewer').status_code == 403
    assert call(client, '/research/runs', {'idempotency_key': 'test_request'}).json['msg'] == 'paper_account_required'
    call(client, '/initialize', {'initial_cash': '10000'})
    call(client, '/settings', {'symbols': ['sz.000001'], 'interval_seconds': 5,
         'research_preferences': {'topics': ['ai']}}, method='PUT')
    response = call(client, '/research/runs', {'idempotency_key': 'test_request'})
    assert response.status_code == 200, response.json
    run = response.json['data']
    assert run['status'] == 'queued' and run['execution_allowed'] is False
    assert run['approval_status'] == 'not_submitted'
    assert call(client, '/research/runs', {'idempotency_key': 'test_request'}).json['data']['id'] == run['id']
    assert call(client, '/research/runs', {'idempotency_key': 'second_request'}).json['msg'] == 'research_already_active'
    assert call(client, '/research/runs/' + run['id'], method='GET').json['data']['id'] == run['id']
    assert call(client, '/research/runs/' + run['id'], token='other', method='GET').json['msg'] == 'research_run_not_found'
    assert call(client, '/research/runs', token='other', method='GET').json['data']['items'] == []
    status = call(client, '/research/status', method='GET').json['data']
    assert status['execution_allowed'] is False and status['latest_run']['id'] == run['id']
    assert call(client, '/state', method='GET').json['data']['orders'] == []
    for private in ('raw_response', 'model_output', 'claim_token', 'tenant_id', 'idempotency_key'):
        assert private not in run


@pytest.mark.parametrize('path', ['/news?limit=51', '/news?limit=1&limit=2',
    '/research/runs?limit=21', '/research/status?tenant_id=someone',
    '/research/status?_t=1&_t=2', '/research/runs/not-a-uuid', '/research/runs?limit=-1'])
def test_research_http_bounded_query(client, path):
    assert call(client, path, method='GET').status_code == 400


@pytest.mark.parametrize('payload', [{}, {'idempotency_key': 'short'},
    {'idempotency_key': 'unsafe.dot'}, {'idempotency_key': 'valid_key', 'tenant_id': 'user_1'},
    {'idempotency_key': 'valid_key', 'approve': True}, {'idempotency_key': 12345678}])
def test_research_http_rejects_untrusted_fields(client, payload):
    assert call(client, '/research/runs', payload).status_code == 400


def test_research_evidence_errors_are_redacted(client, monkeypatch):
    from app.ashare import research
    def fail(self):
        raise research.ResearchError('private-path-or-key-should-not-escape')
    monkeypatch.setattr(research.ResearchStore, 'status', fail)
    response = call(client, '/research/status', method='GET')
    assert response.status_code == 503
    assert response.json['msg'] == 'research_evidence_unavailable'
    assert 'private-path' not in response.text


def _strategy_config(**changes):
    return {"mode": "paper", "kind": "buy_once_below", "symbol": "sz.000001",
            "quantity": 100, "threshold": "10", "limit_price": "9.99", "max_cash": "1100", **changes}


def test_immutable_strategy_version_human_approval_and_revocation(client):
    base = "/strategy-versions"
    request = {"config": _strategy_config(), "request_key": "strategy-request-1"}
    assert client.get("/api/ashare-paper" + base).status_code == 401
    assert call(client, base, request, token="viewer").status_code == 403
    assert call(client, base, request).json["msg"] == "paper_account_required"
    assert call(client, "/initialize", {"initial_cash": "10000"}).status_code == 200
    draft = call(client, base, request).json["data"]
    assert draft["status"] == "draft"
    assert draft["execution_allowed"] is False and draft["created_by"] == client.test_user
    assert draft["config"]["threshold"] == "10.00"
    assert call(client, base, request).json["data"] == draft
    conflict = {**request, "config": _strategy_config(threshold="10.01")}
    assert call(client, base, conflict).json["msg"] == "strategy_request_key_conflict"
    assert call(client, base + "?limit=51", method="GET").status_code == 400
    assert call(client, base, method="GET").json["data"]["versions"] == [draft]
    assert call(client, base, token="other", method="GET").json["data"]["versions"] == []

    decision_path = f"{base}/{draft['id']}/decision"
    approval = {"config_sha256": draft["config_sha256"], "decision": "approved", "confirmed": True}
    assert call(client, decision_path, approval, token="viewer").status_code == 403
    assert call(client, "/initialize", {"initial_cash": "5000"}, token="other").status_code == 200
    assert call(client, decision_path, approval, token="other").json["msg"] == "strategy_version_not_found"
    assert call(client, decision_path, {**approval, "confirmed": 1}).json["msg"] == "explicit_human_confirmation_required"
    assert call(client, decision_path, {**approval, "config_sha256": "0" * 64}).json["msg"] == "strategy_version_hash_mismatch"
    assert call(client, decision_path, {**approval, "actor_user_id": client.test_user + 1}).status_code == 400

    approved = call(client, decision_path, approval).json["data"]
    assert approved["status"] == "approved" and approved["approved_by"] == client.test_user
    assert approved["execution_allowed"] is False
    assert call(client, decision_path, approval).json["data"] == approved
    other_draft = call(client, base, {**request, "request_key": "strategy-request-2"}).json["data"]
    other_decision = f"{base}/{other_draft['id']}/decision"
    assert call(client, other_decision, approval).json["msg"] == "another_strategy_version_approved"

    revocation = {**approval, "decision": "revoked"}
    revoked = call(client, decision_path, revocation).json["data"]
    assert revoked["status"] == "revoked" and revoked["revoked_by"] == client.test_user
    assert revoked["config_sha256"] == draft["config_sha256"] and revoked["approved_at"] == approved["approved_at"]
    assert call(client, decision_path, revocation).json["data"] == revoked
    assert call(client, decision_path, approval).json["msg"] == "invalid_strategy_state_transition"
    assert call(client, other_decision, approval).json["data"]["status"] == "approved"
    activation = {**approval, "decision": "activated"}
    assert call(client, other_decision, activation, token="viewer").status_code == 403
    assert call(client, other_decision, {**activation, "config_sha256": "0" * 64}).status_code == 400
    enabled = call(client, other_decision, activation).json["data"]
    assert enabled["execution_allowed"] is True and enabled["activated_by"] == client.test_user
    assert call(client, other_decision, activation).json["data"] == enabled
    override = {"client_order_id": "managed-strategy-" + other_draft["id"],
                "symbol": "sz.000001", "side": "buy", "quantity": 100, "limit_price": "9.99"}
    assert call(client, "/orders", override).json["msg"] == "reserved_strategy_order_key"
    account = call(client, "/state", method="GET").json["data"]
    assert account["orders"] == [] and account["account"]["cash"] == "10000.00"


@pytest.mark.parametrize("config", [
    _strategy_config(mode="live"),
    _strategy_config(kind="execute_code"),
    _strategy_config(broker="anything"),
    _strategy_config(quantity=True),
    _strategy_config(quantity=101),
    _strategy_config(limit_price="1e1"),
    _strategy_config(max_cash="999"),
    _strategy_config(symbol="sz.000002"),
])
def test_strategy_draft_rejects_unsupported_or_unbounded_config(client, config):
    call(client, "/initialize", {"initial_cash": "10000"})
    request = {"config": config, "request_key": "strategy-invalid"}
    assert call(client, "/strategy-versions", request).status_code == 400


def test_concurrent_decisions_approve_only_one_version(client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.ashare.strategy_versions import StrategyVersions

    call(client, "/initialize", {"initial_cash": "10000"})
    base = "/strategy-versions"
    versions = [call(client, base, {"config": _strategy_config(), "request_key": f"parallel-{i}"}).json["data"]
                for i in range(2)]
    start = Barrier(2, timeout=5)

    def approve(version):
        with closing(psycopg2.connect(os.environ["TEST_ASHARE_DATABASE_URL"], options="-c lock_timeout=5000")) as conn:
            store = StrategyVersions(conn, f"user_{client.test_user}", "paper")
            start.wait()
            try:
                return store.decide(version["id"], version["config_sha256"], "approved", True,
                                    client.test_user)["status"]
            except ValueError as exc:
                return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(approve, versions)) == ["another_strategy_version_approved", "approved"]


def test_strategy_storage_rejects_rewrites_and_wrong_hash_link(client):
    call(client, "/initialize", {"initial_cash": "10000"})
    draft = call(client, "/strategy-versions",
                 {"config": _strategy_config(), "request_key": "storage-invariants"}).json["data"]
    with closing(psycopg2.connect(os.environ["TEST_ASHARE_DATABASE_URL"])) as conn:
        with pytest.raises(psycopg2.errors.CheckViolation), conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO qd_ashare_strategy_versions
                (tenant_id, account_id, id, request_key, config, config_sha256, created_by)
                VALUES (%s,'paper',%s,'invalid-direct-config','{}',%s,%s)""",
                (f"user_{client.test_user}", "a" * 32, "0" * 64, client.test_user))
        with pytest.raises(psycopg2.errors.ForeignKeyViolation), conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO qd_ashare_strategy_decisions
                (tenant_id, account_id, version_id, config_sha256, decision, actor_user_id)
                VALUES (%s,'paper',%s,%s,'approved',%s)""",
                (f"user_{client.test_user}", draft["id"], "0" * 64, client.test_user))
        for query in ("UPDATE qd_ashare_strategy_versions SET config='{}' WHERE false",
                      "DELETE FROM qd_ashare_strategy_decisions WHERE false"):
            with pytest.raises(psycopg2.errors.RaiseException, match="strategy_records_are_append_only"), conn, conn.cursor() as cur:
                cur.execute(query)
