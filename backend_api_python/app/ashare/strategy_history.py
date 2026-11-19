"""Bounded unadjusted BaoStock daily evidence for retrospective paper research."""
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from psycopg2.extensions import TRANSACTION_STATUS_IDLE
from psycopg2.extras import RealDictCursor

from app.ashare.broker import CENT, SHANGHAI, aware, supported
from app.ashare.calendar_rules import _calendar
from app.ashare.securities import FIELDS, HISTORY_URL, MAX_SOURCE_BYTES, _canonical, _day, _mapping, _rows, normalize


SESSIONS = 60
MAX_SYMBOLS = 3
MAX_RANGE_BYTES = 128 * 1024
MAX_RANGE_RECEIPTS = 8
DEFAULT_EVIDENCE_DIR = Path("/app/data/evidence")
_HEX = re.compile(r"[a-f0-9]{64}")
_PRICE = re.compile(r"[0-9]{1,9}(?:\.[0-9]{1,8})?")
_VOLUME = re.compile(r"[0-9]{1,16}")


class HistoricalEvidenceError(ValueError):
    """A bounded history observation cannot support a backtest."""


@dataclass(frozen=True)
class Bar:
    day: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    preclose: Decimal


def history_window(as_of):
    decision_day = aware(as_of).astimezone(SHANGHAI).date()
    try:
        calendar, version = _calendar()
        earliest, latest = calendar.first_session.date(), calendar.last_session.date()
        if not earliest <= decision_day <= latest:
            raise ValueError()
        first = max(earliest, decision_day - timedelta(days=120))
        days = calendar.sessions_in_range(first.isoformat(), (decision_day - timedelta(days=1)).isoformat())[-SESSIONS:]
        if len(days) != SESSIONS:
            raise ValueError()
    except (ImportError, OverflowError, AttributeError, TypeError, KeyError, ValueError):
        raise HistoricalEvidenceError("calendar_window_unverified") from None
    return tuple(session.date() for session in days), version


def _symbols(symbols):
    if (not isinstance(symbols, (list, tuple)) or not 1 <= len(symbols) <= MAX_SYMBOLS
            or any(not supported(symbol) or not symbol.isascii() for symbol in symbols)
            or len(set(symbols)) != len(symbols)):
        raise ValueError("invalid_history_symbols")


def _price(value):
    if not _PRICE.fullmatch(value):
        raise HistoricalEvidenceError("invalid_historical_ohlc")
    price = Decimal(value)
    if price <= 0 or price != price.quantize(CENT):
        raise HistoricalEvidenceError("invalid_historical_ohlc")
    return price.quantize(CENT)


def _bar(row, symbol, day):
    if (row["date"] != day.isoformat() or row["code"] != symbol or row["adjustflag"] != "3"):
        raise HistoricalEvidenceError("historical_bar_scope_mismatch")
    if row["tradestatus"] != "1" or row["isST"] != "0":
        raise HistoricalEvidenceError("unverified_or_ineligible_historical_status")
    if not _VOLUME.fullmatch(row["volume"]) or not int(row["volume"]):
        raise HistoricalEvidenceError("invalid_historical_volume")
    bar = Bar(row["date"], *(_price(row[key]) for key in ("open", "high", "low", "close", "preclose")))
    if not (bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high):
        raise HistoricalEvidenceError("invalid_historical_ohlc")
    return bar


def _table(table):
    try:
        rows = _mapping(table, FIELDS.split(","))
        if set(table["fields"]) != set(FIELDS.split(",")):
            raise ValueError()
        return rows
    except (ValueError, TypeError, KeyError):
        raise HistoricalEvidenceError("invalid_historical_schema") from None


def _continuity(bars):
    if any(previous.close != current.preclose for previous, current in zip(bars, bars[1:])):
        raise HistoricalEvidenceError("historical_preclose_discontinuity")
    return bars


