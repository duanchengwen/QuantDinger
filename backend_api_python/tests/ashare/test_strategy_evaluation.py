"""Strategy assessment against an isolated paper ledger with controlled quotes."""
from datetime import timedelta
from decimal import Decimal

import pytest

from app.ashare import strategy_evaluation
from app.ashare.calendar_rules import market_session
from app.ashare.operations import complete_daily_reports, record_poll
from app.ashare.strategy_evaluation import evaluate_paper_strategy
from app.ashare.strategy_versions import StrategyVersions
from app.ashare.web_store import WebStore
from test_managed_worker import account, instant, quote  # noqa: F401


def draft(connection):
    store = WebStore(connection, "user_42")
    store.initialize("10000")
    versions = StrategyVersions(connection, *store.key, clock=lambda: instant(second=1))
    config = {"mode": "paper", "kind": "buy_once_below", "symbol": "sz.000001",
              "quantity": 200, "threshold": "10.00", "limit_price": "10.00", "max_cash": "2100.00"}
    return store, versions, versions.create(config, "test-assessment-draft", 42)


def decision(versions, version, state):
    return versions.decide(version["id"], version["config_sha256"], state, True, 42)


def submitted(connection):
    store, versions, version = draft(connection)
    decision(versions, version, "approved")
    decision(versions, version, "activated")
    rules = store.review({"symbol": "sz.000001", "reviewed": True, "evidence": "controlled test fixture",
                          "trading_day": "2026-11-17", "is_trading_day": True, "suspended": False,
                          "st": False, "limit_up": "11", "limit_down": "9", "reviewed_by": 42,
                          "approval_method": "explicit_human_review", "reviewed_at": instant().isoformat()})
    stamp = instant(second=2)
    versions.clock = lambda: stamp
    accepted = quote(stamp)
    store.broker.on_quote(accepted, rules, stamp)
    order = versions.submit_on_quote(accepted, rules)
    assert order["status"] == "open"
    return store, versions, version, rules


def test_strategy_assessment_is_tenant_scoped_and_a_draft_is_insufficient(account):  # noqa: F811
    connection, other, _ = account
    store, _, version = draft(connection)
    assessment = evaluate_paper_strategy(connection, *store.key, version["id"])
    assert assessment["status"] == "insufficient_evidence"
    assert assessment["execution"] == {"status": "not_activated"}
    assert assessment["proposal"] is None and assessment["execution_allowed"] is False
    assert assessment["performance"]["performance_return"] is None
    with pytest.raises(ValueError, match="strategy_version_not_found"):
        evaluate_paper_strategy(connection, *other.key, version["id"])


def test_order_read_reconciles_exact_fills_and_ignores_manual_cash_and_strategy_unattributed_exit(account):  # noqa: F811
    connection, _, _ = account
    store, versions, version, rules = submitted(connection)
    manual = store.broker.place_order("manual-same-symbol", "sz.000001", "buy", 100, Decimal("9.99"),
                                     instant(second=3), instant().date())
    assert manual["filled"] == 0
    stamp = instant(second=5)
    fill_quote = {**quote(stamp), "ask_size": 100, "evidence_sha256": "b" * 64}
    fill, = store.broker.on_quote(fill_quote, rules, stamp)
    assert fill["quantity"] == 100
    decision(versions, version, "revoked")
    next_day = instant(day=18, second=5)
    sell = store.broker.place_order("manual-exit", "sz.000001", "sell", 100, Decimal("9.99"),
                                   next_day - timedelta(seconds=1), next_day.date())
    assert sell["status"] == "open"
    exits = store.broker.on_quote(quote(next_day), {**rules, "trading_day": "2026-11-18"}, next_day)
    assert exits[0]["side"] == "sell"

    observation = evaluate_paper_strategy(connection, *store.key, version["id"])
    result = observation["execution"]
    assert result["status"] == "cancelled" and result["requested_quantity"] == 200
    assert result["filled_quantity"] == 100 and result["fill_count"] == 1
    assert result["fill_notional_cny"] == "1000.00" and result["fees_cny"] == "5.01"
    assert result["trigger_quote"]["recorded_quote_sha256"] == "a" * 64
    assert observation["performance"]["realized_pnl_cny"] is None
    assert observation["performance"]["marked_pnl_cny"] is None
    assert observation["proposal"] is None
    repeated = evaluate_paper_strategy(connection, *store.key, version["id"])
    assert observation["assessment_sha256"] == repeated["assessment_sha256"]
    assert len(store.state()["fills"]) == 2


