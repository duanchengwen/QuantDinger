"""Validate persisted generation settings without granting paper execution."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.ashare.strategy_template import saved_template
from app.ashare.strategy_versions import config_hash
from test_web_api import call, client  # noqa: F401


PATH = "/strategy-template"
UNSAVED = {"revision": 0, "template": None}


def template(**overrides):
    return {"enabled": True, "symbol_scope": "watched", "symbols": [], "trigger_price": "10",
            "limit_price": "10.01", "max_cash": "1200", **overrides}


def put(api, value, revision=0, token="user"):
    return call(api, PATH, {"expected_revision": revision, "template": value}, token=token, method="PUT")


def test_template_restores_only_canonical_saved_settings():
    assert saved_template({"symbols": ["sz.000001"]}) == UNSAVED
    value = template(symbol_scope="selected", symbols=["sh.600000"], trigger_price="10.00", max_cash="1200.00")
    settings = {"symbols": ["sz.000001"], "strategy_template": {"revision": 3, "template": value}}
    restored = saved_template(settings)
    assert restored == settings["strategy_template"]
    restored["template"]["symbols"].clear()
    assert value["symbols"] == ["sh.600000"]
    for malformed in (None, {"revision": False, "template": value}, {"revision": 0, "template": value},
                      {"revision": 1, "template": None}, {"revision": 1, "template": template()}):
        with pytest.raises(ValueError, match="^strategy_template_integrity_error$"):
            saved_template({"strategy_template": malformed})


def test_template_api_is_human_tenant_scoped_and_has_no_execution_effect(client):  # noqa: F811
    assert client.get("/api/ashare-paper" + PATH).status_code == 401
    assert call(client, PATH, method="GET").json["data"] == UNSAVED
    assert put(client, template()).json["msg"] == "paper_account_required"
    assert call(client, "/initialize", {"initial_cash": "1000"}).status_code == 200
    assert call(client, "/initialize", {"initial_cash": "1000"}, token="other").status_code == 200
    assert put(client, template(), token="viewer").status_code == 403
    assert call(client, PATH + "?tenant_id=other", method="GET").status_code == 400
    assert call(client, PATH, {"expected_revision": 0, "template": template(), "actor_user_id": 1},
                method="PUT").status_code == 400
    assert call(client, PATH, method="POST").status_code == 405

    saved = put(client, template())
    assert saved.status_code == 200
    clean_template = template(trigger_price="10.00", max_cash="1200.00")
    assert saved.json["data"] == {"revision": 1, "template": clean_template}
    assert call(client, PATH + "?_t=1", token="viewer", method="GET").json["data"] == saved.json["data"]
    assert call(client, PATH, token="other", method="GET").json["data"] == UNSAVED
    assert call(client, "/strategy-versions", method="GET").json["data"]["versions"] == []
    state = call(client, "/state", method="GET").json["data"]
    assert state["orders"] == [] and state["positions"] == [] and state["account"]["cash"] == "1000.00"
    events = [event["details"] for event in state["audit"] if event["event"] == "strategy_template_saved"]
    assert events == [{"revision": 1, "enabled": True, "symbol_scope": "watched", "actor_user_id": client.test_user,
                       "template_sha256": config_hash(clean_template)}]


@pytest.mark.parametrize(("overrides", "reason"), [
    ({"enabled": 1}, "invalid_strategy_template"),
    ({"symbol_scope": "everywhere"}, "invalid_strategy_template"),
    ({"symbol_scope": "selected"}, "invalid_strategy_template"),
    ({"symbols": ["sz.000001"]}, "invalid_strategy_template"),
    ({"symbol_scope": "selected", "symbols": ["sz.000001", "sz.000001"]}, "invalid_strategy_template"),
    ({"symbol_scope": "selected", "symbols": ["sh.688001"]}, "invalid_strategy_template"),
    ({"symbol_scope": "selected", "symbols": ["sh.600000"]}, "strategy_template_symbols_not_watched"),
    ({"quantity": 100}, "invalid_strategy_template"),
    ({"trigger_price": 10}, "invalid_strategy_money"),
    ({"trigger_price": "1e1"}, "invalid_strategy_money"),
    ({"trigger_price": "10.001"}, "invalid_strategy_money"),
    ({"limit_price": "0"}, "invalid_strategy_money"),
    ({"max_cash": "1001"}, "strategy_cash_cap_must_cover_order_and_fees"),
    ({"max_cash": "1001.01"}, "strategy_cash_cap_exceeded"),
])
def test_template_rejects_unsupported_fields_money_and_unguarded_scope(client, overrides, reason):  # noqa: F811
    call(client, "/initialize", {"initial_cash": "10000"})
    response = put(client, template(**overrides))
    assert response.status_code == 400
    assert response.json == {"code": 0, "msg": reason, "data": None}
    assert call(client, PATH, method="GET").json["data"] == UNSAVED


def test_watchlist_and_research_preferences_remain_editable_without_losing_template(client):  # noqa: F811
    call(client, "/initialize", {"initial_cash": "10000"})
    initial = {"symbols": ["sz.000001", "sh.600000"], "interval_seconds": 5,
               "research_preferences": {"topics": ["ai"], "custom_topics": " robotics "}}
    assert call(client, "/settings", initial, method="PUT").status_code == 200
    selected = template(symbol_scope="selected", symbols=["sh.600000"])
    saved = put(client, selected).json["data"]
    assert saved["revision"] == 1
    after_save = call(client, "/state", method="GET").json["data"]["settings"]
    assert after_save["symbols"] == initial["symbols"] and after_save["interval_seconds"] == 5
    assert after_save["research_preferences"]["topics"] == ["ai"]
    assert after_save["research_preferences"]["custom_topics"] == "robotics"
    assert call(client, "/settings", {"symbols": ["sz.000001"], "interval_seconds": 12},
                method="PUT").status_code == 200
    assert call(client, PATH, method="GET").json["data"] == saved
    assert put(client, selected, revision=1).json["msg"] == "strategy_template_symbols_not_watched"
    disabled = put(client, {**selected, "enabled": False}, revision=1).json["data"]
    assert disabled["revision"] == 2 and disabled["template"]["enabled"] is False
    assert disabled["template"]["symbols"] == ["sh.600000"]
    final = call(client, "/state", method="GET").json["data"]["settings"]
    assert final["symbols"] == ["sz.000001"] and final["interval_seconds"] == 12
    assert final["research_preferences"] == after_save["research_preferences"]


def test_concurrent_template_saves_conflict_instead_of_overwriting(client):  # noqa: F811
    call(client, "/initialize", {"initial_cash": "10000"})
    assert put(client, template(), revision=False).json["msg"] == "invalid_strategy_template_revision"
    assert put(client, template(), revision=-1).json["msg"] == "invalid_strategy_template_revision"
    barrier = Barrier(2, timeout=5)

    def save(price):
        other_client = client.application.test_client()
        barrier.wait()
        return put(other_client, template(trigger_price=price))

    with ThreadPoolExecutor(max_workers=2) as threads:
        responses = list(threads.map(save, ("10.02", "10.03")))
    saved, conflict = sorted(responses, key=lambda response: response.status_code)
    assert saved.status_code == 200 and conflict.status_code == 409
    assert conflict.json == {"code": 0, "msg": "strategy_template_conflict", "data": None}
    assert call(client, PATH, method="GET").json["data"] == saved.json["data"]
    assert saved.json["data"]["revision"] == 1
    events = call(client, "/state", method="GET").json["data"]["audit"]
    assert sum(event["event"] == "strategy_template_saved" for event in events) == 1
