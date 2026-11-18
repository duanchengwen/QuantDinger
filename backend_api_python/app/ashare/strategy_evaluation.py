"""Read-only execution assessment for one exact A-share paper strategy version."""
from datetime import timezone
from decimal import Decimal
from hashlib import sha256
import json

from psycopg2.extensions import TRANSACTION_STATUS_IDLE
from psycopg2.extras import RealDictCursor

from app.ashare.broker import SHANGHAI, STRATEGY_ORDER_PREFIX, aware
from app.ashare.calendar_rules import market_session
from app.ashare.market import SOURCE
from app.ashare.strategy_versions import SHA256, VERSION_ID, StrategyVersions


MAX_FILLS = 1000
METHOD = "paper_buy_once_execution_v1"


class _UnverifiedEvidence(ValueError):
    """The ledger does not contain a complete, consistent evidence chain."""


def _instant(value):
    return aware(value).astimezone(timezone.utc).isoformat()


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _single(cursor, key, event, field, value):
    cursor.execute(
        """SELECT id,details FROM qd_ashare_audit
            WHERE tenant_id=%s AND account_id=%s AND event=%s AND details->>%s=%s
            ORDER BY id LIMIT 2""",
        (*key, event, field, str(value)),
    )
    rows = cursor.fetchall()
    if len(rows) != 1:
        raise _UnverifiedEvidence("missing_or_duplicate_" + event)
    return rows[0]


def _quote_records(cursor, key, symbol, ids):
    cursor.execute(
        """SELECT quote_id,exchange_time FROM qd_ashare_quotes
            WHERE tenant_id=%s AND account_id=%s AND symbol=%s AND quote_id=ANY(%s)""",
        (*key, symbol, ids),
    )
    accepted = {row["quote_id"]: _instant(row["exchange_time"]) for row in cursor.fetchall()}
    cursor.execute(
        """SELECT id,details FROM qd_ashare_audit
            WHERE tenant_id=%s AND account_id=%s AND event='quote_accepted' AND details->>'quote_id'=ANY(%s)
            ORDER BY id LIMIT %s""",
        (*key, ids, len(ids) + 1),
    )
    proofs = {}
    for row in cursor.fetchall():
        record = row["details"]
        try:
            quote_id, digest = record["quote_id"], record["evidence_sha256"]
            stamp = _instant(record["exchange_time"])
            if (quote_id in proofs or accepted.get(quote_id) != stamp or record["source"] != SOURCE
                    or not SHA256.fullmatch(digest)):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise _UnverifiedEvidence("quote_acceptance_mismatch") from None
        proofs[quote_id] = {"audit_id": str(row["id"]), "quote_id": quote_id, "exchange_time": stamp,
                            "recorded_quote_sha256": digest}
    if set(proofs) != set(ids):
        raise _UnverifiedEvidence("quote_acceptance_missing")
    return proofs


