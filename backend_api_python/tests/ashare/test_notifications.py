"""Feishu notification behavior using a local transport double, never a webhook."""
import json

import pytest


@pytest.fixture(autouse=True)
def clean_configuration(monkeypatch):
    monkeypatch.delenv("FEISHU_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("FEISHU_SIGNING_SECRET", raising=False)


def test_unconfigured_webhook_is_disabled():
    from app.ashare.notifications import notify_text
    assert notify_text("日报")["status"] == "disabled"


@pytest.mark.parametrize("url", [
    "http://open.feishu.cn/open-apis/bot/v2/hook/test-token",
    "https://evil.test/open-apis/bot/v2/hook/test-token",
    "https://open.feishu.cn.evil.test/open-apis/bot/v2/hook/test-token",
    "https://open.feishu.cn/open-apis/bot/v2/hook/test-token?leak=1",
    "https://user@open.feishu.cn/open-apis/bot/v2/hook/test-token",
])
def test_untrusted_webhook_never_reaches_transport(monkeypatch, url):
    import requests
    from app.ashare.notifications import notify_text
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", url)
    monkeypatch.setattr(requests.Session, "post", lambda *a, **k: pytest.fail("network forbidden"))
    assert notify_text("test") == {"status": "failed", "error": "invalid_webhook_configuration"}


def test_signed_message_has_paper_label_and_redirects_disabled(monkeypatch):
    import requests
    from app.ashare import notifications
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://open.feishu.cn/open-apis/bot/v2/hook/test-token")
    monkeypatch.setenv("FEISHU_SIGNING_SECRET", "test-secret")
    monkeypatch.setattr(notifications.time, "time", lambda: 1700000000)
    captured = {}
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def iter_content(self, chunk_size): yield b'{"code":0,"msg":"success"}'
    def post(self, url, **kwargs):
        captured.update(kwargs)
        return Response()
    monkeypatch.setattr(requests.Session, "post", post)
    assert notifications.notify_text("成交 100 股") == {"status": "sent"}
    payload = json.loads(captured["data"])
    assert payload["timestamp"] == "1700000000"
    assert payload["sign"] == "mbm4Y4oluIPQ00qlBIhX8vAZ0EKv3nw0LuTb91jPL84="
    assert payload["content"]["text"].startswith("[模拟交易 / PAPER]")
    assert captured["allow_redirects"] is False
    assert captured["timeout"] == (3.0, 5.0)


def test_transport_error_never_leaks_secret_url_and_never_retries(monkeypatch):
    import requests
    from app.ashare.notifications import notify_text
    webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/test-token"
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", webhook)
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        raise requests.Timeout(webhook)
    monkeypatch.setattr(requests.Session, "post", post)
    assert notify_text("test") == {"status": "failed", "error": "transport_error_delivery_unknown"}
    assert len(calls) == 1


@pytest.mark.parametrize("status_code,body,expected", [
    (302, b'{"code":0}', "webhook_http_error"),
    (200, b'{"code":19021,"msg":"signature rejected"}', "webhook_rejected"),
    (200, b'{"code":false}', "webhook_rejected"),
    (200, b'not-json', "invalid_webhook_response"),
    (200, b'x' * 32769, "invalid_webhook_response"),
])
def test_redirect_api_failure_and_invalid_response_are_not_success(monkeypatch, status_code, body, expected):
    import requests
    from app.ashare.notifications import notify_text
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://open.feishu.cn/open-apis/bot/v2/hook/test-token")
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def iter_content(self, chunk_size): yield body
    response = Response()
    response.status_code = status_code
    monkeypatch.setattr(requests.Session, "post", lambda *args, **kwargs: response)
    assert notify_text("test") == {"status": "failed", "error": expected}


def test_oversized_message_never_reaches_transport(monkeypatch):
    import requests
    from app.ashare.notifications import notify_text
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://open.feishu.cn/open-apis/bot/v2/hook/test-token")
    monkeypatch.setattr(requests.Session, "post", lambda *a, **k: pytest.fail("network forbidden"))
    assert notify_text("中" * 10000) == {"status": "failed", "error": "message_too_large"}
