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
        for name in ("20261117_ashare_paper.sql", "20261117_ashare_web.sql"):
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
    actual={(rule.rule.replace("/api/ashare-paper", "").replace("<int:order_id>","{order_id}"),method.lower()) for rule in app.url_map.iter_rules() if rule.rule.startswith("/api/ashare-paper") for method in rule.methods if method not in {"HEAD","OPTIONS"}}
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
