"""Downstream safety boundaries must reject before any broker side effects."""
import json
from pathlib import Path

import pytest
from flask import Flask


def test_environment_cannot_enable_brokers(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("AGENT_LIVE_TRADING_ENABLED", "true")
    from app.ashare.safety import PaperOnlyViolation, deny_broker_access
    with pytest.raises(PaperOnlyViolation, match="ASHARE_PAPER_ONLY"):
        deny_broker_access()


@pytest.mark.parametrize("factory", ["create_client", "create_ibkr_client", "create_alpaca_client", "create_tiger_client", "create_webull_client"])
def test_all_broker_factories_reject_before_reading_credentials(factory):
    from app.ashare.safety import PaperOnlyViolation
    from app.services.live_trading import factory as factories
    class ForbiddenConfig(dict):
        def get(self, *args, **kwargs):
            pytest.fail("credentials must not be inspected")
    with pytest.raises(PaperOnlyViolation):
        getattr(factories, factory)(ForbiddenConfig())


def test_direct_exchange_client_is_blocked_before_network(monkeypatch):
    import requests
    from app.ashare.safety import PaperOnlyViolation
    from app.services.live_trading.base import BaseRestClient
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: pytest.fail("network forbidden"))
    with pytest.raises(PaperOnlyViolation):
        BaseRestClient("https://api.binance.com")


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_agent_gateway_rejects_mutation_before_handler(monkeypatch, method):
    from app.routes import agent_v1
    monkeypatch.setattr(agent_v1, "is_agent_gateway_enabled", lambda: True)
    app = Flask(__name__)
    app.before_request(agent_v1._require_agent_gateway_enabled)
    @app.route("/probe", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def probe():
        return {"ok": True}
    response = app.test_client().open("/probe", method=method)
    assert response.status_code == 403
    assert response.json["message"] == "ASHARE_AGENT_READ_ONLY"
    assert app.test_client().get("/probe").status_code == 200


def test_agent_openapi_documents_downstream_restriction():
    spec_path = Path(__file__).resolve().parents[3] / "docs/agent/agent-openapi.json"
    spec = json.loads(spec_path.read_text())
    assert spec["x-ashare-downstream-policy"]["agent_mutations"] == "denied"
    for path in spec["paths"].values():
        for method, operation in path.items():
            if method in {"post", "put", "patch", "delete"}:
                assert "403" in operation["responses"]


@pytest.mark.parametrize("module_name,class_name", [
    ("app.services.ibkr_trading.client", "IBKRClient"),
    ("app.services.alpaca_trading.client", "AlpacaClient"),
    ("app.services.openapi_brokers.clients", "TigerClient"),
    ("app.services.openapi_brokers.clients", "WebullClient"),
])
def test_direct_stock_brokers_reject_before_sdk_setup(module_name, class_name):
    import importlib
    from app.ashare.safety import PaperOnlyViolation
    cls = getattr(importlib.import_module(module_name), class_name)
    with pytest.raises(PaperOnlyViolation):
        cls(None)


@pytest.mark.parametrize("name", ["submit_binance_order", "submit_okx_order", "submit_bybit_order", "submit_bitget_order", "submit_gate_order"])
def test_websocket_submission_rejects_without_client_access(name):
    import inspect
    from app.ashare.safety import PaperOnlyViolation
    from app.services.live_trading import ws_order_transport
    submit = getattr(ws_order_transport, name)
    kwargs = {key: None for key, param in inspect.signature(submit).parameters.items() if param.default is inspect.Parameter.empty}
    with pytest.raises(PaperOnlyViolation):
        submit(**kwargs)


@pytest.mark.parametrize("endpoint", ["place-order", "close-position", "cancel-order", "position-protection"])
def test_human_order_http_endpoints_reject_without_credentials_or_network(monkeypatch, endpoint):
    import requests
    from app.routes.quick_trade import quick_trade_blp
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: pytest.fail("network forbidden"))
    app = Flask(__name__)
    app.register_blueprint(quick_trade_blp, url_prefix="/api/quick-trade")
    response = app.test_client().post("/api/quick-trade/" + endpoint, json={})
    assert response.status_code == 403
    assert response.json["msg"] == "ASHARE_PAPER_ONLY"


def test_queued_live_order_is_failed_without_context_or_credentials():
    from app.services.pending_order_worker import PendingOrderWorker
    worker = object.__new__(PendingOrderWorker)
    rejected = []
    worker._mark_failed = lambda **kwargs: rejected.append(kwargs)
    class ForbiddenPayload(dict):
        def get(self, *args, **kwargs):
            pytest.fail("live order payload must not be resolved")
    worker._execute_live_order(order_id=27, order_row=ForbiddenPayload(), payload=ForbiddenPayload())
    assert rejected == [{"order_id": 27, "error": "ASHARE_PAPER_ONLY"}]
