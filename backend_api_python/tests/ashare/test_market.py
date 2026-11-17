"""Quote ingestion contract tests without application startup or network access."""

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[2] / "app" / "ashare" / "market.py"
spec = importlib.util.spec_from_file_location("ashare_market_contract", MODULE)
market = importlib.util.module_from_spec(spec)
spec.loader.exec_module(market)
NOW = datetime(2026, 11, 17, 2, 0, 2, tzinfo=timezone.utc)


def response_bytes():
    fields = [""] * 49
    for index, value in {1: "平安银行", 2: "000001", 3: "12.01", 4: "12.00",
                         5: "12.00", 6: "1234", 9: "12.00", 10: "2",
                         19: "12.02", 20: "3", 30: "20261117100000",
                         33: "12.10", 34: "11.90", 47: "13.20", 48: "10.80"}.items():
        fields[index] = value
    return ('v_sz000001="' + "~".join(fields) + '";').encode("gb18030")


class Response:
    status = 200
    url = "https://qt.gtimg.cn/q=sz000001"
    headers = {"Content-Type": "text/plain"}

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read1(self, size):
        result, self.body = self.body[:size], self.body[size:]
        return result


def mock_source(monkeypatch, body=None):
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, timeout))
            response = Response(response_bytes() if body is None else body)
            response.url = request.full_url
            return response

    monkeypatch.setattr(market.urllib.request, "build_opener", lambda *args: Opener())
    return calls


def test_fetch_archives_raw_and_preserves_source_time_and_share_units(tmp_path, monkeypatch):
    calls = mock_source(monkeypatch)
    quote = market.fetch_quotes(["sz.000001"], tmp_path)[0]
    assert calls == [("https://qt.gtimg.cn/q=sz000001", 10)]
    assert quote["exchange_time"] == "2026-11-17T10:00:00+08:00"
    assert quote["bid_size"] == 200 and quote["ask_size"] == 300
    assert quote["volume"] == 123400
    assert quote["last"] == "12.01"
    assert Path(quote["evidence_path"]).read_bytes() == response_bytes()
    metadata = json.loads(Path(quote["evidence_path"]).with_name("metadata.json").read_text())
    assert metadata["source"] == "tencent_web"
    assert metadata["raw_sha256"] == quote["evidence_sha256"]
    assert metadata["quotes"][0]["quote_id"] == quote["quote_id"]


def test_same_snapshot_has_same_quote_id_but_separate_immutable_receipts(tmp_path, monkeypatch):
    mock_source(monkeypatch)
    first = market.fetch_quotes(["sz.000001"], tmp_path)[0]
    second = market.fetch_quotes(["sz.000001"], tmp_path)[0]
    assert first["quote_id"] == second["quote_id"]
    assert first["evidence_path"] != second["evidence_path"]
    assert Path(first["evidence_path"]).read_bytes() == Path(second["evidence_path"]).read_bytes()


def fresh_quote(tmp_path, monkeypatch):
    mock_source(monkeypatch)
    quote = market.fetch_quotes(["sz.000001"], tmp_path)[0]
    quote["received_at"] = NOW.isoformat()
    return quote


def test_quote_identity_does_not_change_when_another_symbol_changes(tmp_path, monkeypatch):
    other = response_bytes().replace(b"sz000001", b"sh600000").replace(b"~000001~", b"~600000~")
    first_raw = response_bytes() + other
    mock_source(monkeypatch, first_raw)
    first = market.fetch_quotes(["sz.000001", "sh.600000"], tmp_path)[0]
    second_raw = first_raw.replace(b"600000~12.01", b"600000~12.02")
    mock_source(monkeypatch, second_raw)
    second = market.fetch_quotes(["sz.000001", "sh.600000"], tmp_path)[0]
    assert first["quote_id"] == second["quote_id"]
    assert first["evidence_sha256"] != second["evidence_sha256"]