def _order_observation(cursor, key, version, order):
    config = version["config"]
    activated = version["activated_at"]
    order_id = str(order["id"])
    submitted = order["submitted_at"]
    day = order["trading_day"]
    if (order["side"] != "buy" or order["symbol"] != config["symbol"]
            or order["quantity"] != config["quantity"] or order["limit_price"] != Decimal(config["limit_price"])
            or day != submitted.astimezone(SHANGHAI).date()
            or activated is None or version["approved_at"] is None or activated >= submitted):
        raise _UnverifiedEvidence("order_version_mismatch")
    if ((order["status"] == "filled" and order["filled"] != order["quantity"])
            or (order["status"] == "partial" and not 0 < order["filled"] < order["quantity"])
            or (order["status"] == "open" and order["filled"])
            or (order["status"] in ("filled", "cancelled", "expired") and order["reserved_cash"])):
        raise _UnverifiedEvidence("order_status_mismatch")

    audit = _single(cursor, key, "approved_strategy_order_placed", "version_id", version["id"])
    details = audit["details"]
    try:
        trigger_id = details["quote_id"]
        trigger_time = aware(details["exchange_time"])
        if (type(details["order_id"]) is not int or str(details["order_id"]) != order_id
                or details["config_sha256"] != version["config_sha256"]
                or details["activated_by"] != version["activated_by"]
                or aware(details["activated_at"]) != activated
                or not isinstance(trigger_id, str) or not trigger_id
                or not activated < trigger_time <= submitted):
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise _UnverifiedEvidence("submission_audit_mismatch") from None

    cursor.execute(
        """SELECT id,symbol,side,quantity,price,fee,exchange_time,quote_id,evidence_sha256
            FROM qd_ashare_fills WHERE tenant_id=%s AND account_id=%s AND order_id=%s ORDER BY id LIMIT %s""",
        (*key, order["id"], MAX_FILLS + 1),
    )
    fills = cursor.fetchall()
    if len(fills) > MAX_FILLS:
        raise _UnverifiedEvidence("fill_observation_limit")

    proofs = _quote_records(cursor, key, order["symbol"], sorted({trigger_id, *(fill["quote_id"] for fill in fills)}))
    trigger = proofs[trigger_id]
    if (trigger["recorded_quote_sha256"] != details.get("evidence_sha256")
            or trigger["exchange_time"] != _instant(trigger_time)):
        raise _UnverifiedEvidence("submission_quote_mismatch")

    fill_digest_input = []
    quantity, notional, fees = 0, Decimal("0.00"), Decimal("0.00")
    for fill in fills:
        proof = proofs[fill["quote_id"]]
        stamp = fill["exchange_time"]
        if (fill["symbol"] != order["symbol"] or fill["side"] != "buy" or fill["quantity"] <= 0
                or fill["price"] <= 0 or fill["price"] > order["limit_price"] or fill["fee"] < 0
                or stamp <= submitted or stamp.astimezone(SHANGHAI).date() != day
                or fill["evidence_sha256"] != proof["recorded_quote_sha256"]
                or _instant(stamp) != proof["exchange_time"]):
            raise _UnverifiedEvidence("fill_quote_mismatch")
        quantity += fill["quantity"]
        notional += fill["price"] * fill["quantity"]
        fees += fill["fee"]
        fill_digest_input.append({
            "fill_id": str(fill["id"]), "quantity": fill["quantity"], "price_cny": str(fill["price"]),
            "fee_cny": str(fill["fee"]), "quote": proof,
        })
    if (quantity != order["filled"] or notional != order["notional"]
            or fees != order["commission"] + order["transfer_fee"] + order["sell_tax"]):
        raise _UnverifiedEvidence("order_fill_totals_mismatch")
    return {
        "order_id": order_id,
        "trading_day": day.isoformat(),
        "submitted_at": _instant(submitted),
        "status": order["status"],
        "requested_quantity": order["quantity"],
        "filled_quantity": quantity,
        "fill_count": len(fills),
        "fill_notional_cny": str(notional),
        "fees_cny": str(fees),
        "reserved_cash_cny": str(order["reserved_cash"]),
        "submission_audit_id": str(audit["id"]),
        "trigger_quote": trigger,
        "fill_observation_sha256": _digest(fill_digest_input),
    }


