"""Dated BaoStock observations, conservative knowledge times and immutable evidence.

A daily bar is an end-of-day observation, never evidence of next-day status.
The archived source is the SDK's original decoded fields/rows, not wire bytes.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

from psycopg2.extras import RealDictCursor

from app.ashare.market import SHANGHAI, SYMBOL

SOURCE_URL = "https://www.baostock.com/mainContent?file=stock_info.md"
HISTORY_URL = "https://www.baostock.com/mainContent?file=history_K_data.md"
FIELDS = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,tradestatus,isST"
PARSER_VERSION = "baostock-dated-security-v1"
MAX_SOURCE_BYTES = 16 * 1024 * 1024


def _day(value):
    if isinstance(value, datetime):
        raise ValueError("calendar date required")
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("ISO trading date required")
    result = date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError("ISO trading date required")
    return result


def _instant(value):
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.utcoffset() is None:
        raise ValueError("timezone-aware knowledge time required")
    return result


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _rows(response, *, max_rows=20000):
    if str(response.error_code) != "0":
        raise ValueError("baostock query rejected")
    fields, rows = list(response.fields), []
    while response.next():
        if str(response.error_code) != "0":
            raise ValueError("baostock pagination rejected")
        row = list(response.get_row_data())
        if len(row) != len(fields) or len(rows) >= max_rows:
            raise ValueError("baostock response bounds/schema rejected")
        rows.append(row)
    if str(response.error_code) != "0":
        raise ValueError("baostock pagination incomplete")
    return {"fields": fields, "rows": rows}


def _provider_fetch(trading_day, symbols):
    # SDK uses process-global sockets and prints login messages. Run only in child.
    import baostock as bs
    with redirect_stdout(io.StringIO()):
        login = bs.login()
        if str(login.error_code) != "0":
            raise ValueError("baostock login rejected")
        try:
            universe = _rows(bs.query_all_stock(day=trading_day))
            bars = {symbol: _rows(bs.query_history_k_data_plus(
                symbol, FIELDS, start_date=trading_day, end_date=trading_day,
                frequency="d", adjustflag="3"), max_rows=1) for symbol in symbols}
        finally:
            bs.logout()
    return {"request": {"trading_day": trading_day, "symbols": symbols,
                        "universe_api": "query_all_stock", "bars_api": "query_history_k_data_plus",
                        "frequency": "d", "adjustflag": "3", "fields": FIELDS},
            "universe": universe, "bars": bars, "sdk_version": getattr(bs, "__version__", "unknown")}


def fetch_baostock(trading_day, symbols, timeout=30):
    """Bound the SDK's global socket lifetime with a disposable child process."""
    trading_day = _day(trading_day)
    _symbols(symbols)
    if type(timeout) not in (int, float) or not 1 <= timeout <= 120:
        raise ValueError("provider timeout must be 1..120 seconds")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "app.ashare.securities", "_provider"],
            input=json.dumps({"trading_day": trading_day.isoformat(), "symbols": symbols}),
            capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise ValueError("baostock_provider_timeout") from None
    if result.returncode or len(result.stdout.encode()) > MAX_SOURCE_BYTES:
        raise ValueError("baostock_provider_failed")
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError):
        raise ValueError("baostock_provider_invalid_response") from None


def _symbols(symbols):
    if (not isinstance(symbols, list) or len(symbols) > 30
            or any(not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol) for symbol in symbols)
            or len(set(symbols)) != len(symbols)):
        raise ValueError("0..30 distinct supported A-share symbols required")


def _mapping(table, required):
    if not isinstance(table, dict) or set(table) != {"fields", "rows"}:
        raise ValueError("invalid provider table")
    fields = table["fields"]
    if (not isinstance(fields, list) or not all(isinstance(field, str) for field in fields)
            or len(set(fields)) != len(fields) or not set(required) <= set(fields)):
        raise ValueError("missing/duplicate provider fields")
    rows = table["rows"]
    if not isinstance(rows, list) or len(rows) > 20000:
        raise ValueError("invalid provider rows")
    if any(not isinstance(row, list) or len(row) != len(fields)
           or not all(isinstance(value, str) and len(value) < 1024 for value in row) for row in rows):
        raise ValueError("invalid provider values")
    return [dict(zip(fields, row)) for row in rows]


