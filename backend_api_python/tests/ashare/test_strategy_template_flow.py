"""Controlled acceptance from web settings to one paper fill using PostgreSQL."""
from contextlib import closing
from datetime import datetime, timezone
from functools import partial
import os

import psycopg2

from app.ashare import notifications, strategy_proposals, web_api
from app.ashare.broker import SHANGHAI
from app.ashare.managed_worker import ManagedWorker
from app.ashare.research import ResearchStore
from app.ashare.strategy_versions import StrategyVersions
from test_managed_worker import instant, quote
from test_research import Adapter, News, SYMBOL, Themes
from test_strategy_template import put, template
from test_web_api import call, client  # noqa: F401


def test_saved_template_research_notice_and_human_enable_lead_to_exactly_one_paper_fill(
        client, monkeypatch, tmp_path):  # noqa: F811
    versions_path = "/strategy-versions"
    assert call(client, "/initialize", {"initial_cash": "10000"}).status_code == 200
    assert call(client, "/settings", {"symbols": [SYMBOL], "interval_seconds": 5,
                                     "research_preferences": {"topics": ["ai"]}}, method="PUT").status_code == 200
    assert put(client, template(symbol_scope="selected", symbols=[SYMBOL])).status_code == 200
    assert call(client, versions_path, method="GET").json["data"]["versions"] == []
    assert call(client, "/state", method="GET").json["data"]["orders"] == []

    review_origin = "https://paper.test"
    webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/template-flow-fixture"
    assert call(client, "/notifications/config", {"webhook_url": webhook}).status_code == 200
    monkeypatch.setenv("ASHARE_REVIEW_BASE_URL", review_origin)
    notices = []

    def fake_webhook(payload, webhook_url, signing_secret):
        assert webhook_url == webhook and signing_secret == ""
        assert payload["msg_type"] == "interactive"
        notices.append(payload["card"]["elements"][1]["actions"][0]["url"])
        return {"status": "sent"}

    monkeypatch.setattr(notifications, "_send", fake_webhook)
    # Keep research timestamps from PostgreSQL while controlling the proposal calendar input.
    research_day = datetime.now(SHANGHAI).date()
    monkeypatch.setattr(strategy_proposals, "trading_day_status", lambda day: {"is_trading_day": day == research_day})
    scope = (f"user_{client.test_user}", "paper")
    tick = instant()

    class ReviewClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return tick.astimezone(tz or timezone.utc)

    monkeypatch.setattr(web_api, "datetime", ReviewClock)
    monkeypatch.setattr(web_api, "StrategyVersions", partial(StrategyVersions, clock=lambda: tick))

    with closing(psycopg2.connect(os.environ["TEST_ASHARE_DATABASE_URL"])) as connection:
        research = ResearchStore(connection, *scope)
        queued = research.request_scheduled_run(research_day)
        adapter = Adapter()
        complete = research.run_once(adapter, Themes(), News())
        assert complete["status"] == "completed" and complete["id"] == queued["id"]
        scheduled = strategy_proposals.propose_and_notify(connection, scope=scope)
        assert scheduled["status"] == "draft_created" and scheduled["notice"] == "webhook_acknowledged"
        version_id = scheduled["version_id"]
        assert notices == [f"{review_origin}/#/ashare-paper?strategy_version={version_id}"]

        version_path = f"{versions_path}/{version_id}"
        draft = call(client, version_path, method="GET").json["data"]
        assert draft["status"] == "draft" and draft["execution_allowed"] is False
        assert draft["config"]["research_reference"] == {
            "run_id": complete["id"], "input_hash": complete["input_hash"], "output_hash": complete["output_hash"],
        }
        assert call(client, "/state", method="GET").json["data"]["orders"] == []

        worker = ManagedWorker(connection, tmp_path, notifications_enabled=False,
                               fetcher=lambda *_args, **_kwargs: [{**quote(tick), "symbol": SYMBOL, "name": "浦发银行"}])
        worker.process_account(*scope, now=tick)
        tick = instant(second=1)
        review = call(client, "/rules/review", {"symbol": SYMBOL, "trading_day": tick.date().isoformat(),
                                               "suspended": False, "st": False, "evidence": "Controlled test fixture",
                                               "limit_up": "11", "limit_down": "9"})
        assert review.json["data"]["approval_method"] == "explicit_human_review"
        tick = instant(second=6)
        before_approval = worker.process_account(*scope, now=tick)
        assert before_approval["state"] == "healthy" and before_approval["strategy_submitted"] == []

        decision = {"config_sha256": draft["config_sha256"], "confirmed": True}
        decision_path = f"{version_path}/decision"
        assert call(client, decision_path, {**decision, "decision": "activated"}).status_code == 400
        decision_result = call(client, decision_path, {**decision, "decision": "approved"}).json["data"]
        assert decision_result["approved_by"] == client.test_user and decision_result["execution_allowed"] is False
        tick = instant(second=12)
        approved_only = worker.process_account(*scope, now=tick)
        assert approved_only["state"] == "healthy" and approved_only["strategy_submitted"] == []
        assert call(client, "/state", method="GET").json["data"]["orders"] == []

        tick = instant(second=13)
        enabled = call(client, decision_path, {**decision, "decision": "activated"}).json["data"]
        assert enabled["activated_by"] == client.test_user and enabled["execution_allowed"] is True
        tick = instant(second=18)
        submitted = worker.process_account(*scope, now=tick)
        order_id, = submitted["strategy_submitted"]
        assert submitted["fills"] == [] and call(client, version_path, method="GET").json["data"]["order"]["status"] == "open"
        tick = instant(second=24)
        execution = worker.process_account(*scope, now=tick)
        fill, = execution["fills"]
        assert execution["strategy_submitted"] == [] and fill["order_id"] == order_id
        assert fill["side"] == "buy" and fill["quantity"] == 100 and fill["symbol"] == SYMBOL

        tick = instant(second=30)
        repeated = worker.process_account(*scope, now=tick)
        assert repeated["strategy_submitted"] == repeated["fills"] == []
        assert research.request_scheduled_run(research_day)["id"] == complete["id"]
        assert research.run_once(adapter, Themes(), News()) is None
        assert strategy_proposals.propose_and_notify(connection, scope=scope) == {**scheduled, "status": "already_proposed"}

    assert len(notices) == adapter.calls == 1
    state = call(client, "/state", method="GET").json["data"]
    assert len(state["orders"]) == len(state["fills"]) == 1
    assert state["orders"][0]["status"] == "filled"
