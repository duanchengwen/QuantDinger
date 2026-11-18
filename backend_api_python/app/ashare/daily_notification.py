"""Bounded ledger summaries; unavailable valuations and research stay explicit."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json

from app.ashare.broker import SHANGHAI


MAX_PAYLOAD_BYTES = 18000
MAX_REPORTS = 10
MAX_POSITIONS = 10
MAX_WATCHLIST_SYMBOLS = 5
STATES = {
    "healthy": "正常", "pre_open": "盘前", "lunch": "午间休市", "closed": "已收盘",
    "non_trading_day": "非交易日", "calendar_unknown": "交易日历待核验",
    "awaiting_review": "待审核当日规则", "failed": "运行异常",
    "reconciliation_failed": "账本对账异常", "storage_low": "磁盘空间不足",
}


def daily_feishu_status(result):
    """Summarize transport evidence without keeping credentials or response bodies."""
    if not isinstance(result, dict):
        return "unknown"
    status = result.get("status")
    error = result.get("error")
    if status == "sent":
        return "webhook_acknowledged"
    if status == "disabled":
        return "disabled"
    if error == "webhook_rejected":
        return "rejected"
    if error in ("invalid_webhook_configuration", "invalid_message", "message_too_large"):
        return "failed"
    return "unknown"


def _text(value, limit=100):
    if value is None:
        return "未提供"
    return "".join(character for character in str(value) if character.isprintable())[:limit]


def _money(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or abs(number) > Decimal("1e15"):
            return "不可用"
        return f"{number.quantize(Decimal('0.01')):f} 元"
    except (ValueError, TypeError, InvalidOperation):
        return "不可用"


def _state(value):
    return STATES[value] if isinstance(value, str) and value in STATES else _text(value, 40)


def beijing_time(value):
    try:
        stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
        if isinstance(stamp, datetime) and stamp.utcoffset() is not None:
            return f"{stamp.astimezone(SHANGHAI):%Y-%m-%d %H:%M:%S}（北京时间）"
    except (ValueError, OverflowError):
        pass
    return "不可用"


def format_paper_event(account_id, state, fill_count, observed_at):
    return (f"模拟运行 · 账户 {_text(account_id, 64)} · {_state(state)}\n"
            f"观察时间：{beijing_time(observed_at)}\n"
            f"本轮虚拟成交：{fill_count} 笔")


def _payload_size(text):
    # Leave room for the transport's timestamp/signature, extra indentation and
    # future fields beneath Feishu's 20 KB maximum.
    return len(json.dumps({"msg_type": "text", "content": {
        "text": "[模拟交易 / PAPER]\n" + text}}, ensure_ascii=False).encode("utf-8"))


def format_daily_notification(reports, watchlist, *, account_id="paper", worker_state=None):
    return daily_notification_summary(reports, watchlist, account_id=account_id, worker_state=worker_state)[0]


def daily_notification_summary(reports, watchlist, *, account_id="paper", worker_state=None):
    """Format only newly created reports; never load account history or retry."""
    lines = [f"[模拟交易日报] 账户 {_text(account_id, 64)} · {_state(worker_state)}"]
    if watchlist:
        symbols = "、".join(_text(symbol, 20) for symbol in watchlist[:MAX_WATCHLIST_SYMBOLS])
        suffix = f" 等 {len(watchlist)} 只" if len(watchlist) > MAX_WATCHLIST_SYMBOLS else ""
        lines.append(f"关注列表：{symbols}{suffix}")
    lines.append("本日报无选股和策略更新数据。")
    ordered = sorted(reports, key=lambda report: str(report.get("trading_day", "")))
    selected = ordered[-MAX_REPORTS:]
    omitted_reports = max(0, len(ordered) - len(selected))
    report_lines = []
    for report in selected:
        lines.append("")
        label = "（补生成）" if report.get("late_generation") else ""
        report_lines.append((len(lines), report.get("trading_day")))
        lines.append(f"交易日：{_text(report.get('trading_day'), 10)}{label}")
        lines.append(f"生成时间：{beijing_time(report.get('generated_at'))}")
        lines.append(f"当日成交：{_text(report.get('day_fill_count'), 12)} 笔 · "
                     f"买入 {_money(report.get('buy_notional'))} · 卖出 {_money(report.get('sell_notional'))} · "
                     f"费用 {_money(report.get('fees'))}")
        lines.append(f"期末现金：{_money(report.get('cash_end'))}")
        positions = report.get("positions_end") or []
        held = "、".join(f"{_text(row.get('symbol'), 20)} {_text(row.get('quantity'), 16)} 股"
                        for row in positions[:MAX_POSITIONS])
        lines.append("期末持仓：" + (held or "无"))
        if len(positions) > MAX_POSITIONS:
            lines.append(f"持仓展示已截断，另有 {len(positions) - MAX_POSITIONS} 项。")
        if report.get("equity_end") is None:
            lines.append("期末净值、当日盈亏及收益率：不可用（缺少可信收盘估值）。")
        else:
            lines.append(f"期末净值：{_money(report['equity_end'])}")
            lines.append("当日盈亏及收益率：不可用（缺少已核验收益基准）。")
        reconciled = report.get("reconciliation", {}).get("ok") is True
        lines.append("账本对账：" + ("通过" if reconciled else "未通过/未知，请检查页面"))
    if omitted_reports:
        lines.append(f"日报展示已截断：另有 {omitted_reports} 份本轮新建历史日报，完整记录见页面。")
    lines.append("完整记录见 A 股模拟页面。")
    text = "\n".join(lines)
    truncation = "\n消息长度已截断；完整日报和交易记录请查看页面。"
    if _payload_size(text) > MAX_PAYLOAD_BYTES:
        while lines and _payload_size("\n".join(lines) + truncation) > MAX_PAYLOAD_BYTES:
            lines.pop()
        text = "\n".join(lines) + truncation
    return text, [day for index, day in report_lines if index < len(lines) and isinstance(day, str)]