def _optional_bool(value):
    if value == "":
        return None
    if value not in ("0", "1"):
        raise ValueError("invalid provider status flag")
    return value == "1"


def _price(value):
    if value == "":
        return None
    try:
        number = Decimal(value)
        if not number.is_finite() or number <= 0 or number > Decimal("1e10"):
            raise ValueError("invalid provider price")
        return format(number, "f")
    except InvalidOperation:
        raise ValueError("invalid provider price") from None


def normalize(payload, trading_day, symbols):
    """Reject incomplete scope; absent provider status remains unknown."""
    day = _day(trading_day).isoformat()
    _symbols(symbols)
    if not isinstance(payload, dict):
        raise ValueError("invalid provider response")
    request = payload.get("request", {})
    if not isinstance(request, dict):
        raise ValueError("invalid provider request")
    if (request.get("trading_day") != day or request.get("symbols") != symbols
            or request.get("universe_api") != "query_all_stock"
            or request.get("bars_api") != "query_history_k_data_plus"
            or request.get("frequency") != "d" or request.get("adjustflag") != "3"
            or request.get("fields") != FIELDS):
        raise ValueError("provider request/date mismatch")
    members, all_codes = [], set()
    for row in _mapping(payload.get("universe"), ("code", "tradeStatus", "code_name")):
        symbol = row["code"]
        if symbol in all_codes:
            raise ValueError("duplicate universe member")
        all_codes.add(symbol)
        tradable = _optional_bool(row["tradeStatus"])
        if SYMBOL.fullmatch(symbol):
            members.append({"symbol": symbol, "name": row["code_name"],
                            "trade_status": row["tradeStatus"] or None,
                            "suspended": None if tradable is None else not tradable})
    if not members:
        raise ValueError("empty supported historical universe")
    member_map = {member["symbol"]: member for member in members}
    bars = payload.get("bars", {})
    if not isinstance(bars, dict) or set(bars) != set(symbols):
        raise ValueError("provider bar scope incomplete")
    statuses = []
    for symbol in symbols:
        rows = _mapping(bars[symbol], FIELDS.split(","))
        if symbol not in member_map:
            if rows:
                raise ValueError("bar security absent from dated universe")
            statuses.append({"symbol": symbol, "is_st": None, "st": None, "suspended": None,
                             "trade_status": None, "previous_close": None, "close": None,
                             "missing_reason": "not_in_dated_universe", "listing_day": None,
                             "special_listing_status": None, "corporate_action_status": None})
            continue
        if len(rows) > 1:
            raise ValueError("duplicate daily status")
        # A missing daily bar is not a declaration of suspension/delisting.
        if not rows:
            statuses.append({"symbol": symbol, "is_st": None, "st": None, "suspended": None,
                             "trade_status": None, "previous_close": None, "close": None,
                             "missing_reason": "daily_bar_missing", "listing_day": None,
                             "special_listing_status": None, "corporate_action_status": None})
            continue
        row = rows[0]
        if row["date"] != day or row["code"] != symbol or row["adjustflag"] != "3":
            raise ValueError("daily status date/security/adjustment mismatch")
        tradable, is_st = _optional_bool(row["tradestatus"]), _optional_bool(row["isST"])
        suspended = None if tradable is None else not tradable
        if member_map[symbol]["suspended"] is not None and suspended is not None and member_map[symbol]["suspended"] != suspended:
            raise ValueError("universe/bar status conflict")
        statuses.append({"symbol": symbol, "is_st": is_st, "st": is_st, "suspended": suspended,
                         "trade_status": row["tradestatus"] or None,
                         "previous_close": _price(row["preclose"]), "close": _price(row["close"]),
                         "missing_reason": None, "listing_day": None, "special_listing_status": None,
                         "corporate_action_status": None})
    return members, statuses


