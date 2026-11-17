"""Daily Feishu content derives from ledger reports, never fabricated returns."""
import json

from app.ashare.daily_notification import format_daily_notification


def report(day="2026-11-17"):
    return {"trading_day": day, "cash_end": "98974.99", "day_fill_count": 1,
            "buy_notional": "1020", "sell_notional": "0", "fees": "5.01",
            "positions_end": [{"symbol": "sz.000001", "quantity": 100}],
            "equity_end": None, "reconciliation": {"ok": True},
            "observation_summary": {"wall_clock_polls": 1234, "controlled_time_polls": 0}}


def test_reports_cash_fills_fees_positions_and_missing_profit_without_fabrication():
    text = format_daily_notification([report()], ["sz.000001"], worker_state="closed")
    for value in ("98974.99", "1020.00", "5.01", "100 股", "1234", "账本对账：通过"):
        assert value in text
    assert "现金余额不等于收益" in text
    assert "收益率：不可用" in text
    assert "0.00%" not in text
    assert "AI 选股/推荐：尚未接入" in text
    assert "关注证券不代表推荐" in text
    assert "未来策略变更须人工批准" in text


def test_catchup_and_position_truncation_are_explicit():
    rows = [{**report(f"2026-11-{day:02d}"), "late_generation": True,
             "positions_end": [{"symbol": f"sz.{index:06d}", "quantity": 100} for index in range(30)]}
            for day in range(1, 32)]
    text = format_daily_notification(rows, [])
    assert "（补生成）" in text
    assert "日报展示已截断" in text
    assert "持仓展示已截断" in text
    assert "2026-11-31" in text
    assert "2026-11-01" not in text
    payload = {"msg_type": "text", "timestamp": "1234567890", "sign": "a" * 100,
               "content": {"text": "[模拟交易 / PAPER]\n" + text}}
    assert len(json.dumps(payload, ensure_ascii=False).encode()) < 20000


def test_pathological_strings_cannot_exceed_transport_payload_budget():
    item = report()
    item["positions_end"] = [{"symbol": '"\\' * 20000, "quantity": "坏" * 20000}] * 50
    text = format_daily_notification([item] * 100, ["坏" * 100000] * 100,
                                     account_id="坏\n" * 100000, worker_state="坏" * 100000)
    assert len(json.dumps({"content": {"text": text}}, ensure_ascii=False).encode()) < 18000
    assert "截断" in text


def test_missing_values_do_not_become_zero_profits_or_reconciled():
    text = format_daily_notification([{"trading_day": "2026-11-17"}], [])
    assert "期末现金：不可用" in text
    assert "账本对账：未通过/未知" in text
    assert "0.00%" not in text


def test_nonfinite_amount_and_unverified_equity_return_are_not_formatted_as_profit():
    text = format_daily_notification([{**report(), "cash_end": "NaN", "equity_end": "100000"}], [])
    assert "期末现金：不可用" in text
    assert "期末净值：100000.00" in text
    assert "缺少已核验收益基准" in text