def _range_request(symbol, days):
    return {"symbol": symbol, "start_date": days[0].isoformat(), "end_date": days[-1].isoformat(),
            "api": "query_history_k_data_plus", "frequency": "d", "adjustflag": "3", "fields": FIELDS}


def _range_bars(source, symbol, days):
    if (not isinstance(source, dict) or source.get("request") != _range_request(symbol, days)
            or not isinstance(source.get("sdk_version"), str) or len(source["sdk_version"]) > 100):
        raise HistoricalEvidenceError("historical_receipt_scope_mismatch")
    rows = _table(source.get("bars"))
    if len(rows) != len(days) or [row["date"] for row in rows] != [day.isoformat() for day in days]:
        raise HistoricalEvidenceError("historical_bars_missing_or_noncontiguous")
    return _continuity([_bar(row, symbol, day) for day, row in zip(days, rows)])


def _root(evidence_dir):
    return Path(evidence_dir if evidence_dir is not None else DEFAULT_EVIDENCE_DIR).resolve()


def _within(root, path):
    try:
        resolved = path.resolve()
        if resolved != path or not resolved.is_relative_to(root):
            raise ValueError()
    except (OSError, RuntimeError, ValueError):
        raise HistoricalEvidenceError("historical_archive_path_invalid") from None
    return resolved


def _range_dir(root, symbol, days):
    return _within(root, root / "strategy-history" / symbol / f"{days[0]}--{days[-1]}")


def _read_json(path, digest, size):
    if not _HEX.fullmatch(digest):
        raise HistoricalEvidenceError("historical_evidence_hash_invalid")
    try:
        if not path.is_file():
            raise OSError()
        with path.open("rb") as source:
            raw = source.read(size + 1)
    except OSError:
        raise HistoricalEvidenceError("historical_evidence_file_unavailable") from None
    if len(raw) > size or hashlib.sha256(raw).hexdigest() != digest:
        raise HistoricalEvidenceError("historical_evidence_hash_mismatch")
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):
        raise HistoricalEvidenceError("historical_evidence_invalid") from None


def _receipt_time(value, day):
    try:
        stamp = aware(value)
        local = stamp.astimezone(SHANGHAI)
        if local.date() < day or (local.date() == day and local.time() < time(19)):
            raise ValueError()
        return stamp.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise HistoricalEvidenceError("historical_receipt_time_invalid") from None


def _range_history(root, symbol, days, as_of):
    folder = _range_dir(root, symbol, days)
    if not folder.exists():
        return None
    entries = []
    try:
        for index, path in enumerate(folder.iterdir()):
            if index >= 2 * MAX_RANGE_RECEIPTS:
                raise HistoricalEvidenceError("historical_receipt_count_exceeded")
            if path.suffix != ".json":
                continue
            if len(entries) == MAX_RANGE_RECEIPTS:
                raise HistoricalEvidenceError("historical_receipt_count_exceeded")
            entries.append(_within(root, path))
    except OSError:
        raise HistoricalEvidenceError("historical_evidence_file_unavailable") from None
    eligible = []
    for path in entries:
        item = _read_json(path, path.stem, MAX_RANGE_BYTES)
        if (not isinstance(item, dict) or set(item) != {"schema_version", "received_at", "source_url",
                "provider_published_at", "source_sha256", "source"}
                or type(item["schema_version"]) is not int or item["schema_version"] != 1
                or item["source_url"] != HISTORY_URL or item["provider_published_at"] is not None):
            raise HistoricalEvidenceError("historical_evidence_invalid")
        received = _receipt_time(item.get("received_at"), days[-1])
        if received <= as_of:
            eligible.append((received, path.stem, item))
    if not eligible:
        return None
    received, receipt, item = max(eligible, key=lambda row: (row[0], row[1]))
    source = item.get("source")
    source_hash = hashlib.sha256(_canonical(source)).hexdigest()
    if source_hash != item.get("source_sha256"):
        raise HistoricalEvidenceError("historical_evidence_hash_mismatch")
    return _range_bars(source, symbol, days), [{"receipt_id": receipt, "source_sha256": source_hash,
                                               "received_at": received.isoformat()}]