def test_fresh_quote_passes_validation(tmp_path, monkeypatch):
    market.validate_quote(fresh_quote(tmp_path, monkeypatch), NOW)


@pytest.mark.parametrize("changes", [
    {"exchange_time": "2026-11-17T09:59:00+08:00"},
    {"exchange_time": "2026-11-17T10:00:03+08:00"},
    {"exchange_time": "2026-11-17T10:00:00"},
    {"received_at": "2026-11-17T10:00:03+08:00"},
    {"received_at": "2026-11-17T09:59:00+08:00"},
    {"last": "NaN"}, {"last": "-1"}, {"last": "Infinity"},
    {"bid": "12.03"}, {"ask_size": -1}, {"bid_size": 1.5},
    {"symbol": "sz.000001?injected"}, {"limit_up": "11.00"},
])
def test_invalid_quote_cannot_be_used(tmp_path, monkeypatch, changes):
    quote = fresh_quote(tmp_path, monkeypatch)
    quote.update(changes)
    with pytest.raises(ValueError):
        market.validate_quote(quote, NOW)


@pytest.mark.parametrize("symbols", [[], ["sz.000001", "sz.000001"], ["sz.000001,sh600000"], ["http://evil"], ["sh.000001"], ["sz.600000"]])
def test_invalid_symbols_rejected_before_download(tmp_path, monkeypatch, symbols):
    calls = mock_source(monkeypatch)
    with pytest.raises(ValueError):
        market.fetch_quotes(symbols, tmp_path)
    assert calls == []


def test_missing_security_and_oversized_response_fail_closed(tmp_path, monkeypatch):
    mock_source(monkeypatch, b'v_sz000001="";')
    with pytest.raises(ValueError):
        market.fetch_quotes(["sz.000001"], tmp_path)
    assert list(tmp_path.rglob("source.bin"))
    mock_source(monkeypatch, b"a" * 256001)
    with pytest.raises(ValueError, match="size"):
        market.fetch_quotes(["sz.000001"], tmp_path)


def test_missing_limits_remain_unknown(tmp_path, monkeypatch):
    raw = response_bytes().replace(b"~13.20~10.80", b"~~")
    mock_source(monkeypatch, raw)
    quote = market.fetch_quotes(["sz.000001"], tmp_path)[0]
    assert quote["limit_up"] is None and quote["limit_down"] is None
    quote["received_at"] = NOW.isoformat()
    with pytest.raises(ValueError, match="limits"):
        market.validate_quote(quote, NOW)


def test_naive_now_is_rejected(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        market.validate_quote(fresh_quote(tmp_path, monkeypatch), NOW.replace(tzinfo=None))


def test_stale_source_with_current_receipt_is_rejected(tmp_path, monkeypatch):
    quote = fresh_quote(tmp_path, monkeypatch)
    quote["received_at"] = (NOW + timedelta(seconds=30)).isoformat()
    with pytest.raises(ValueError, match="stale"):
        market.validate_quote(quote, NOW + timedelta(seconds=30))


def test_redirects_are_rejected_even_when_target_uses_https(tmp_path, monkeypatch):
    def build(*handlers):
        class Opener:
            def open(self, request, timeout):
                handlers[0].redirect_request(request, None, 302, "redirect", {}, "https://evil.example/")
        return Opener()
    monkeypatch.setattr(market.urllib.request, "build_opener", build)
    with pytest.raises(ValueError, match="redirect"):
        market.fetch_quotes(["sz.000001"], tmp_path)


def test_timeout_is_reported_without_returning_invented_price(tmp_path, monkeypatch):
    class Opener:
        def open(self, request, timeout):
            raise TimeoutError("source unavailable")
    monkeypatch.setattr(market.urllib.request, "build_opener", lambda *args: Opener())
    with pytest.raises(ValueError, match="download failed"):
        market.fetch_quotes(["sz.000001"], tmp_path)
