"""One-shot Feishu receipts for new committed fills from a managed paper poll."""
from datetime import timedelta
from hashlib import sha256
from uuid import uuid4

from psycopg2.extras import RealDictCursor

from app.ashare.broker import aware
from app.ashare.daily_notification import _text, beijing_time, daily_feishu_status


TRADE_FEISHU_ATTEMPT = "trade_fill_feishu_attempt"
TRADE_FEISHU_RESULT = "trade_fill_feishu_result"
MAX_FILLS_PER_MESSAGE = 50
MAX_POLL_QUOTE_AGE = timedelta(seconds=15)


def _message(account_id, fills):
    lines = [f"[模拟成交] 账户 {_text(account_id, 64)} · {len(fills)} 笔"]
    for fill in fills:
        side = "买入" if fill["side"] == "buy" else "卖出"
        lines.append(f"{beijing_time(fill['exchange_time'])} · {side} {fill['symbol']} · "
                     f"{fill['quantity']} 股 × {fill['price']:.2f} 元 · "
                     f"成交额 {fill['notional']:.2f} 元 · 费用 {fill['fee']:.2f} 元")
    return "\n".join(lines)


def notify_new_fills(store, fills, observed_at, notifier):
    """Never discover historical fills. A committed intent is terminal for retry."""
    fill_ids = sorted({fill["id"] for fill in fills if isinstance(fill, dict)
                       and type(fill.get("id")) is int and 0 < fill["id"] <= 2**63 - 1})
    observed = aware(observed_at)
    receipts = []
    for offset in range(0, len(fill_ids), MAX_FILLS_PER_MESSAGE):
        batch = fill_ids[offset:offset + MAX_FILLS_PER_MESSAGE]
        with store.connection, store.connection.cursor(cursor_factory=RealDictCursor) as cursor:
            store.broker._account(cursor)
            cursor.execute("""SELECT f.id,f.side,f.symbol,f.quantity,f.price,f.fee,f.exchange_time,
                       f.price*f.quantity AS notional
                FROM qd_ashare_fills f
                WHERE f.tenant_id=%s AND f.account_id=%s AND f.id=ANY(%s)
                  AND f.exchange_time BETWEEN %s AND %s AND f.side IN ('buy','sell')
                  AND NOT EXISTS (SELECT 1 FROM qd_ashare_audit a
                    WHERE a.tenant_id=f.tenant_id AND a.account_id=f.account_id AND a.event=%s
                      AND a.details->'fill_ids' ? f.id::text)
                ORDER BY f.exchange_time,f.id""",
                           (*store.key, batch, observed - MAX_POLL_QUOTE_AGE, observed, TRADE_FEISHU_ATTEMPT))
            claimed = cursor.fetchall()
            if not claimed:
                continue
            message = _message(store.account_id, claimed)
            attempt = {"attempt_id": uuid4().hex, "fill_ids": [str(fill["id"]) for fill in claimed],
                       "summary_sha256": sha256(message.encode("utf-8")).hexdigest()}
            store.broker._audit(cursor, TRADE_FEISHU_ATTEMPT, attempt)

        try:
            credentials = store.notification_credentials()
            response = (notifier(message, webhook_url=credentials["webhook_url"],
                                 signing_secret=credentials.get("signing_secret", ""))
                        if credentials.get("webhook_url") else {"status": "disabled"})
            status = daily_feishu_status(response)
        except Exception:
            status = "unknown"
        receipt = {**attempt, "status": status}
        with store.connection, store.connection.cursor() as cursor:
            store.broker._audit(cursor, TRADE_FEISHU_RESULT, receipt)
        receipts.append(receipt)
    return receipts