def _daily_history(connection, root, symbol, days, as_of):
    if connection.autocommit or connection.get_transaction_status() != TRANSACTION_STATUS_IDLE:
        raise HistoricalEvidenceError("historical_evidence_idle_connection_required")
    with connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute("SET LOCAL statement_timeout = '5s'")
        cursor.execute("""SELECT DISTINCT ON (r.trading_day)
            r.id,r.trading_day,r.received_at,r.source_hash,r.evidence_path,s.details
            FROM qd_ashare_security_receipts r JOIN qd_ashare_security_statuses s ON s.receipt_id=r.id
            WHERE s.symbol=%s AND r.trading_day=ANY(%s) AND r.received_at<=%s AND r.source='baostock'
            ORDER BY r.trading_day,r.received_at DESC,r.id DESC""", (symbol, list(days), as_of))
        receipts = cursor.fetchall()
    if tuple(row["trading_day"] for row in receipts) != days:
        raise HistoricalEvidenceError("historical_bars_missing_or_noncontiguous")
    bars, proofs = [], []
    for row in receipts:
        day = row["trading_day"]
        received = _receipt_time(row["received_at"], day)
        path = _within(root, Path(row["evidence_path"]))
        payload = _read_json(path, row["source_hash"], MAX_SOURCE_BYTES)
        try:
            _, statuses = normalize(payload, day, payload["request"]["symbols"])
            status = next(item for item in statuses if item["symbol"] == symbol)
            if status != row["details"] or status["suspended"] is not False or status["st"] is not False:
                raise ValueError()
            raw_bar, = _table(payload["bars"][symbol])
        except (ValueError, KeyError, TypeError, StopIteration):
            raise HistoricalEvidenceError("historical_daily_status_mismatch") from None
        bars.append(_bar(raw_bar, symbol, day))
        proofs.append({"receipt_id": row["id"], "source_sha256": row["source_hash"],
                       "received_at": received.isoformat(), "session": day.isoformat()})
    return _continuity(bars), proofs


def load_history(connection, symbol, days, as_of, evidence_dir=None):
    """Read an exact calendar window without migrations, network or writes."""
    root = _root(evidence_dir)
    standalone = _range_history(root, symbol, days, as_of)
    if standalone is not None:
        bars, proofs = standalone
        return bars, "range_archive", proofs
    if connection is None:
        raise HistoricalEvidenceError("historical_bars_missing_or_noncontiguous")
    bars, proofs = _daily_history(connection, root, symbol, days, as_of)
    return bars, "daily_receipts", proofs


def _provider_fetch(symbols, start, end):
    import baostock as bs

    responses = {}
    with redirect_stdout(io.StringIO()):
        login = bs.login()
        if str(login.error_code) != "0":
            raise ValueError("baostock login rejected")
        try:
            for symbol in symbols:
                try:
                    responses[symbol] = _rows(bs.query_history_k_data_plus(
                        symbol, FIELDS, start_date=start, end_date=end, frequency="d", adjustflag="3"),
                        max_rows=SESSIONS)
                except ValueError:
                    responses[symbol] = None
        finally:
            bs.logout()
    return {"sdk_version": getattr(bs, "__version__", "unknown"), "bars": responses}


