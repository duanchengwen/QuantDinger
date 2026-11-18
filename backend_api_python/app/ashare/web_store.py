"""Durable UI configuration and worker observations; no trading loops."""
from datetime import datetime, timezone
from decimal import Decimal
import json

from psycopg2.extras import RealDictCursor

from app.ashare.broker import PaperBroker, supported


def _research_preferences(value):
    if not isinstance(value, dict) or set(value) - {"topics", "follow_current_events", "custom_topics"}:
        raise ValueError("invalid_research_preferences")
    topics = value.get("topics", [])
    follow_events = value.get("follow_current_events", False)
    custom = value.get("custom_topics", "")
    if (not isinstance(topics, list) or len(topics) > 4
            or any(topic not in ("ai", "robotics", "new_energy", "semiconductors") for topic in topics)
            or len(set(topics)) != len(topics) or type(follow_events) is not bool
            or not isinstance(custom, str) or len(custom) > 240):
        raise ValueError("invalid_research_preferences")
    return {"topics": topics, "follow_current_events": follow_events, "custom_topics": custom.strip()}


class WebStore:
    def __init__(self, connection, tenant_id, account_id="paper"):
        self.connection = connection
        self.tenant_id, self.account_id = tenant_id, account_id
        self.key = (tenant_id, account_id)
        self.broker = PaperBroker(connection, tenant_id, account_id)

    def _row(self):
        with self.connection, self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT * FROM qd_ashare_web WHERE tenant_id=%s AND account_id=%s", self.key)
            row = cursor.fetchone()
        return dict(row) if row else {"settings": {"symbols": ["sz.000001"], "interval_seconds": 5}, "rules": {}, "quotes": [], "worker": {"state": "not_started"}}

    def settings(self):
        return self._row()["settings"]

    def initialize(self, cash):
        cash = Decimal(str(cash))
        if not cash.is_finite() or cash <= 0 or cash > Decimal("1000000000") or cash != cash.quantize(Decimal(".01")):
            raise ValueError("invalid_initial_cash")
        result = self.broker.initialize(cash)
        with self.connection, self.connection.cursor() as cursor:
            cursor.execute("INSERT INTO qd_ashare_web(tenant_id,account_id) VALUES(%s,%s) ON CONFLICT DO NOTHING", self.key)
        return result

    def save_settings(self, symbols, interval_seconds, research_preferences=None):
        if not isinstance(symbols, list) or not 1 <= len(symbols) <= 30 or any(not supported(s) for s in symbols) or len(set(symbols)) != len(symbols):
            raise ValueError("invalid_symbols")
        if type(interval_seconds) is not int or not 2 <= interval_seconds <= 3600:
            raise ValueError("invalid_interval")
        with self.connection, self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
            # Match strategy generation and broker mutations: lock the ledger before the web row.
            self.broker._account(cursor)
            cursor.execute("SELECT settings FROM qd_ashare_web WHERE tenant_id=%s AND account_id=%s FOR UPDATE",
                           self.key)
            row = cursor.fetchone()
            if row is None:
                raise ValueError("paper_account_required")
            if research_preferences is None:
                research_preferences = row["settings"].get("research_preferences", {})
            settings = {"symbols": symbols, "interval_seconds": interval_seconds,
                        "research_preferences": _research_preferences(research_preferences)}
            cursor.execute("""SELECT EXISTS (SELECT 1 FROM qd_ashare_lots
                WHERE tenant_id=%s AND account_id=%s AND quantity>0 AND symbol<>ALL(%s))
                OR EXISTS (SELECT 1 FROM qd_ashare_orders
                WHERE tenant_id=%s AND account_id=%s AND status IN ('open','partial') AND symbol<>ALL(%s)) AS active""",
                           (*self.key, symbols, *self.key, symbols))
            if cursor.fetchone()["active"]:
                raise ValueError("cannot_remove_active_symbols")
            cursor.execute("""UPDATE qd_ashare_web SET settings=settings || %s::jsonb,updated_at=now()
                WHERE tenant_id=%s AND account_id=%s""", (json.dumps(settings), *self.key))
        return settings

    def review(self, rule):
        return self.review_many([rule])[0]

    def review_many(self, rules):
        self.broker.snapshot(history_limit=200)
        with self.connection, self.connection.cursor() as cursor:
            cursor.execute("UPDATE qd_ashare_web SET rules=rules || %s::jsonb,updated_at=now() WHERE tenant_id=%s AND account_id=%s", (json.dumps({rule["symbol"]: rule for rule in rules}, default=str), *self.key))
            for rule in rules:
                cursor.execute("INSERT INTO qd_ashare_audit(tenant_id,account_id,event,details) VALUES (%s,%s,'daily_rules_reviewed',%s::jsonb)", (*self.key, json.dumps(rule, default=str)))
        return rules

    def observe(self, quotes, worker):
        with self.connection, self.connection.cursor() as cursor:
            cursor.execute("UPDATE qd_ashare_web SET quotes=%s::jsonb,worker=%s::jsonb,updated_at=now() WHERE tenant_id=%s AND account_id=%s", (json.dumps(quotes, default=str), json.dumps(worker, default=str), *self.key))

    @staticmethod
    def accounts(connection):
        with connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT tenant_id,account_id FROM qd_ashare_web ORDER BY tenant_id,account_id")
            return [dict(row) for row in cursor.fetchall()]

    def _cipher(self):
        import base64
        import hashlib
        from cryptography.fernet import Fernet
        from app.config.settings import Config
        secret = Config.SECRET_KEY
        if not secret or len(secret.encode()) < 32:
            raise ValueError("notification_encryption_key_required")
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))

    def configure_notifications(self, webhook_url, signing_secret=""):
        import re
        if not isinstance(webhook_url, str) or not isinstance(signing_secret, str) or len(signing_secret) > 512:
            raise ValueError("invalid_notification_configuration")
        if webhook_url and not re.fullmatch(r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9-]{1,160}", webhook_url):
            raise ValueError("invalid_webhook_configuration")
        self.broker.snapshot(history_limit=200)
        encrypted = self._cipher().encrypt(json.dumps({"webhook_url": webhook_url, "signing_secret": signing_secret}).encode()).decode() if webhook_url else None
        with self.connection, self.connection.cursor() as cursor:
            cursor.execute("UPDATE qd_ashare_web SET notifications_encrypted=%s WHERE tenant_id=%s AND account_id=%s", (encrypted, *self.key))
        return {"configured": bool(webhook_url), "signing_enabled": bool(webhook_url and signing_secret)}

    def notification_credentials(self):
        encrypted = self._row().get("notifications_encrypted")
        if not encrypted:
            return {"webhook_url": "", "signing_secret": ""}
        try:
            return json.loads(self._cipher().decrypt(encrypted.encode()))
        except Exception:
            raise ValueError("notification_configuration_unreadable") from None

    def state(self):
        row = self._row()
        try:
            result = self.broker.snapshot(history_limit=200)
        except ValueError:
            result = {"account": None, "orders": [], "positions": [], "fills": [], "audit": []}
        result.update({key: row[key] for key in ("settings", "rules", "quotes", "worker")})
        try:
            notification = self.notification_credentials()
            result["notifications"] = {"configured": bool(notification["webhook_url"]), "signing_enabled": bool(notification["signing_secret"])}
        except ValueError:
            result["notifications"] = {"configured": True, "signing_enabled": False, "error": "configuration_unreadable"}
        observed = result["worker"].get("observed_at")
        if observed:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(observed)).total_seconds()
                result["worker"] = {**result["worker"], "heartbeat_age_seconds": age, "stale": age < 0 or age > 30}
            except (TypeError, ValueError):
                result["worker"] = {**result["worker"], "stale": True}
        else:
            result["worker"] = {**result["worker"], "stale": True}
        return result
