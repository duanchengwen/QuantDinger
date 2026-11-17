"""Bounded ledger summaries; unavailable valuations and research stay explicit."""
from decimal import Decimal, InvalidOperation
import json


MAX_PAYLOAD_BYTES = 18000
MAX_REPORTS = 10
MAX_POSITIONS = 10


def _text(value, limit=100):
    if value is None:
        return "未提供"
    return "".join(character for character in str(value) if character.isprintable())[:limit]


def _money(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or abs(number) > Decimal("1e15"):
            return "不可用"
        return format(number.quantize(Decimal("0.01")), "f")
    except (ValueError, TypeError, InvalidOperation):
        return "不可用"


def _payload_size(text):
    # Leave room for the transport's timestamp/signature, extra indentation and
    # future fields beneath Feishu's 20 KB maximum.
    return len(json.dumps({"msg_type": "text", "content": {
        "text": "[模拟交易 / PAPER]\n" + text}}, ensure_ascii=False).encode("utf-8"))


def format_daily_notification(reports, watchlist, *, account_id="paper", worker_state=None):
    """Format only newly created reports; never load account history or retry."""
    lines = [f"[模拟交易日报] 账户={_text(account_id, 64)} 状态={_text(worker_state, 40)}",
             "AI 选股/推荐：尚未接入；下列关注证券不代表推荐。",
             "关注列表：" + ("、".join(_text(symbol, 20) for symbol in watchlist[:30]) or "空"),
             "策略更新：自动研究与更新尚未接入；未来策略变更须人工批准，AI 不得自动晋级。"]
    ordered = sorted(reports, key=lambda report: str(report.get("trading_day", "")))
    selected = ordered[-MAX_REPORTS:]
    omitted_reports = max(0, len(ordered) - len(selected))
    for report in selected:
        lines.append("")
        label = "（补生成）" if report.get("late_generation") else ""
        lines.append(f"交易日：{_text(report.get('trading_day'), 10)}{label}")
        lines.append(f"期末现金：{_money(report.get('cash_end'))} 元（现金余额不等于收益）")
        lines.append(f"当日成交：{_text(report.get('day_fill_count'), 12)} 笔；"
                     f"买入金额 {_money(report.get('buy_notional'))} 元；"
                     f"卖出金额 {_money(report.get('sell_notional'))} 元；"
                     f"费用 {_money(report.get('fees'))} 元")
        positions = report.get("positions_end") or []
        held = "、".join(f"{_text(row.get('symbol'), 20)} {_text(row.get('quantity'), 16)} 股"
                        for row in positions[:MAX_POSITIONS])
        lines.append("期末持仓：" + (held or "无"))
        if len(positions) > MAX_POSITIONS:
            lines.append(f"持仓展示已截断，另有 {len(positions) - MAX_POSITIONS} 项；完整记录见页面账本。")
        if report.get("equity_end") is None:
            lines.append("期末净值、当日盈亏及收益率：不可用（缺少可信收盘估值，未估算）。")
        else:
            lines.append(f"期末净值：{_money(report['equity_end'])} 元；当日盈亏及收益率：不可用（缺少已核验收益基准）。")
        reconciled = report.get("reconciliation", {}).get("ok") is True
        lines.append("账本对账：" + ("通过" if reconciled else "未通过/未知，请检查页面"))
        observation = report.get("observation_summary", {})
        lines.append(f"采样：真实时钟 {_text(observation.get('wall_clock_polls'), 12)} 次；"
                     f"受控回放 {_text(observation.get('controlled_time_polls'), 12)} 次；不代表完整连续在线。")
        lines.append("详细委托、成交与证据请查看 A 股模拟页面的每日账本和成交记录。")
    if omitted_reports:
        lines.append(f"日报展示已截断：另有 {omitted_reports} 份本轮新建历史日报，完整记录见页面。")
    text = "\n".join(lines)
    truncation = "\n消息长度已截断；完整日报和交易记录请查看页面。"
    if _payload_size(text) > MAX_PAYLOAD_BYTES:
        while lines and _payload_size("\n".join(lines) + truncation) > MAX_PAYLOAD_BYTES:
            lines.pop()
        text = "\n".join(lines) + truncation
    return text