def _expiry_review(cursor, key, version, order):
    if order["status"] != "expired" or order["filled"] >= order["quantity"]:
        return None, "entry_expiry_not_observed"
    audit = _single(cursor, key, "order_expired", "id", order["id"])
    cursor.execute(
        """SELECT generated_at,report FROM qd_ashare_daily_reports
            WHERE tenant_id=%s AND account_id=%s AND trading_day=%s""", (*key, order["trading_day"]),
    )
    row = cursor.fetchone()
    if row is None:
        return None, "postclose_report_unavailable"
    day = order["trading_day"].isoformat()
    generated = row["generated_at"]
    session = market_session(generated)
    report = row["report"]
    if not isinstance(report, dict):
        return None, "postclose_report_unavailable"
    observations = report.get("observation_summary")
    reconciliation = report.get("reconciliation")
    if not isinstance(observations, dict) or not isinstance(reconciliation, dict):
        return None, "postclose_report_unavailable"
    samples = observations.get("ready_polls")
    wall_clock = observations.get("wall_clock_polls")
    controlled = observations.get("controlled_time_polls")
    if (session["state"] != "closed" or session["trading_day"] != day
            or report.get("trading_day") != day or report.get("mode") != "paper"
            or report.get("provenance") != "ledger_reconstruction" or report.get("late_generation") is not False
            or reconciliation.get("ok") is not True
            or observations.get("status") != "samples_recorded" or type(controlled) is not int or controlled != 0
            or type(samples) is not int or type(wall_clock) is not int or not 0 < samples <= wall_clock):
        return None, "verified_wall_clock_report_unavailable"
    try:
        poll = observations["last_details"]["last_ready_wall_clock"]
        poll_seen = aware(poll["observed_at"])
        quotes = poll["quotes"]
        if (not isinstance(quotes, list) or not 0 < len(quotes) <= 30
                or not order["submitted_at"] < poll_seen <= generated or not market_session(poll_seen)["can_match"]):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        return None, "post_submission_wall_clock_poll_unavailable"
    cursor.execute(
        """SELECT quote_id FROM qd_ashare_quotes
            WHERE tenant_id=%s AND account_id=%s AND symbol=%s AND exchange_time>%s AND exchange_time<=%s
            ORDER BY exchange_time DESC LIMIT 1""",
        (*key, order["symbol"], order["submitted_at"], poll_seen),
    )
    quote = cursor.fetchone()
    if quote is None:
        return None, "post_submission_quote_unavailable"
    quote_id = quote["quote_id"]
    sample_proof = _quote_records(cursor, key, order["symbol"], [quote_id])[quote_id]
    # Daily aggregate counts cannot qualify an unrelated quote.
    if not any(_poll_quote_matches(row, order["symbol"], sample_proof) for row in quotes):
        return None, "post_submission_quote_not_linked_to_wall_clock_poll"
    return ({
        "kind": "review_entry_constraints",
        "reason_code": "entry_order_expired_with_unfilled_quantity",
        "base_version_id": version["id"],
        "base_config_sha256": version["config_sha256"],
        "requires_human_review": True,
        "execution_allowed": False,
        "candidate_config": None,
        "expiry_audit_id": str(audit["id"]),
        "postclose_report_sha256": _digest(report),
        "observed_ready_wall_clock_samples": samples,
        "poll_observed_at": _instant(poll_seen),
        "post_submission_quote": sample_proof,
    }, None)


def _poll_quote_matches(row, symbol, proof):
    try:
        return (row["symbol"] == symbol and row["evidence_sha256"] == proof["recorded_quote_sha256"]
                and _instant(row["exchange_time"]) == proof["exchange_time"])
    except (KeyError, ValueError, TypeError):
        return False


def evaluate_paper_strategy(connection, tenant_id, account_id, version_id):
    """Read the current database snapshot. A caller must authorize the account."""
    if not isinstance(version_id, str) or not VERSION_ID.fullmatch(version_id):
        raise ValueError("invalid_strategy_version_or_hash")
    if connection.autocommit or connection.get_transaction_status() != TRANSACTION_STATUS_IDLE:
        raise ValueError("idle_transactional_connection_required")
    versions = StrategyVersions(connection, tenant_id, account_id)
    key = versions.key
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cursor.execute("SET LOCAL statement_timeout = '5s'")
        cursor.execute("SELECT transaction_timestamp() AS as_of")
        as_of = _instant(cursor.fetchone()["as_of"])
        version = versions._find(cursor, version_id)
        order = versions.broker._find_order(cursor, STRATEGY_ORDER_PREFIX + version_id)
        execution = {"status": "not_activated" if version["activated_at"] is None else "order_not_observed"}
        proposal = None
        proposal_reason = execution["status"]
        if order is not None:
            try:
                execution = _order_observation(cursor, key, version, order)
            except _UnverifiedEvidence as exc:
                execution = {"status": "evidence_unavailable", "reason_code": str(exc)}
                proposal_reason = "verified_order_observation_unavailable"
            else:
                try:
                    proposal, proposal_reason = _expiry_review(cursor, key, version, order)
                except _UnverifiedEvidence as exc:
                    proposal_reason = str(exc)
    proof = {
        "method": METHOD,
        "mode": "paper",
        "version_id": version_id,
        "config_sha256": version["config_sha256"],
        "version_status": version["status"],
        "version_decisions": {
            name: _instant(version[name]) if version[name] is not None else None
            for name in ("approved_at", "activated_at", "revoked_at")
        },
        "execution": execution,
        "performance": {
            "status": "insufficient_evidence",
            "reason_codes": ["strategy_exit_not_attributed", "verified_valuation_not_available",
                             "holdout_not_evaluated"],
            "realized_pnl_cny": None,
            "marked_pnl_cny": None,
            "performance_return": None,
        },
        "proposal": proposal,
        "proposal_unavailable_reason": proposal_reason,
        "status": "review_suggested" if proposal is not None else "insufficient_evidence",
        "execution_allowed": False,
    }
    return {"observed_at": as_of, "assessment_sha256": _digest(proof), **proof}