class SecuritiesStore:
    """Shared public market-data observations, without account/approval writes."""
    def __init__(self, connection):
        self.connection = connection

    @staticmethod
    def _evidence(row):
        path = Path(row["evidence_path"])
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
            raise ValueError("security_evidence_unavailable")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["source_hash"]:
            raise ValueError("security_evidence_hash_mismatch")
        try:
            payload = json.loads(raw)
            normalized = normalize(payload, row["trading_day"], payload["request"]["symbols"])
        except (ValueError, TypeError, KeyError):
            raise ValueError("security_evidence_invalid") from None
        if "details" in row:
            expected = next((status for status in normalized[1] if status["symbol"] == row["requested_symbol"]), None)
            if row["details"] != expected:
                raise ValueError("security_evidence_details_mismatch")
        return normalized

    @staticmethod
    def _provenance(row):
        received = row["received_at"].isoformat()
        return {"receipt_id": row["id"], "trading_day": row["trading_day"].isoformat(),
                "source": "baostock", "source_url": SOURCE_URL, "history_source_url": HISTORY_URL,
                "source_hash": row["source_hash"], "evidence_sha256": row["source_hash"],
                "evidence_path": row["evidence_path"], "received_at": received, "available_at": received,
                "source_time": None, "quality": "provider_reported_eod_not_exchange_certified",
                "timestamp_semantics": "retrieved_after_close_not_certified_point_in_time",
                "reviewed": False, "auto_approval_eligible": False}

    def status(self, symbol, trading_day, known_at=None):
        _symbols([symbol])
        day, known_at = _day(trading_day), _instant(known_at or datetime.now(timezone.utc))
        with self.connection, self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""SELECT r.*, s.details, s.symbol AS requested_symbol FROM qd_ashare_security_receipts r
                JOIN qd_ashare_security_statuses s ON s.receipt_id=r.id
                WHERE r.trading_day=%s AND s.symbol=%s AND r.received_at<=%s
                ORDER BY r.received_at DESC,r.id DESC LIMIT 1""", (day, symbol, known_at))
            row = cursor.fetchone()
        if not row:
            return None
        self._evidence(row)
        return {**row["details"], **self._provenance(row)}

    def universe(self, trading_day, known_at=None, limit=500, offset=0):
        day, known_at = _day(trading_day), _instant(known_at or datetime.now(timezone.utc))
        if type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or not 0 <= offset <= 20000:
            raise ValueError("invalid universe page")
        with self.connection, self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""SELECT * FROM qd_ashare_security_receipts
                WHERE trading_day=%s AND received_at<=%s
                ORDER BY received_at DESC,id DESC LIMIT 1""", (day, known_at))
            row = cursor.fetchone()
            if not row:
                return {"trading_day": day.isoformat(), "members": [], "total": 0, "status": "not_observed"}
            cursor.execute("""SELECT details FROM qd_ashare_universe_members WHERE receipt_id=%s
                ORDER BY symbol LIMIT %s OFFSET %s""", (row["id"], limit, offset))
            members = [dict(item["details"]) for item in cursor.fetchall()]
        source_members, _ = self._evidence(row)
        expected = sorted(source_members, key=lambda member: member["symbol"])[offset:offset + limit]
        if members != expected or row["universe_count"] != len(source_members):
            raise ValueError("security_evidence_details_mismatch")
        return {**self._provenance(row), "members": members, "total": row["universe_count"],
                "status": "observed", "offset": offset, "limit": limit,
                "complete_exchange_universe_certified": False}

    def archive(self, payload, trading_day, symbols, evidence_dir, received_at=None):
        """Validate and append one receipt. No upserts alter prior observations."""
        received = _instant(received_at or datetime.now(timezone.utc))
        day = _day(trading_day)
        if day > received.astimezone(SHANGHAI).date():
            raise ValueError("future security date rejected")
        if day == received.astimezone(SHANGHAI).date() and received.astimezone(SHANGHAI).time() < time(19):
            raise ValueError("current-day EOD observation requires conservative 19:00 cutoff")
        members, statuses = normalize(payload, day, symbols)
        raw = _canonical(payload)
        if len(raw) > MAX_SOURCE_BYTES:
            raise ValueError("security evidence exceeds size limit")
        source_hash = hashlib.sha256(raw).hexdigest()
        receipt_id = uuid.uuid4().hex
        evidence_dir = Path(evidence_dir).resolve()
        evidence_dir.mkdir(parents=True, exist_ok=True)
        destination = evidence_dir / ("securities-" + receipt_id)
        metadata = {"schema_version": 1, "parser_version": PARSER_VERSION, "source": "baostock",
                    "source_url": SOURCE_URL, "received_at": received.isoformat(),
                    "trading_day": day.isoformat(), "source_hash": source_hash,
                    "source_representation": "canonical_original_sdk_decoded_fields_and_rows_not_wire_bytes",
                    "provider_published_at": None, "universe_scope": "supported_shanghai_shenzhen_a_shares",
                    "complete_exchange_universe_certified": False,
                    "universe_count": len(members), "status_count": len(statuses)}
        with tempfile.TemporaryDirectory(dir=evidence_dir) as temporary:
            staged = Path(temporary) / "receipt"
            staged.mkdir()
            (staged / "source.json").write_bytes(raw)
            (staged / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
            staged.rename(destination)
        with self.connection, self.connection.cursor() as cursor:
            cursor.execute("""INSERT INTO qd_ashare_security_receipts
                (id,trading_day,received_at,source,source_hash,evidence_path,universe_count,status_count,metadata)
                VALUES(%s,%s,%s,'baostock',%s,%s,%s,%s,%s::jsonb)""",
                (receipt_id, day, received, source_hash, str(destination / "source.json"),
                 len(members), len(statuses), json.dumps(metadata)))
            cursor.executemany("INSERT INTO qd_ashare_universe_members VALUES(%s,%s,%s::jsonb)",
                               [(receipt_id, row["symbol"], json.dumps(row)) for row in members])
            if statuses:
                cursor.executemany("INSERT INTO qd_ashare_security_statuses VALUES(%s,%s,%s::jsonb)",
                                   [(receipt_id, row["symbol"], json.dumps(row)) for row in statuses])
        return {**metadata, "receipt_id": receipt_id, "evidence_path": str(destination / "source.json")}


def ingest_baostock(connection, evidence_dir, trading_day, symbols=None, timeout=30):
    symbols = [] if symbols is None else symbols
    day = _day(trading_day)
    now = datetime.now(timezone.utc)
    if day > now.astimezone(SHANGHAI).date() or (day == now.astimezone(SHANGHAI).date() and now.astimezone(SHANGHAI).time() < time(19)):
        raise ValueError("only completed-day observations may be ingested")
    payload = fetch_baostock(day, symbols, timeout)
    return SecuritiesStore(connection).archive(payload, day, symbols, evidence_dir)


def main():
    parser = argparse.ArgumentParser(description="Read-only BaoStock market data; no broker or approval writes")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("_provider", help=argparse.SUPPRESS)
    ingest = sub.add_parser("ingest")
    ingest.add_argument("--day", required=True)
    ingest.add_argument("--symbol", action="append", default=[])
    ingest.add_argument("--evidence-dir", required=True)
    ingest.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    if args.command == "_provider":
        try:
            request = json.loads(sys.stdin.read(MAX_SOURCE_BYTES))
            print(json.dumps(_provider_fetch(request["trading_day"], request["symbols"]), ensure_ascii=False))
        except Exception:
            print("baostock provider failed", file=sys.stderr)
            return 1
        return 0
    import psycopg2
    from app.ashare.schema import get_database_url
    dsn = get_database_url()
    if not dsn:
        parser.error("ASHARE_DATABASE_URL or DATABASE_URL required")
    connection = psycopg2.connect(dsn, connect_timeout=10, options="-c statement_timeout=30000 -c lock_timeout=5000")
    try:
        result = ingest_baostock(connection, args.evidence_dir, args.day, args.symbol, args.timeout)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "auto_approved": False}))
        return 1
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
