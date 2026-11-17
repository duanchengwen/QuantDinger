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

import requests

_WEBHOOK = re.compile(r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9-]{1,160}\Z")


def notify_text(text: str) -> dict:
    """Send a paper-labelled text message; secrets never appear in return values.

    Configuration is process-local FEISHU_WEBHOOK_URL and optional
    FEISHU_SIGNING_SECRET. No configuration means disabled. A timeout does not
    prove non-delivery; callers must not retry the same event automatically.
    """
    webhook = os.environ.get("FEISHU_WEBHOOK_URL", "").strip()
    if not webhook:
        return {"status": "disabled"}
    if not _WEBHOOK.fullmatch(webhook):
        return {"status": "failed", "error": "invalid_webhook_configuration"}
    if not isinstance(text, str) or not text.strip():
        return {"status": "failed", "error": "invalid_message"}
    payload = {"msg_type": "text", "content": {"text": "[模拟交易 / PAPER]\n" + text}}
    secret = os.environ.get("FEISHU_SIGNING_SECRET", "")
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