@pytest.mark.parametrize(("clock_source", "post_submit_quote", "poll_matches", "reason"), [
    ("wall_clock", True, True, None),
    ("controlled_time", True, True, "verified_wall_clock_report_unavailable"),
    ("wall_clock", False, True, "post_submission_quote_unavailable"),
    ("wall_clock", True, False, "post_submission_quote_not_linked_to_wall_clock_poll"),
])
def test_expiry_review_requires_same_day_finished_evidence_and_post_submit_observation(
        account, clock_source, post_submit_quote, poll_matches, reason):  # noqa: F811
    connection, _, _ = account
    store, _, version, rules = submitted(connection)
    stamp = instant(second=5)
    sampled = {**quote(stamp), "ask": "10.01", "evidence_sha256": "b" * 64}
    if post_submit_quote:
        assert store.broker.on_quote(sampled, rules, stamp) == []
    polled = sampled if poll_matches else {**sampled, "evidence_sha256": "d" * 64}
    record_poll(connection, *store.key, stamp, [polled], "healthy", clock_source)
    end = instant(hour=7)
    record_poll(connection, *store.key, end, [], "closed", "wall_clock")
    expired = store.broker.expire_day_orders(end.date() + timedelta(days=1))
    assert expired[0]["status"] == "expired"
    report, = complete_daily_reports(connection, *store.key, end, market_session(end))
    assert report["reconciliation"]["ok"] is True
    assert report["observation_summary"]["last_details"]["worker_state"] == "closed"

    evaluation = evaluate_paper_strategy(connection, *store.key, version["id"])
    assert evaluation["execution"]["status"] == "expired"
    assert evaluation["execution"]["filled_quantity"] == 0
    assert evaluation["proposal_unavailable_reason"] == reason
    assert (evaluation["status"] == "review_suggested") is (reason is None)
    assert evaluation["performance"]["performance_return"] is None
    assert evaluation["execution_allowed"] is False
    if reason is None:
        proposal = evaluation["proposal"]
        assert proposal["base_version_id"] == version["id"]
        assert proposal["base_config_sha256"] == version["config_sha256"]
        assert proposal["requires_human_review"] is True
        assert proposal["execution_allowed"] is False and proposal["candidate_config"] is None
        assert proposal["post_submission_quote"]["recorded_quote_sha256"] == "b" * 64
        assert proposal["poll_observed_at"] == stamp.isoformat()
        assert len(proposal["postclose_report_sha256"]) == 64
    else:
        assert evaluation["proposal"] is None


@pytest.mark.parametrize(("damage", "reason"), [
    ("submission", "submission_audit_mismatch"),
    ("quote", "fill_quote_mismatch"),
    ("fee", "order_fill_totals_mismatch"),
    ("budget", "fill_observation_limit"),
])
def test_unverified_fill_or_submitted_version_evidence_cannot_propose_update(account, monkeypatch, damage, reason):  # noqa: F811
    connection, _, _ = account
    store, _, version, rules = submitted(connection)
    stamp = instant(second=5)
    store.broker.on_quote(quote(stamp), rules, stamp)
    with connection, connection.cursor() as cursor:
        if damage == "submission":
            cursor.execute("""UPDATE qd_ashare_audit SET details=details || '{"config_sha256":"bad"}'
                WHERE tenant_id=%s AND account_id=%s AND event='approved_strategy_order_placed'""", store.key)
        elif damage == "quote":
            cursor.execute("""UPDATE qd_ashare_fills SET evidence_sha256=%s
                WHERE tenant_id=%s AND account_id=%s""", ("b" * 64, *store.key))
        elif damage == "fee":
            cursor.execute("""UPDATE qd_ashare_orders SET commission=commission+1
                WHERE tenant_id=%s AND account_id=%s""", store.key)
        else:
            monkeypatch.setattr(strategy_evaluation, "MAX_FILLS", 0)
    result = evaluate_paper_strategy(connection, *store.key, version["id"])
    assert result["execution"] == {"status": "evidence_unavailable", "reason_code": reason}
    assert result["status"] == "insufficient_evidence" and result["proposal"] is None
    assert result["performance"]["performance_return"] is None


def test_evaluation_transaction_rejects_writes(account, monkeypatch):  # noqa: F811
    connection, _, _ = account
    store, _, version, _ = submitted(connection)

    def attempt_write(cursor, *_):
        cursor.execute("DELETE FROM qd_ashare_orders WHERE false")

    import psycopg2

    monkeypatch.setattr(strategy_evaluation, "_order_observation", attempt_write)
    with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
        evaluate_paper_strategy(connection, *store.key, version["id"])
    assert len(store.state()["orders"]) == 1
