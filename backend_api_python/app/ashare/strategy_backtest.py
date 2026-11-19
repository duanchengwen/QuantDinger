"""Fixed-rule, read-only A-share paper backtest for manually reviewed drafts."""
from datetime import timezone
from decimal import Decimal
import re

import psycopg2

from app.ashare.broker import PaperBroker, SHANGHAI, aware, supported
from app.ashare.securities import HISTORY_URL
from app.ashare.strategy_history import HistoricalEvidenceError, SESSIONS, history_window, load_history


METHOD = "ashare_closed_day_ma5_ma20_v1"
FAST = 5
WARMUP = 20
LOT = 100
_MONEY = re.compile(r"[0-9]{1,9}(?:\.[0-9]{1,2})?")


def _trend(bars, stop):
    fast = sum(bar.close for bar in bars[stop - FAST:stop]) / FAST
    slow = sum(bar.close for bar in bars[stop - WARMUP:stop]) / WARMUP
    return fast, slow


def _replay(bars, starting_cash, costs):
    cash = peak = starting_cash
    drawdown = realized = Decimal("0")
    entry = None
    buys = sells = 0
    for index in range(WARMUP, len(bars)):
        bar = bars[index]
        fast, slow = _trend(bars, index)
        limit = bars[index - 1].close
        notional = LOT * limit
        # Prior close sets both trigger and limit. The next session must open
        # through it and trade across it. Price at the limit, never at an OHLC extreme.
        if entry is None and fast > slow and bar.low <= bar.open < limit < bar.high:
            reserve = costs._buy_reserve(LOT, limit)
            if reserve <= min(cash, starting_cash):
                cash -= reserve
                entry = reserve
                buys += 1
        elif entry is not None and fast < slow and bar.low < limit < bar.open <= bar.high:
            # An entry cannot sell on its entry date. Require the observed high
            # plus fees also fit the cap when only daily bars are available.
            high_notional = LOT * bar.high
            fees = sum(costs._costs(notional, "sell"))
            if high_notional + sum(costs._costs(high_notional, "sell")) <= starting_cash:
                proceeds = notional - fees
                cash += proceeds
                realized += proceeds - entry
                entry = None
                sells += 1
        mark = cash
        if entry is not None:
            marked_notional = LOT * bar.close
            mark += marked_notional - sum(costs._costs(marked_notional, "sell"))
        peak = max(peak, mark)
        drawdown = max(drawdown, (peak - mark) / peak)
    net = mark - starting_cash
    fast, slow = _trend(bars, len(bars))
    return {
        "holdout_start": bars[WARMUP].day, "holdout_end": bars[-1].day,
        "starting_cash": f"{starting_cash:.2f}", "closing_equity_after_exit_costs": f"{mark:.2f}",
        "net_pnl": f"{net:.2f}", "realized_net_pnl": f"{realized:.2f}",
        "return_fraction": f"{net / starting_cash:.8f}", "max_drawdown_fraction": f"{drawdown:.8f}",
        "buy_fills": buys, "sell_fills": sells, "closed_round_trips": sells,
        "last_close": f"{bars[-1].close:.2f}", "fast_sma": f"{fast:.4f}", "slow_sma": f"{slow:.4f}",
        "required_buy_cash": f"{costs._buy_reserve(LOT, bars[-1].close):.2f}", "fee_policy": costs.fees,
    }, fast, slow, net, realized


def _candidate(symbol, close, cap, settled_quantity, costs, metrics, fast, slow, net, realized):
    if not metrics["closed_round_trips"]:
        return None, "no_closed_holdout_round_trip"
    if fast > slow and settled_quantity < LOT:
        if net <= 0 or realized <= 0:
            return None, "holdout_result_not_positive_after_costs"
        if close * LOT >= cap or Decimal(metrics["required_buy_cash"]) > cap:
            return None, "buy_cap_below_one_lot_with_fees"
        kind, reason = "buy_once_below", "bullish_trend_holdout_gate_met"
    elif fast < slow and settled_quantity >= LOT:
        notional = close * LOT
        if notional >= cap or notional + sum(costs._costs(notional, "sell")) > cap:
            return None, "sell_cap_below_one_lot_with_fees"
        kind, reason = "sell_once_above", "bearish_trend_settled_lot_available"
    elif fast < slow:
        return None, "no_settled_lot_to_sell"
    elif fast > slow:
        return None, "settled_lot_already_held"
    else:
        return None, "neutral_trend"
    return {"mode": "paper", "kind": kind, "symbol": symbol, "quantity": LOT,
            "threshold": f"{close:.2f}", "limit_price": f"{close:.2f}",
            "max_cash": f"{cap:.2f}"}, reason


def evaluate(connection, symbol, *, as_of, max_cash, fee_policy, settled_quantity, evidence_dir=None):
    """Return one proposed paper rule or explicit lack of trade/evidence; never submit it."""
    if not supported(symbol) or not symbol.isascii():
        raise ValueError("invalid_history_symbol")
    if not isinstance(max_cash, str) or not _MONEY.fullmatch(max_cash) or Decimal(max_cash) <= 0:
        raise ValueError("invalid_backtest_cash_cap")
    # PostgreSQL SUM(bigint) returns numeric, including integral lot totals.
    if (isinstance(settled_quantity, Decimal) and settled_quantity.is_finite()
            and settled_quantity == settled_quantity.to_integral_value()):
        settled_quantity = int(settled_quantity)
    if type(settled_quantity) is not int or settled_quantity < 0:
        raise ValueError("invalid_settled_quantity")
    if not isinstance(fee_policy, dict):
        raise ValueError("invalid_fee_policy")
    as_of = aware(as_of).astimezone(timezone.utc)
    cap = Decimal(max_cash)
    costs = PaperBroker(None, "historical-analysis", "paper", fee_policy=fee_policy)
    evidence = {"source": "baostock", "source_url": HISTORY_URL, "adjustment": "3", "calendar": "XSHG",
                "calendar_version": None, "first_session": None, "last_session": None,
                "warmup_sessions": WARMUP, "holdout_sessions": SESSIONS - WARMUP,
                "source_kind": None, "receipts": [], "historical_point_in_time_certified": False,
                "timestamp_semantics": "provider_reported_eod_retrieval_time_only",
                "source_representation": "canonical_sdk_decoded_fields_and_rows",
                "holdout_scope": "fixed_price_rule_on_current_research_candidate_only",
                "decision_shanghai_day": as_of.astimezone(SHANGHAI).date().isoformat()}
    report = {"method": METHOD, "execution_allowed": False, "config": None, "evidence": evidence, "metrics": None}
    try:
        days, calendar_version = history_window(as_of)
        evidence.update(calendar_version=calendar_version, first_session=days[0].isoformat(),
                        last_session=days[-1].isoformat())
        bars, source_kind, receipts = load_history(connection, symbol, days, as_of, evidence_dir)
    except HistoricalEvidenceError as exc:
        return {**report, "status": "insufficient_evidence", "reason_code": str(exc)}
    except psycopg2.Error:
        return {**report, "status": "insufficient_evidence", "reason_code": "historical_evidence_database_unavailable"}
    evidence.update(source_kind=source_kind, receipts=receipts)
    metrics, fast, slow, net, realized = _replay(bars, cap, costs)
    config, reason = _candidate(symbol, bars[-1].close, cap, settled_quantity, costs, metrics, fast, slow, net, realized)
    return {**report, "status": "candidate" if config is not None else "no_trade", "reason_code": reason,
            "metrics": metrics, "config": config}