def _fetch_ranges(symbols, days, timeout):
    try:
        result = subprocess.run([sys.executable, "-m", "app.ashare.strategy_history", "_provider"],
                                input=json.dumps({"symbols": symbols, "start": days[0].isoformat(),
                                                  "end": days[-1].isoformat()}),
                                capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise HistoricalEvidenceError("baostock_provider_timeout") from None
    if result.returncode or len(result.stdout.encode()) > MAX_RANGE_BYTES * len(symbols):
        raise HistoricalEvidenceError("baostock_provider_failed")
    try:
        payload = json.loads(result.stdout)
        if (not isinstance(payload, dict) or not isinstance(payload.get("bars"), dict)
                or set(payload["bars"]) != set(symbols)):
            raise ValueError()
        return payload
    except (ValueError, KeyError, TypeError):
        raise HistoricalEvidenceError("baostock_provider_invalid_response") from None


def _archive_range(root, source, symbol, days, received):
    _range_bars(source, symbol, days)
    source_hash = hashlib.sha256(_canonical(source)).hexdigest()
    item = {"schema_version": 1, "received_at": received.isoformat(), "source_url": HISTORY_URL,
            "provider_published_at": None, "source_sha256": source_hash, "source": source}
    raw = _canonical(item)
    if len(raw) > MAX_RANGE_BYTES:
        raise HistoricalEvidenceError("historical_evidence_size_exceeded")
    receipt = hashlib.sha256(raw).hexdigest()
    folder = _range_dir(root, symbol, days)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=folder) as temporary:
            staged = Path(temporary) / "receipt"
            staged.write_bytes(raw)
            try:
                os.link(staged, folder / f"{receipt}.json")
            except FileExistsError:
                _read_json(folder / f"{receipt}.json", receipt, MAX_RANGE_BYTES)
    except OSError:
        raise HistoricalEvidenceError("historical_archive_write_failed") from None
    return {"receipt_id": receipt, "source_sha256": source_hash, "received_at": received.isoformat()}


def collect_history(symbols, *, as_of, evidence_dir=None, timeout=30):
    """Optional scheduler step. Collect a verified range with one bounded child login."""
    _symbols(symbols)
    if type(timeout) not in (int, float) or not 1 <= timeout <= 120:
        raise ValueError("invalid_history_timeout")
    as_of = aware(as_of)
    try:
        days, _ = history_window(as_of)
        if days[-1] >= datetime.now(SHANGHAI).date():
            raise HistoricalEvidenceError("history_collection_requires_closed_sessions")
    except HistoricalEvidenceError as exc:
        return {symbol: {"status": "insufficient_evidence", "reason_code": str(exc)} for symbol in symbols}
    root = _root(evidence_dir)
    results, pending = {}, []
    for symbol in symbols:
        try:
            current = _range_history(root, symbol, days, as_of)
            if current:
                results[symbol] = {"status": "already_available", "reason_code": "verified_range_available",
                                   "evidence": current[1][0]}
            else:
                pending.append(symbol)
        except HistoricalEvidenceError as exc:
            results[symbol] = {"status": "insufficient_evidence", "reason_code": str(exc)}
    if not pending:
        return results
    try:
        payload = _fetch_ranges(pending, days, timeout)
    except HistoricalEvidenceError as exc:
        return {**results, **{symbol: {"status": "insufficient_evidence", "reason_code": str(exc)} for symbol in pending}}
    received = datetime.now(timezone.utc)
    for symbol in pending:
        try:
            source = {"request": _range_request(symbol, days), "bars": payload["bars"][symbol],
                      "sdk_version": payload.get("sdk_version")}
            evidence = _archive_range(root, source, symbol, days, received)
            results[symbol] = {"status": "collected", "reason_code": "verified_range_collected", "evidence": evidence}
        except HistoricalEvidenceError as exc:
            results[symbol] = {"status": "insufficient_evidence", "reason_code": str(exc)}
    return results


if __name__ == "__main__":
    if sys.argv[1:] != ["_provider"]:
        raise SystemExit(2)
    try:
        request = json.loads(sys.stdin.read(2048))
        start, end = _day(request["start"]), _day(request["end"])
        _symbols(request["symbols"])
        if not start <= end < datetime.now(SHANGHAI).date() or (end - start).days > 120:
            raise ValueError()
        print(json.dumps(_provider_fetch(request["symbols"], start.isoformat(), end.isoformat())))
    except Exception:
        print("baostock historical provider failed", file=sys.stderr)
        raise SystemExit(1) from None
