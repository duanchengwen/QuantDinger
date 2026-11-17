"""Bounded current Eastmoney theme observations with immutable raw evidence.

There is no historical-date fetch, inferred publication time, approval or order
write. A paginated observation is not an atomic exchange-certified constituent
history. Production ingestion owns its clock and accepts no observation time.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

from psycopg2.extras import RealDictCursor
import requests

from app.ashare.market import SYMBOL

CATALOG_URL = "https://79.push2.eastmoney.com/api/qt/clist/get"
MEMBERS_URL = "https://29.push2.eastmoney.com/api/qt/clist/get"
SOURCE = "eastmoney"
PARSER_VERSION = "eastmoney-current-themes-v1"
PAGE_SIZE = 100
MAX_REQUESTS = 64
MAX_PAGE_BYTES = 262144
MAX_TOTAL_BYTES = 8 * 1024 * 1024
BOARD_CODE = re.compile(r"BK[0-9]{4,8}\Z")


class ThemeError(ValueError):
    """Stable, credential-free errors suitable for operational reports."""


def _utcnow():
    return datetime.now(timezone.utc)


def _instant(value):
    try:
        result = datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError:
        raise ThemeError("invalid_knowledge_time") from None
    if not isinstance(result, datetime) or result.utcoffset() is None:
        raise ThemeError("aware_knowledge_time_required")
    return result.astimezone(timezone.utc)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _name(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 160 or value != value.strip()
            or any(ord(char) < 32 for char in value)):
        raise ThemeError("invalid_theme_name")
    return value


def _board(value):
    if not isinstance(value, str) or not BOARD_CODE.fullmatch(value):
        raise ThemeError("invalid_board_code")
    return value


def _selection(board_code, expected_name):
    if board_code is None and expected_name is None:
        raise ThemeError("explicit_board_or_name_required")
    if board_code is not None:
        _board(board_code)
    if expected_name is not None:
        _name(expected_name)


def _params(kind, page, board_code=None):
    return {"pn": str(page), "pz": str(PAGE_SIZE), "po": "0", "np": "1", "fltt": "2",
            "invt": "2", "fid": "f12", "fs": "m:90 t:3 f:!50" if kind == "catalog" else f"b:{_board(board_code)} f:!50",
            "fields": "f12,f14" if kind == "catalog" else "f12,f13,f14"}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ThemeError("duplicate_json_key")
        result[key] = value
    return result


def _decode(raw):
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError) as exc:
        if isinstance(exc, ThemeError):
            raise
        raise ThemeError("invalid_provider_json") from None
    if not isinstance(result, dict) or type(result.get("rc")) is not int or result["rc"] != 0:
        raise ThemeError("provider_rejected")
    data = result.get("data")
    if not isinstance(data, dict) or type(data.get("total")) is not int or not 1 <= data["total"] <= 10000:
        raise ThemeError("invalid_provider_total")
    rows = data.get("diff")
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, list) or len(rows) > PAGE_SIZE or not all(isinstance(row, dict) for row in rows):
        raise ThemeError("invalid_provider_rows")
    return data["total"], rows


def _identity(row, kind):
    if kind == "catalog":
        return _board(row.get("f12"))
    code, market = row.get("f12"), row.get("f13")
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
        raise ThemeError("invalid_member_code")
    if type(market) is not int or market not in (0, 1, 2):
        raise ThemeError("invalid_member_exchange")
    return f"{market}.{code}"


def _validate_page_rows(rows, kind):
    identities = []
    for row in rows:
        identities.append(_identity(row, kind))
        _name(row.get("f14"))
    if len(set(identities)) != len(identities):
        raise ThemeError("duplicate_provider_identity")
    return identities


class _HTTPReader:
    def __init__(self, timeout):
        self.deadline = time.monotonic() + timeout
        self.requests = 0
        self.bytes = 0
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": "QuantDinger-Theme-Research/1.0", "Accept-Encoding": "identity"})

    def get(self, kind, page, board_code=None, recheck=False):
        if self.requests >= MAX_REQUESTS or time.monotonic() >= self.deadline:
            raise ThemeError("theme_request_budget_exceeded")
        self.requests += 1
        url = CATALOG_URL if kind == "catalog" else MEMBERS_URL
        params = _params(kind, page, board_code)
        remaining = self.deadline - time.monotonic()
        try:
            with self.session.get(url, params=params, timeout=(min(3, remaining), min(5, remaining)),
                                  allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise ThemeError("theme_http_rejected")
                if response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
                    raise ThemeError("unexpected_response_encoding")
                raw = bytearray()
                for chunk in response.iter_content(8192):
                    raw.extend(chunk)
                    self.bytes += len(chunk)
                    if len(raw) > MAX_PAGE_BYTES or self.bytes > MAX_TOTAL_BYTES or time.monotonic() > self.deadline:
                        raise ThemeError("theme_response_budget_exceeded")
                return {"kind": kind, "page": page, "recheck": recheck, "url": url, "params": params,
                        "received_at": _utcnow().isoformat(), "raw": base64.b64encode(raw).decode("ascii")}
        except requests.RequestException:
            raise ThemeError("theme_transport_failed") from None

    def close(self):
        self.session.close()


def _collect(reader, kind, board_code=None):
    pages, rows, identities, total = [], [], set(), None
    page_number = 1
    while total is None or len(rows) < total:
        page = reader.get(kind, page_number, board_code)
        count, current = _decode(base64.b64decode(page["raw"], validate=True))
        total = count if total is None else total
        if count != total:
            raise ThemeError("provider_total_changed")
        if len(current) != min(PAGE_SIZE, total - len(rows)):
            raise ThemeError("provider_page_incomplete")
        current_ids = _validate_page_rows(current, kind)
        if identities.intersection(current_ids):
            raise ThemeError("duplicate_provider_identity")
        identities.update(current_ids)
        rows.extend(current)
        pages.append(page)
        page_number += 1
    # Re-reading the boundary catches observable churn; pagination is not atomic.
    check = reader.get(kind, 1, board_code, recheck=True)
    count, current = _decode(base64.b64decode(check["raw"], validate=True))
    first_rows = _decode(base64.b64decode(pages[0]["raw"], validate=True))[1]
    if count != total or current != first_rows:
        raise ThemeError("provider_membership_changed")
    pages.append(check)
    return pages, rows


def _select(catalog_rows, board_code, expected_name):
    selected = [row for row in catalog_rows if row["f12"] == board_code] if board_code else [
        row for row in catalog_rows if row["f14"] == expected_name]
    if len(selected) != 1:
        raise ThemeError("theme_missing_or_ambiguous")
    result = selected[0]
    if expected_name is not None and result["f14"] != expected_name:
        raise ThemeError("theme_name_mismatch")
    return result["f12"], result["f14"]


def _fetch_direct(board_code, expected_name, timeout, catalog_only=False):
    reader = _HTTPReader(timeout)
    started = _utcnow().isoformat()
    try:
        pages, catalog_rows = _collect(reader, "catalog")
        if catalog_only:
            return {"started_at": started, "pages": pages, "catalog_only": True}
        board_code, name = _select(catalog_rows, board_code, expected_name)
        member_pages, _ = _collect(reader, "members", board_code)
        return {"started_at": started, "board_code": board_code, "board_name": name,
                "expected_name": expected_name, "pages": pages + member_pages, "catalog_only": False}
    finally:
        reader.close()


def _fetch_isolated(board_code, expected_name, timeout, catalog_only=False):
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 120:
        raise ThemeError("invalid_theme_timeout")
    if not catalog_only:
        _selection(board_code, expected_name)
    try:
        result = subprocess.run([sys.executable, "-m", "app.ashare.themes", "_fetch"],
                                input=json.dumps({"board_code": board_code, "expected_name": expected_name,
                                                  "timeout": timeout, "catalog_only": catalog_only}),
                                capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise ThemeError("theme_fetch_timeout") from None
    if len(result.stdout.encode()) > MAX_TOTAL_BYTES * 2:
        raise ThemeError("theme_response_budget_exceeded")
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise ThemeError("theme_child_failed") from None
    if result.returncode or not isinstance(payload, dict):
        code = payload.get("error") if isinstance(payload, dict) else None
        raise ThemeError(code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,80}", code) else "theme_child_failed")
    return payload


def fetch_theme(board_code=None, expected_name=None, timeout=60):
    """Collect a full current catalog and one exact theme; no caller URL/date."""
    return _fetch_isolated(board_code, expected_name, timeout)


def fetch_catalog(timeout=30):
    """Collect the complete current catalog for explicit subsequent mapping."""
    return _fetch_isolated(None, None, timeout, catalog_only=True)


def _page_content(page):
    try:
        raw = base64.b64decode(page["raw"], validate=True)
    except (ValueError, KeyError, TypeError):
        raise ThemeError("invalid_theme_page_evidence") from None
    if len(raw) > MAX_PAGE_BYTES:
        raise ThemeError("theme_response_budget_exceeded")
    return raw


def _normalize_capture(capture, received_at):
    """Revalidate scope, all pages and temporal ordering, including on disk reads."""
    if not isinstance(capture, dict) or type(capture.get("catalog_only")) is not bool:
        raise ThemeError("invalid_theme_capture")
    pages = capture.get("pages")
    if not isinstance(pages, list) or not 2 <= len(pages) <= MAX_REQUESTS:
        raise ThemeError("invalid_theme_page_count")
    started, previous = _instant(capture.get("started_at")), _instant(capture.get("started_at"))
    received = _instant(received_at)
    if started > received:
        raise ThemeError("invalid_theme_time_order")
    rows_by_kind, total_bytes = {}, 0
    for kind in (["catalog"] if capture["catalog_only"] else ["catalog", "members"]):
        subset = [page for page in pages if isinstance(page, dict) and page.get("kind") == kind]
        if len(subset) < 2 or not subset[-1].get("recheck") or subset[-1].get("page") != 1:
            raise ThemeError("theme_missing_boundary_check")
        rows, identities, total = [], set(), None
        for index, page in enumerate(subset):
            checking = index == len(subset) - 1
            expected_page = 1 if checking else index + 1
            board = None if kind == "catalog" else _board(capture.get("board_code"))
            if (page.get("url") != (CATALOG_URL if kind == "catalog" else MEMBERS_URL)
                    or page.get("params") != _params(kind, expected_page, board)
                    or type(page.get("page")) is not int or page["page"] != expected_page
                    or type(page.get("recheck")) is not bool or page["recheck"] != checking):
                raise ThemeError("theme_page_scope_mismatch")
            page_time = _instant(page.get("received_at"))
            if not previous <= page_time <= received:
                raise ThemeError("invalid_theme_time_order")
            previous = page_time
            raw = _page_content(page)
            total_bytes += len(raw)
            if total_bytes > MAX_TOTAL_BYTES:
                raise ThemeError("theme_response_budget_exceeded")
            count, current = _decode(raw)
            if total is None:
                total = count
            if count != total:
                raise ThemeError("provider_total_changed")
            _validate_page_rows(current, kind)
            if checking:
                first = _decode(_page_content(subset[0]))[1]
                if current != first:
                    raise ThemeError("provider_membership_changed")
            else:
                if len(current) != min(PAGE_SIZE, total - len(rows)) or len(rows) >= total:
                    raise ThemeError("provider_page_incomplete")
                current_ids = {_identity(row, kind) for row in current}
                if identities.intersection(current_ids):
                    raise ThemeError("duplicate_provider_identity")
                identities.update(current_ids)
                rows.extend(current)
        if len(rows) != total:
            raise ThemeError("provider_page_incomplete")
        rows_by_kind[kind] = rows
    expected_kinds = {"catalog"} if capture["catalog_only"] else {"catalog", "members"}
    if any(not isinstance(page, dict) or page.get("kind") not in expected_kinds for page in pages):
        raise ThemeError("theme_page_scope_mismatch")
    kinds = [page["kind"] for page in pages]
    if kinds != sorted(kinds, key=lambda kind: 0 if kind == "catalog" else 1):
        raise ThemeError("theme_page_scope_mismatch")
    if capture["catalog_only"]:
        return {"catalog": [{"board_code": row["f12"], "board_name": row["f14"]} for row in rows_by_kind["catalog"]]}
    board, name = _select(rows_by_kind["catalog"], capture.get("board_code"), capture.get("expected_name"))
    if capture.get("board_name") != name:
        raise ThemeError("theme_name_mismatch")
    members, excluded = [], []
    for row in rows_by_kind["members"]:
        code, market = row["f12"], row["f13"]
        symbol = ("sh." if market == 1 else "sz.") + code
        details = {"symbol": symbol, "name": row["f14"], "provider_code": code, "provider_market": market}
        if market in (0, 1) and SYMBOL.fullmatch(symbol):
            members.append(details)
        else:
            excluded.append({"provider_code": code, "provider_market": market, "name": row["f14"],
                             "reason": "outside_supported_shanghai_shenzhen_a_share_scope"})
    if len({row["symbol"] for row in members}) != len(members):
        raise ThemeError("duplicate_provider_identity")
    return {"board_code": board, "board_name": name, "members": sorted(members, key=lambda row: row["symbol"]),
            "excluded_members": excluded, "provider_total": len(rows_by_kind["members"]),
            "member_count": len(members), "excluded_count": len(excluded)}


def _archive_capture(capture, evidence_dir):
    # The caller cannot supply/backdate received_at. Tests patch only this clock.
    received = _utcnow()
    normalized = _normalize_capture(capture, received)
    receipt_id = uuid.uuid4().hex
    manifest = {"schema_version": 1, "parser_version": PARSER_VERSION, "receipt_id": receipt_id,
                "source": SOURCE, "received_at": received.isoformat(), "published_at": None,
                "membership_semantics": "current_observation", "consistency": "paginated_not_atomic",
                "historical_membership_certified": False, "capture": {key: value for key, value in capture.items() if key != "pages"},
                "normalized": normalized, "pages": []}
    evidence_dir = Path(evidence_dir).resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    destination = evidence_dir / ("theme-" + receipt_id)
    with tempfile.TemporaryDirectory(dir=evidence_dir) as temporary:
        staged = Path(temporary) / "receipt"
        staged.mkdir()
        for index, page in enumerate(capture["pages"]):
            filename = f"page-{index:03d}.bin"
            raw = _page_content(page)
            (staged / filename).write_bytes(raw)
            manifest["pages"].append({**{key: value for key, value in page.items() if key != "raw"},
                                      "file": filename, "sha256": hashlib.sha256(raw).hexdigest()})
        raw_manifest = _canonical(manifest)
        (staged / "manifest.json").write_bytes(raw_manifest)
        staged.rename(destination)
    return manifest, str(destination / "manifest.json"), hashlib.sha256(raw_manifest).hexdigest()


def _verified_manifest(row):
    path = Path(row["evidence_path"])
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_TOTAL_BYTES:
        raise ThemeError("theme_evidence_unavailable")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != row["source_hash"]:
        raise ThemeError("theme_manifest_hash_mismatch")
    try:
        manifest = json.loads(raw, object_pairs_hook=_unique_object)
        if (manifest["schema_version"] != 1 or manifest["parser_version"] != PARSER_VERSION
                or manifest["receipt_id"] != row["id"] or manifest["source"] != SOURCE
                or manifest["published_at"] is not None or row["published_at"] is not None
                or manifest["membership_semantics"] != "current_observation"
                or _instant(manifest["received_at"]) != row["received_at"]):
            raise ThemeError("theme_manifest_scope_mismatch")
        capture = {**manifest["capture"], "pages": []}
        if not isinstance(manifest["pages"], list) or len(manifest["pages"]) > MAX_REQUESTS:
            raise ThemeError("invalid_theme_page_count")
        for index, page in enumerate(manifest["pages"]):
            filename = f"page-{index:03d}.bin"
            if page["file"] != filename:
                raise ThemeError("theme_evidence_path_rejected")
            raw_path = path.parent / filename
            if raw_path.is_symlink() or not raw_path.is_file() or raw_path.stat().st_size > MAX_PAGE_BYTES:
                raise ThemeError("theme_evidence_unavailable")
            raw_page = raw_path.read_bytes()
            if hashlib.sha256(raw_page).hexdigest() != page["sha256"]:
                raise ThemeError("theme_page_hash_mismatch")
            capture["pages"].append({key: value for key, value in page.items() if key not in ("file", "sha256")}
                                      | {"raw": base64.b64encode(raw_page).decode("ascii")})
        normalized = _normalize_capture(capture, row["received_at"])
        if (normalized != manifest["normalized"] or normalized["board_code"] != row["board_code"]
                or normalized["board_name"] != row["board_name"]
                or any(normalized[key] != row[key] for key in ("provider_total", "member_count", "excluded_count"))):
            raise ThemeError("theme_evidence_details_mismatch")
        return manifest, normalized
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ThemeError):
            raise
        raise ThemeError("theme_evidence_invalid") from None


class ThemeStore:
    def __init__(self, connection):
        self.connection = connection

    def snapshot(self, board_code, known_at=None, limit=100, offset=0):
        board_code = _board(board_code)
        known_at = _instant(known_at or _utcnow())
        if type(limit) is not int or not 1 <= limit <= 500 or type(offset) is not int or not 0 <= offset <= 10000:
            raise ThemeError("invalid_theme_page")
        with self.connection:
            return self._snapshot(board_code, known_at, limit, offset)

    def latest_snapshots(self, known_at=None, limit=20):
        """All-or-nothing verified summaries of the latest distinct boards."""
        known_at = _instant(known_at or _utcnow())
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ThemeError("invalid_theme_list_limit")
        with self.connection:
            with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""SELECT id,board_code FROM (
                    SELECT DISTINCT ON (board_code) id,board_code,received_at
                    FROM qd_ashare_theme_receipts WHERE received_at<=%s
                    ORDER BY board_code,received_at DESC,id DESC
                ) AS latest ORDER BY received_at DESC,board_code LIMIT %s""", (known_at, limit))
                selected = cursor.fetchall()
            keys = ("board_code", "board_name", "received_at", "source", "source_hash", "member_count",
                    "provider_total", "excluded_count", "membership_semantics", "historical_membership_certified")
            summaries = []
            for item in selected:
                snapshot = self._snapshot(item["board_code"], known_at, 1, 0)
                if snapshot.get("receipt_id") != item["id"]:
                    raise ThemeError("theme_snapshot_changed")
                summaries.append({key: snapshot[key] for key in keys})
        return {"themes": summaries, "status": "observed" if summaries else "not_observed"}

    def _snapshot(self, board_code, known_at, limit, offset):
        with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""SELECT * FROM qd_ashare_theme_receipts WHERE board_code=%s AND received_at<=%s
                ORDER BY received_at DESC,id DESC LIMIT 1""", (board_code, known_at))
            row = cursor.fetchone()
            if not row:
                return {"board_code": board_code, "status": "not_observed", "members": [], "member_count": 0,
                        "membership_semantics": "current_observation", "published_at": None}
            cursor.execute("SELECT symbol,details FROM qd_ashare_theme_members WHERE receipt_id=%s ORDER BY symbol", (row["id"],))
            db_members = cursor.fetchall()
        manifest, normalized = _verified_manifest(row)
        expected = normalized["members"]
        if (len(db_members) != len(expected) or any(db["symbol"] != member["symbol"] or db["details"] != member
                                                   for db, member in zip(db_members, expected))):
            raise ThemeError("theme_evidence_details_mismatch")
        return {"receipt_id": row["id"], "board_code": board_code, "board_name": row["board_name"],
                "status": "observed", "members": expected[offset:offset + limit], "offset": offset, "limit": limit,
                "member_count": row["member_count"], "provider_total": row["provider_total"],
                "excluded_count": row["excluded_count"], "excluded_members": normalized["excluded_members"],
                "received_at": row["received_at"].isoformat(), "known_at": row["received_at"].isoformat(),
                "published_at": None, "source": SOURCE, "source_hash": row["source_hash"],
                "evidence_path": row["evidence_path"], "source_urls": [CATALOG_URL, MEMBERS_URL],
                "membership_semantics": "current_observation", "consistency": manifest["consistency"],
                "historical_membership_certified": False, "auto_approval_eligible": False}


def ingest_theme(connection, evidence_dir, board_code=None, expected_name=None, timeout=60):
    """Fetch and archive actual current observations; no date/clock parameter."""
    capture = fetch_theme(board_code, expected_name, timeout)
    manifest, path, source_hash = _archive_capture(capture, evidence_dir)
    data = manifest["normalized"]
    with connection, connection.cursor() as cursor:
        cursor.execute("""INSERT INTO qd_ashare_theme_receipts
            (id,board_code,board_name,received_at,published_at,source,source_hash,evidence_path,
             member_count,provider_total,excluded_count,metadata)
            VALUES(%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
            (manifest["receipt_id"], data["board_code"], data["board_name"], manifest["received_at"], SOURCE,
             source_hash, path, data["member_count"], data["provider_total"], data["excluded_count"],
             json.dumps({"parser_version": PARSER_VERSION, "membership_semantics": "current_observation"})))
        if data["members"]:
            cursor.executemany("INSERT INTO qd_ashare_theme_members(receipt_id,symbol,details) VALUES(%s,%s,%s::jsonb)",
                               [(manifest["receipt_id"], member["symbol"], json.dumps(member)) for member in data["members"]])
    return {"receipt_id": manifest["receipt_id"], "board_code": data["board_code"], "board_name": data["board_name"],
            "received_at": manifest["received_at"], "published_at": None, "source": SOURCE,
            "source_hash": source_hash, "evidence_path": path, "member_count": data["member_count"],
            "provider_total": data["provider_total"], "excluded_count": data["excluded_count"],
            "membership_semantics": "current_observation", "auto_approval_eligible": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("_fetch", help=argparse.SUPPRESS)
    catalog = sub.add_parser("catalog")
    catalog.add_argument("--evidence-dir", type=Path, required=True)
    catalog.add_argument("--timeout", type=float, default=30)
    ingest = sub.add_parser("ingest")
    ingest.add_argument("--board-code")
    ingest.add_argument("--name")
    ingest.add_argument("--evidence-dir", type=Path, required=True)
    ingest.add_argument("--timeout", type=float, default=60)
    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--board-code", required=True)
    snapshot.add_argument("--known-at")
    snapshot.add_argument("--limit", type=int, default=100)
    snapshot.add_argument("--offset", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        if args.command == "_fetch":
            request = json.loads(sys.stdin.read(4096))
            print(json.dumps(_fetch_direct(**request), ensure_ascii=False))
            return 0
        if args.command == "catalog":
            manifest, path, digest = _archive_capture(fetch_catalog(args.timeout), args.evidence_dir)
            result = {**manifest["normalized"], "received_at": manifest["received_at"], "published_at": None,
                      "source_hash": digest, "evidence_path": path, "membership_semantics": "current_observation"}
        else:
            import psycopg2
            from app.ashare.schema import get_database_url
            dsn = get_database_url()
            if not dsn:
                raise ThemeError("theme_database_required")
            with closing(psycopg2.connect(dsn, connect_timeout=10,
                                         options="-c statement_timeout=30000 -c lock_timeout=5000")) as connection:
                result = (ingest_theme(connection, args.evidence_dir, args.board_code, args.name, args.timeout)
                          if args.command == "ingest" else ThemeStore(connection).snapshot(
                              args.board_code, args.known_at, args.limit, args.offset))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except ThemeError as exc:
        print(json.dumps({"status": "failed", "error": str(exc), "approvals_created": 0}))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "approvals_created": 0}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
