"""Daily Feishu content derives from ledger reports, never fabricated returns."""
import json

from app.ashare.daily_notification import beijing_time, daily_notification_summary, format_daily_notification, format_paper_event


def report(day="2026-11-17"):
    return {"trading_day": day, "generated_at": "2026-11-16T16:07:05Z",
            "cash_end": "98974.99", "day_fill_count": 1,
            "buy_notional": "1020", "sell_notional": "0", "fees": "5.01",
            "positions_end": [{"symbol": "sz.000001", "quantity": 100}],
            "equity_end": None, "reconciliation": {"ok": True},
            "observation_summary": {"wall_clock_polls": 1234, "controlled_time_polls": 0}}


def test_reports_cash_fills_fees_positions_and_missing_profit_without_fabrication():
    text = format_daily_notification([report()], ["sz.000001"], worker_state="closed")
    for value in ("98974.99", "1020.00", "5.01", "100 股", "账本对账：通过", "已收盘"):
        assert value in text
    assert "生成时间：2026-11-17 00:07:05（北京时间）" in text
    assert "收益率：不可用" in text
    assert "0.00%" not in text
    assert "本日报无选股和策略更新数据" in text
    assert "关注列表：sz.000001" in text
    assert "尚未接入" not in text and "采样：" not in text


def test_catchup_and_position_truncation_are_explicit():
    rows = [{**report(f"2026-11-{day:02d}"), "late_generation": True,
             "positions_end": [{"symbol": f"sz.{index:06d}", "quantity": 100} for index in range(30)]}
            for day in range(1, 32)]
    text, days = daily_notification_summary(rows, [])
    assert "（补生成）" in text
    assert "日报展示已截断" in text
    assert "持仓展示已截断" in text
    assert "2026-11-31" in text
    assert "2026-11-01" not in text
    assert days == [row["trading_day"] for row in rows if "交易日：" + row["trading_day"] in text]
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
    assert "生成时间：不可用" in text
    assert "0.00%" not in text


def test_nonfinite_amount_and_unverified_equity_return_are_not_formatted_as_profit():
    text = format_daily_notification([{**report(), "cash_end": "NaN", "equity_end": "100000"}], [])
    assert "期末现金：不可用" in text
    assert "期末净值：100000.00" in text
    assert "缺少已核验收益基准" in text


def test_operational_summary_uses_observed_time_in_beijing():
    text = format_paper_event("paper", "awaiting_review", 2, "2026-11-19T01:30:09+00:00")
    assert "观察时间：2026-11-19 09:30:09（北京时间）" in text
    assert "待审核当日规则" in text and "虚拟成交：2 笔" in text
    assert beijing_time("2026-11-19T09:30:09") == "不可用"
