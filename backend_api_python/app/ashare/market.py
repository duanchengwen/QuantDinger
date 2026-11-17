"""Bounded, evidence-preserving Tencent web snapshots for A-share paper research.

These are provider-reported timestamps, not authenticated exchange ticks. Fresh
quotes do not prove trading status or guarantee execution at displayed depth.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


SHANGHAI = timezone(timedelta(hours=8))
SOURCE = "tencent_web"
PARSER_VERSION = "tencent-ashare-web-v1"
MAX_BYTES = 256000
SYMBOL = re.compile(r"(?:sh\.(?:60|68)\d{4}|sz\.(?:00|30)\d{4})\Z")


def _decimal(value, *, positive=False):
    try:
        if isinstance(value, bool):
            raise ValueError("invalid quote number")
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number > Decimal("1e15") or (positive and number == 0):
            raise ValueError("invalid quote number")
        return number
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("invalid quote number") from exc


def _aware(value):
    try:
        result = datetime.fromisoformat(value)
        if result.utcoffset() is None:
            raise ValueError("quote timestamp timezone required")
        return result
    except (TypeError, AttributeError) as exc:
        raise ValueError("invalid quote timestamp") from exc


def validate_quote(quote: dict, now: datetime, max_age_seconds: float = 15) -> None:
    """Reject unusable execution input; a pass is not security-status approval."""
    if now.utcoffset() is None:
        raise ValueError("current time timezone required")
    if not math.isfinite(max_age_seconds) or not 0 < max_age_seconds <= 300:
        raise ValueError("invalid quote freshness threshold")
    try:
        if not isinstance(quote["symbol"], str) or not SYMBOL.fullmatch(quote["symbol"]):
            raise ValueError("invalid A-share symbol")
        source_time = _aware(quote["exchange_time"])
        received = _aware(quote["received_at"])
        if source_time > now or received > now or source_time > received + timedelta(seconds=2):
            raise ValueError("quote timestamp is in the future")
        if max((now - source_time).total_seconds(), (now - received).total_seconds()) > max_age_seconds:
            raise ValueError("stale quote")
        if source_time.astimezone(SHANGHAI).date() != now.astimezone(SHANGHAI).date():
            raise ValueError("stale quote session")
        last = _decimal(quote["last"], positive=True)
        bid = _decimal(quote["bid"], positive=True)
        ask = _decimal(quote["ask"], positive=True)
        if bid > ask:
            raise ValueError("crossed quote book")
        for field in ("bid_size", "ask_size", "volume"):
            value = quote[field]
            if type(value) is not int or value < 0:
                raise ValueError("invalid quote share volume")
        if not quote["bid_size"] or not quote["ask_size"]:
            raise ValueError("missing quote book")
        for field in ("previous_close", "open", "high", "low"):
            _decimal(quote[field], positive=True)
        if quote.get("limit_up") is None or quote.get("limit_down") is None:
            raise ValueError("missing quote price limits")
        lower = _decimal(quote["limit_down"], positive=True)
        upper = _decimal(quote["limit_up"], positive=True)
        if not lower < upper or not all(lower <= value <= upper for value in (bid, ask, last)):
            raise ValueError("quote outside reported price limits")
    except KeyError as exc:
        raise ValueError(f"missing quote field: {exc.args[0]}") from exc


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("quote source redirect rejected")


def _download(url: str, timeout: float) -> tuple[bytes, datetime]:
    request = urllib.request.Request(url, headers={
        "User-Agent": "QuantDinger-Ashare-Paper/1.0",
        "Referer": "https://qt.gtimg.cn/",
        "Accept-Encoding": "identity",
    })
    deadline = time.monotonic() + timeout
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            if response.url != url or response.status != 200:
                raise ValueError("unexpected quote response")
            chunks, length = [], 0
            while True:
                chunk = response.read1(min(8192, MAX_BYTES + 1 - length))
                length += len(chunk)
                if length > MAX_BYTES:
                    raise ValueError("quote response exceeds size limit")
                if time.monotonic() > deadline:
                    raise ValueError("quote download exceeded deadline")
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks), datetime.now(timezone.utc)
    except (OSError, urllib.error.URLError) as exc:
        raise ValueError("quote source download failed") from exc


def _parse(raw: bytes, symbols: list[str], received: datetime, evidence: Path) -> list[dict]:
    raw_hash = hashlib.sha256(raw).hexdigest()
    quotes = []
    try:
        text = raw.decode("gb18030")
        for match in re.finditer(r'v_((?:sh|sz)\d{6})="([^"\r\n]*)";', text):
            code, payload = match.groups()
            symbol = code[:2] + "." + code[2:]
            fields = payload.split("~")
            if len(fields) < 49 or fields[2] != code[2:]:
                raise ValueError("quote schema/security mismatch")
            source_time = datetime.strptime(fields[30], "%Y%m%d%H%M%S").replace(tzinfo=SHANGHAI)

            def price(index):
                return format(_decimal(fields[index]), "f")

            def shares(index):
                value = _decimal(fields[index]) * 100
                if value != value.to_integral_value():
                    raise ValueError("nonintegral quote share count")
                return int(value)

            def limit(index):
                return price(index) if fields[index] and _decimal(fields[index]) > 0 else None

            row = {
                "symbol": symbol, "name": fields[1],
                "exchange_time": source_time.isoformat(), "received_at": received.isoformat(),
                "last": price(3), "previous_close": price(4), "open": price(5),
                "high": price(33), "low": price(34),
                "bid": price(9), "ask": price(19),
                "bid_size": shares(10), "ask_size": shares(20), "volume": shares(6),
                "limit_up": limit(47), "limit_down": limit(48),
                "source": SOURCE, "source_url": f"https://qt.gtimg.cn/q={code}",
                "evidence_sha256": raw_hash, "evidence_path": str(evidence),
                "parser_version": PARSER_VERSION,
                "timestamp_semantics": "provider_reported_not_exchange_authenticated",
                "volume_unit": "shares_converted_from_lots_of_100",
                "quality": "web_snapshot_latency_unverified",
            }
            payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            identity = f"{SOURCE}|{symbol}|{source_time.isoformat()}|{payload_hash}"
            row["quote_id"] = hashlib.sha256(identity.encode()).hexdigest()
            quotes.append(row)
    except (UnicodeError, IndexError) as exc:
        raise ValueError("malformed quote response") from exc
    if len(quotes) != len(symbols) or {q["symbol"] for q in quotes} != set(symbols):
        raise ValueError("provider quote scope incomplete, unexpected or duplicate")
    by_symbol = {quote["symbol"]: quote for quote in quotes}
    return [by_symbol[symbol] for symbol in symbols]


def fetch_quotes(symbols: list[str], evidence_dir: Path, timeout: float = 10) -> list[dict]:
    """Fetch current snapshots and archive each receipt without overwriting evidence.

    Closed-market/stale snapshots are archived and returned for monitoring; call
    validate_quote at the matching boundary before considering any virtual fill.
    """
    if (not isinstance(symbols, list) or not 1 <= len(symbols) <= 30
            or any(not isinstance(s, str) or not SYMBOL.fullmatch(s) for s in symbols)
            or len(set(symbols)) != len(symbols)):
        raise ValueError("quote scope must contain 1..30 distinct Shanghai/Shenzhen A-shares")
    if isinstance(timeout, bool) or not math.isfinite(timeout) or not 1 <= timeout <= 60:
        raise ValueError("invalid quote download timeout")
    url = "https://qt.gtimg.cn/q=" + ",".join(symbol.replace(".", "") for symbol in symbols)
    raw, received = _download(url, timeout)
    evidence_dir = Path(evidence_dir).resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    destination = evidence_dir / (received.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex)
    metadata = {
        "schema_version": 1, "source": SOURCE, "source_url": url,
        "received_at": received.isoformat(), "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "requested_symbols": symbols, "parser_version": PARSER_VERSION,
    }
    parse_error = None
    try:
        quotes = _parse(raw, symbols, received, destination / "source.bin")
        metadata["quotes"] = quotes
        metadata["status"] = "parsed"
    except ValueError as exc:
        parse_error = exc
        metadata.update(status="rejected", error=str(exc))
    with tempfile.TemporaryDirectory(dir=evidence_dir) as temporary:
        stage = Path(temporary) / "receipt"
        stage.mkdir()
        (stage / "source.bin").write_bytes(raw)
        (stage / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        os.rename(stage, destination)
    if parse_error is not None:
        raise parse_error
    return quotes
