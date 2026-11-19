"""Optional one-way Feishu notifications for paper operation.

Official contract: https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot
No automatic retry: after a transport failure delivery may already have occurred.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from urllib.parse import urlsplit

import requests

from app.ashare.daily_notification import beijing_time, daily_feishu_status

_WEBHOOK = re.compile(r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9-]{1,160}\Z")


def notify_text(text: str, *, webhook_url: str | None = None, signing_secret: str | None = None) -> dict:
    """Send a paper-labelled text message; secrets never appear in return values.

    Configuration is process-local FEISHU_WEBHOOK_URL and optional
    FEISHU_SIGNING_SECRET. No configuration means disabled. A timeout does not
    prove non-delivery; callers must not retry the same event automatically.
    """
    payload = None
    if isinstance(text, str) and text.strip():
        payload = {"msg_type": "text", "content": {"text": "[模拟交易 / PAPER]\n" + text}}
    return _send(payload, webhook_url, signing_secret)


def notify_strategy_card(version, url, *, webhook_url, signing_secret):
    config = version["config"]
    lines = [
        f"生成时间：{beijing_time(version.get('created_at'))}",
        f"标的：{config['symbol']} · {config['quantity']} 股",
        f"触发价 {config['threshold']} 元 · 限价 {config['limit_price']} 元",
        f"含费上限：{config['max_cash']} 元",
        "登录后人工批准此版本，再单独启用自动模拟委托。",
    ]
    return notify_paper_link("策略待审", lines, "查看待批策略", url, webhook_url=webhook_url,
                             signing_secret=signing_secret)


def notify_paper_link(title, lines, button, url, *, webhook_url, signing_secret):
    payload = {"msg_type": "interactive", "card": {
        "header": {"title": {"tag": "plain_text", "content": f"模拟交易 · {title}"}},
        "elements": [
            {"tag": "div", "text": {"tag": "plain_text", "content": "\n".join(lines)}},
            {"tag": "action", "actions": [
                {"tag": "button", "type": "primary", "text": {"tag": "plain_text", "content": button},
                 "url": url},
            ]},
        ],
    }}
    return _send(payload, webhook_url, signing_secret)


def review_origin():
    base = os.environ.get("ASHARE_REVIEW_BASE_URL", "").strip().rstrip("/")
    if base:
        origin = urlsplit(base)
        if (origin.scheme != "https" or not origin.hostname or origin.username is not None
                or origin.path or origin.query or origin.fragment or origin.port == 0):
            raise ValueError("invalid_strategy_review_origin")
    return base


def send_strategy_review_notice(store, versions, version):
    """Send only after the exact paper draft and its one-shot intent have committed."""
    base = review_origin()
    if not base:
        return "unconfigured"
    credentials = store.notification_credentials()
    if not credentials["webhook_url"]:
        return "unconfigured"
    if not versions.claim_review_notice(version["id"]):
        return versions.review_notice_status(version["id"])
    link = f"{base}/#/ashare-paper?strategy_version={version['id']}"
    status = daily_feishu_status(notify_strategy_card(version, link, **credentials))
    versions.record_review_notice(version["id"], status)
    return status


def _send(payload, webhook_url, signing_secret):
    webhook = (os.environ.get("FEISHU_WEBHOOK_URL", "") if webhook_url is None else webhook_url).strip()
    if not webhook:
        return {"status": "disabled"}
    if not _WEBHOOK.fullmatch(webhook):
        return {"status": "failed", "error": "invalid_webhook_configuration"}
    if payload is None:
        return {"status": "failed", "error": "invalid_message"}
    secret = os.environ.get("FEISHU_SIGNING_SECRET", "") if signing_secret is None else signing_secret
    if secret:
        timestamp = str(int(time.time()))
        signing_key = (timestamp + "\n" + secret).encode("utf-8")
        signature = base64.b64encode(hmac.new(signing_key, b"", hashlib.sha256).digest()).decode("ascii")
        payload.update(timestamp=timestamp, sign=signature)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(body) > 20000:
        return {"status": "failed", "error": "message_too_large"}
    try:
        with requests.Session() as session:
            # Do not forward a secret webhook through environment proxy settings.
            session.trust_env = False
            with session.post(
                webhook, data=body, headers={"Content-Type": "application/json"},
                timeout=(3.0, 5.0), allow_redirects=False, stream=True,
            ) as response:
                if response.status_code != 200:
                    return {"status": "failed", "error": "webhook_http_error"}
                received = bytearray()
                deadline = time.monotonic() + 5.0
                for chunk in response.iter_content(chunk_size=4096):
                    received.extend(chunk)
                    if len(received) > 32768 or time.monotonic() > deadline:
                        return {"status": "failed", "error": "invalid_webhook_response"}
                result = json.loads(received)
                if not isinstance(result, dict):
                    return {"status": "failed", "error": "invalid_webhook_response"}
                code = result.get("code", result.get("StatusCode"))
                if type(code) is not int or code != 0:
                    return {"status": "failed", "error": "webhook_rejected"}
                return {"status": "sent"}
    except requests.RequestException:
        return {"status": "failed", "error": "transport_error_delivery_unknown"}
    except (ValueError, UnicodeError):
        return {"status": "failed", "error": "invalid_webhook_response"}
