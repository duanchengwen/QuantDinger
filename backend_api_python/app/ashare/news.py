"""Bounded CNinfo announcement observations for research, never order execution.

The public index supplies titles and issuer identities. PDF bytes are archived,
not parsed: content is explicitly an announcement title, not the full document.
"""
from __future__ import annotations

import argparse
import base64
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

SOURCE = "cninfo"
INDEX_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
PDF_ORIGIN = "https://static.cninfo.com.cn/"
PARSER_VERSION = "cninfo-announcement-titles-v1"
TOPIC_KEYWORDS = {"ai": ("人工智能",), "robotics": ("机器人",), "new_energy": ("新能源",)}
PAGE_SIZE = 5
MAX_REQUESTS = 18
MAX_INDEX_BYTES = 262144
MAX_PDF_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_RECEIPTS = 20
PDF_PATH = re.compile(r"finalpage/[0-9]{4}-[0-9]{2}-[0-9]{2}/([0-9]{1,20})\.[pP][dD][fF]\Z")


class NewsError(ValueError):
    """Stable errors without credentials, provider bodies or filesystem paths."""


SAFE_NEWS_ERRORS = frozenset({
    "aware_news_time_required", "invalid_news_topics", "duplicate_news_json_key", "invalid_news_json",
    "invalid_news_text", "invalid_news_provider_total", "invalid_news_provider_page",
    "invalid_news_article_identity", "invalid_news_pdf_url", "invalid_news_issuer",
    "invalid_news_publication_time", "invalid_news_raw_evidence", "news_response_budget_exceeded",
    "invalid_news_request_scope", "news_request_budget_exceeded", "news_http_rejected",
    "unexpected_news_encoding", "news_pdf_response_invalid", "news_transport_failed",
    "invalid_news_timeout", "news_fetch_timeout", "news_fetch_failed", "invalid_news_capture",
    "invalid_news_page", "invalid_news_time_order", "invalid_news_index_scope",
    "news_article_changed_during_capture", "invalid_news_pdf_scope", "invalid_news_page_kind",
    "news_capture_incomplete", "news_evidence_unavailable", "news_manifest_hash_mismatch",
    "news_manifest_scope_mismatch", "invalid_news_page_count", "news_evidence_path_rejected",
    "news_page_hash_mismatch", "news_evidence_details_mismatch", "invalid_news_query",
    "news_database_items_mismatch", "news_capture_topic_mismatch", "news_operation_failed",
    "invalid_news_attachment_size",
})


def safe_error_code(error):
    """Permit only explicit machine codes, never arbitrary exception text."""
    return str(error) if isinstance(error, NewsError) and str(error) in SAFE_NEWS_ERRORS else "news_operation_failed"


def _utcnow():
    return datetime.now(timezone.utc)


def _instant(value):
    try:
        result = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(result, datetime) or result.utcoffset() is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise NewsError("aware_news_time_required") from None


def _topics(value):
    if (not isinstance(value, (tuple, list)) or not 1 <= len(value) <= 3
            or any(not isinstance(item, str) or item not in TOPIC_KEYWORDS for item in value)
            or len(set(value)) != len(value)):
        raise NewsError("invalid_news_topics")
    return sorted(value)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise NewsError("duplicate_news_json_key")
        result[key] = value
    return result


def _json(raw):
    try:
        return json.loads(raw, object_pairs_hook=_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(NewsError("invalid_news_json")))
    except (ValueError, UnicodeError, TypeError):
        raise NewsError("invalid_news_json") from None


def _params(topic):
    return {"pageNum": "1", "pageSize": str(PAGE_SIZE), "column": "szse", "tabName": "fulltext",
            "stock": "", "searchkey": TOPIC_KEYWORDS[topic][0], "secid": "", "category": "", "trade": "",
            "seDate": "", "sortName": "time", "sortType": "desc", "isHLtitle": "false"}


def _text(value, maximum):
    if (not isinstance(value, str) or not 1 <= len(value) <= maximum or value != value.strip()
            or any(ord(char) < 32 for char in value) or "<" in value or ">" in value):
        raise NewsError("invalid_news_text")
    return value


def _rows(raw):
    data = _json(raw)
    if not isinstance(data, dict) or type(data.get("totalAnnouncement")) is not int or data["totalAnnouncement"] < 0:
        raise NewsError("invalid_news_provider_total")
    rows = data.get("announcements")
    if rows is None and data["totalAnnouncement"] == 0:
        rows = []
    if (not isinstance(rows, list) or len(rows) != min(PAGE_SIZE, data["totalAnnouncement"])
            or not all(isinstance(row, dict) for row in rows)):
        raise NewsError("invalid_news_provider_page")
    ids = [row.get("announcementId") for row in rows]
    if any(not isinstance(i, str) or not re.fullmatch(r"[0-9]{1,20}", i) for i in ids) or len(set(ids)) != len(ids):
        raise NewsError("invalid_news_article_identity")
    return rows, data["totalAnnouncement"]


def _article(row, topics, received):
    article_id = row["announcementId"]
    title = _text(row.get("announcementTitle"), 1000)
    path = row.get("adjunctUrl")
    match = PDF_PATH.fullmatch(path) if isinstance(path, str) else None
    if not match or match[1] != article_id or (not isinstance(row.get("adjunctType"), str) or row["adjunctType"].upper() != "PDF"):
        raise NewsError("invalid_news_pdf_url")
    code = row.get("secCode")
    name = _text(row.get("secName"), 160)
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
        raise NewsError("invalid_news_issuer")
    # Market inference is restricted to explicit official issuer codes and columns.
    column = row.get("pageColumn")
    prefix = "sh." if column in ("SHZB", "SHKCB") else "sz." if column in ("SZZB", "SZCY") else None
    symbol = prefix + code if prefix else None
    securities = [{"symbol": symbol, "name": name, "identity_basis": "provider_explicit_security"}] if symbol and SYMBOL.fullmatch(symbol) else []
    stamp = row.get("announcementTime")
    published = None
    if stamp is not None:
        if type(stamp) is not int or not 946684800000 <= stamp <= 4102444800000:
            raise NewsError("invalid_news_publication_time")
        published = datetime.fromtimestamp(stamp / 1000, timezone.utc)
    return {"article_id": article_id, "title": title, "content": title,
            "content_kind": "announcement_title", "source": SOURCE, "source_url": PDF_ORIGIN + path,
            "published_at": published.isoformat() if published else None,
            "publication_time_precision": "provider_timestamp_unverified",
            "timestamp_status": "missing" if published is None else "future" if published > received else "valid",
            "topics": [topic for topic in topics if any(word in title for word in TOPIC_KEYWORDS[topic])],
            "relevance_basis": "explicit_title_keyword", "securities": securities,
            "stock_symbols": [item["symbol"] for item in securities],
            "issuer": {"code": code, "name": name, "provider_column": column},
            "coverage": "limited_recent_sample", "auto_approval_eligible": False}



def _pdf_exclusion(row, item):
    # Treat provider size metadata as KB at 1024 bytes for conservative exclusion. Actual bytes
    # remain bounded independently, so missing/underreported sizes cannot bypass limits.
    size = row.get("adjunctSize")
    if size is not None and (type(size) is not int or size < 0):
        raise NewsError("invalid_news_attachment_size")
    if size is not None and size * 1024 > MAX_PDF_BYTES:
        return {"article_id": item["article_id"], "title": item["title"], "topics": item["topics"],
                "source_url": item["source_url"], "declared_size_kb": size, "size_unit_bytes": 1024,
                "reason": "provider_declared_pdf_over_limit", "original_pdf_archived": False}
    return None


def _raw(page):
    try:
        raw = base64.b64decode(page["raw"], validate=True)
    except (ValueError, KeyError, TypeError):
        raise NewsError("invalid_news_raw_evidence") from None
    maximum = MAX_INDEX_BYTES if page.get("kind") == "index" else MAX_PDF_BYTES
    if not raw or len(raw) > maximum:
        raise NewsError("news_response_budget_exceeded")
    return raw


class _HTTPReader:
    def __init__(self, timeout):
        self.deadline = time.monotonic() + timeout
        self.requests = self.bytes = 0
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": "QuantDinger-News-Research/1.0", "Accept-Encoding": "identity",
                                     "Referer": "https://www.cninfo.com.cn/"})

    def get(self, kind, topic=None, pdf_url=None):
        if kind == "index" and topic in TOPIC_KEYWORDS and pdf_url is None:
            url, params = INDEX_URL, _params(topic)
        elif kind == "pdf" and topic is None and isinstance(pdf_url, str) and pdf_url.startswith(PDF_ORIGIN) and PDF_PATH.fullmatch(pdf_url[len(PDF_ORIGIN):]):
            url, params = pdf_url, None
        else:
            raise NewsError("invalid_news_request_scope")
        if self.requests >= MAX_REQUESTS or time.monotonic() >= self.deadline:
            raise NewsError("news_request_budget_exceeded")
        self.requests += 1
        remaining = self.deadline - time.monotonic()
        maximum = MAX_INDEX_BYTES if kind == "index" else MAX_PDF_BYTES
        try:
            method = self.session.post if kind == "index" else self.session.get
            kwargs = {"data": params} if kind == "index" else {}
            with method(url, **kwargs, timeout=(min(3, remaining), min(5, remaining)),
                        allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise NewsError("news_http_rejected")
                if response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
                    raise NewsError("unexpected_news_encoding")
                raw = bytearray()
                for chunk in response.iter_content(8192):
                    raw.extend(chunk)
                    self.bytes += len(chunk)
                    if len(raw) > maximum or self.bytes > MAX_TOTAL_BYTES or time.monotonic() > self.deadline:
                        raise NewsError("news_response_budget_exceeded")
                if kind == "pdf" and not raw.startswith(b"%PDF-"):
                    raise NewsError("news_pdf_response_invalid")
                return {"kind": kind, "topic": topic, "url": url, "params": params,
                        "received_at": _utcnow().isoformat(), "raw": base64.b64encode(raw).decode("ascii")}
        except requests.RequestException:
            raise NewsError("news_transport_failed") from None

    def close(self):
        self.session.close()


def _fetch_direct(topics, timeout):
    topics = _topics(topics)
    reader = _HTTPReader(timeout)
    capture = {"started_at": _utcnow().isoformat(), "topics": topics, "pages": []}
    urls = set()
    try:
        for topic in topics:
            page = reader.get("index", topic=topic)
            capture["pages"].append(page)
            for row in _rows(_raw(page))[0]:
                item = _article(row, topics, _instant(page["received_at"]))
                if item["topics"] and _pdf_exclusion(row, item) is None:
                    urls.add(item["source_url"])
        for url in sorted(urls):
            capture["pages"].append(reader.get("pdf", pdf_url=url))
        return capture
    finally:
        reader.close()


def fetch_news(topics=("ai", "robotics", "new_energy"), timeout=45):
    topics = _topics(topics)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 120:
        raise NewsError("invalid_news_timeout")
    try:
        result = subprocess.run([sys.executable, "-m", "app.ashare.news", "_fetch"],
                                input=json.dumps({"topics": topics, "timeout": timeout}), capture_output=True,
                                text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise NewsError("news_fetch_timeout") from None
    if len(result.stdout.encode()) > MAX_TOTAL_BYTES * 2:
        raise NewsError("news_response_budget_exceeded")
    payload = _json(result.stdout)
    if result.returncode or not isinstance(payload, dict):
        code = payload.get("error") if isinstance(payload, dict) else None
        raise NewsError(code if isinstance(code, str) and code in SAFE_NEWS_ERRORS else "news_fetch_failed")
    return payload


def _normalize(capture, received_at):
    if not isinstance(capture, dict):
        raise NewsError("invalid_news_capture")
    topics = _topics(capture.get("topics"))
    received = _instant(received_at)
    previous = _instant(capture.get("started_at"))
    pages = capture.get("pages")
    if not isinstance(pages, list) or not len(topics) <= len(pages) <= MAX_REQUESTS or previous > received:
        raise NewsError("invalid_news_capture")
    indexes, pdfs, items, totals, total_bytes = [], {}, {}, {}, 0
    excluded = {}
    for page in pages:
        if not isinstance(page, dict):
            raise NewsError("invalid_news_page")
        page_time = _instant(page.get("received_at"))
        if not previous <= page_time <= received:
            raise NewsError("invalid_news_time_order")
        previous = page_time
        raw = _raw(page)
        total_bytes += len(raw)
        if total_bytes > MAX_TOTAL_BYTES:
            raise NewsError("news_response_budget_exceeded")
        kind, topic = page.get("kind"), page.get("topic")
        if kind == "index":
            if pdfs or topic not in topics or topic in indexes or page.get("url") != INDEX_URL or page.get("params") != _params(topic):
                raise NewsError("invalid_news_index_scope")
            indexes.append(topic)
            rows, totals[topic] = _rows(raw)
            for row in rows:
                item = _article(row, topics, received)
                if not item["topics"]:
                    continue
                exclusion = _pdf_exclusion(row, item)
                old = items.get(item["article_id"])
                prior_exclusion = excluded.get(item["article_id"])
                if exclusion is not None:
                    if old or (prior_exclusion and prior_exclusion != exclusion):
                        raise NewsError("news_article_changed_during_capture")
                    excluded[item["article_id"]] = exclusion
                    continue
                if prior_exclusion or (old and {k: v for k, v in old.items() if k != "source_hash"} != item):
                    raise NewsError("news_article_changed_during_capture")
                if not old:
                    items[item["article_id"]] = {**item, "source_hash": _sha(raw)}
        elif kind == "pdf":
            url = page.get("url")
            if (topic is not None or page.get("params") is not None or not isinstance(url, str)
                    or not url.startswith(PDF_ORIGIN) or not PDF_PATH.fullmatch(url[len(PDF_ORIGIN):])
                    or url in pdfs or not raw.startswith(b"%PDF-")):
                raise NewsError("invalid_news_pdf_scope")
            pdfs[url] = _sha(raw)
        else:
            raise NewsError("invalid_news_page_kind")
    if indexes != topics or set(pdfs) != {item["source_url"] for item in items.values()}:
        raise NewsError("news_capture_incomplete")
    normalized = []
    for article_id in sorted(items):
        item = items[article_id]
        item["pdf_hash"] = pdfs[item["source_url"]]
        item["content_hash"] = _sha(_canonical({key: value for key, value in item.items() if key not in ("source_hash", "timestamp_status", "topics")}))
        normalized.append(item)
    return {"items": normalized, "topics": topics, "provider_totals": totals,
            "excluded_items": [excluded[key] for key in sorted(excluded)],
            "coverage": "limited_recent_sample", "page_size": PAGE_SIZE, "page_count_per_topic": 1,
            "relevance_basis": "explicit_title_keyword", "content_kind": "announcement_title"}


def _archive(capture, evidence_dir):
    received = _utcnow()
    normalized = _normalize(capture, received)
    receipt_id = uuid.uuid4().hex
    manifest = {"schema_version": 1, "parser_version": PARSER_VERSION, "receipt_id": receipt_id,
                "source": SOURCE, "source_url": INDEX_URL, "received_at": received.isoformat(),
                "capture": {key: value for key, value in capture.items() if key != "pages"},
                "normalized": normalized, "pages": []}
    root = Path(evidence_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / ("news-" + receipt_id)
    with tempfile.TemporaryDirectory(dir=root) as temporary:
        staged = Path(temporary) / "receipt"
        staged.mkdir()
        for index, page in enumerate(capture["pages"]):
            raw = _raw(page)
            filename = f"page-{index:03d}.bin"
            (staged / filename).write_bytes(raw)
            manifest["pages"].append({**{key: value for key, value in page.items() if key != "raw"},
                                      "file": filename, "sha256": _sha(raw)})
        manifest_bytes = _canonical(manifest)
        (staged / "manifest.json").write_bytes(manifest_bytes)
        staged.rename(destination)
    return manifest, str(destination / "manifest.json"), _sha(manifest_bytes)


def _file(path, maximum):
    if not path.is_absolute() or path.resolve() != path or not path.is_file() or path.stat().st_size > maximum:
        raise NewsError("news_evidence_unavailable")
    return path.read_bytes()


def _verified(row):
    try:
        path = Path(row["evidence_path"])
        raw = _file(path, MAX_TOTAL_BYTES)
        if _sha(raw) != row["source_hash"]:
            raise NewsError("news_manifest_hash_mismatch")
        manifest = _json(raw)
        if (manifest["schema_version"] != 1 or manifest["parser_version"] != PARSER_VERSION
                or manifest["receipt_id"] != row["id"] or manifest["source"] != SOURCE or row["source"] != SOURCE
                or manifest["source_url"] != INDEX_URL or row["source_url"] != INDEX_URL
                or _instant(manifest["received_at"]) != row["received_at"]):
            raise NewsError("news_manifest_scope_mismatch")
        if not isinstance(manifest["pages"], list) or len(manifest["pages"]) > MAX_REQUESTS:
            raise NewsError("invalid_news_page_count")
        capture = {**manifest["capture"], "pages": []}
        for index, page in enumerate(manifest["pages"]):
            if page["file"] != f"page-{index:03d}.bin":
                raise NewsError("news_evidence_path_rejected")
            raw_page = _file(path.parent / page["file"], MAX_INDEX_BYTES if page["kind"] == "index" else MAX_PDF_BYTES)
            if _sha(raw_page) != page["sha256"]:
                raise NewsError("news_page_hash_mismatch")
            capture["pages"].append({**{key: value for key, value in page.items() if key not in ("file", "sha256")},
                                      "raw": base64.b64encode(raw_page).decode("ascii")})
        normalized = _normalize(capture, row["received_at"])
        if (normalized != manifest["normalized"] or row["item_count"] != len(normalized["items"])
                or row["topics"] != normalized["topics"] or row["metadata"] != {key: value for key, value in normalized.items() if key != "items"}):
            raise NewsError("news_evidence_details_mismatch")
        return normalized
    except (KeyError, TypeError, ValueError, OSError) as exc:
        if isinstance(exc, NewsError):
            raise
        raise NewsError("news_evidence_unavailable") from None


def _db_item(receipt_id, item):
    return {"id": receipt_id + ":" + item["article_id"], "receipt_id": receipt_id,
            "article_id": item["article_id"], "published_at": _instant(item["published_at"]) if item["published_at"] else None,
            "source_url": item["source_url"], "source_hash": item["source_hash"], "content_hash": item["content_hash"], "details": item}


class NewsStore:
    def __init__(self, connection):
        self.connection = connection

    def list_items(self, topics, known_at, limit=100):
        """Read and verify complete receipts before filtering or limiting items."""
        topics, known_at = _topics(topics), _instant(known_at)
        if known_at > _utcnow() or type(limit) is not int or not 1 <= limit <= 100:
            raise NewsError("invalid_news_query")
        items, receipts = [], []
        with self.connection:
            with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""SELECT * FROM qd_ashare_news_receipts WHERE received_at<=%s
                    ORDER BY received_at DESC,id DESC LIMIT %s""", (known_at, MAX_RECEIPTS + 1))
                rows = cursor.fetchall()
                bounded = len(rows) > MAX_RECEIPTS
                for row in rows[:MAX_RECEIPTS]:
                    normalized = _verified(row)
                    cursor.execute("SELECT * FROM qd_ashare_news_items WHERE receipt_id=%s ORDER BY article_id", (row["id"],))
                    expected = [_db_item(row["id"], item) for item in normalized["items"]]
                    if [dict(item) for item in cursor.fetchall()] != expected:
                        raise NewsError("news_database_items_mismatch")
                    receipts.append({"receipt_id": row["id"], "received_at": row["received_at"].isoformat(),
                                     "source_hash": row["source_hash"], **{k: v for k, v in normalized.items() if k != "items"}})
                    for db in expected:
                        item = db["details"]
                        items.append({**item, "id": db["id"], "receipt_id": row["id"],
                                      "received_at": row["received_at"].isoformat(), "known_at": row["received_at"].isoformat()})
        # Retain every revision in storage, but research sees only the latest known version.
        # Select versions before topic filtering so an old topic tag cannot resurrect a revision.
        unique = {}
        for item in items:
            unique.setdefault(item["article_id"], item)
        items = [item for item in unique.values() if set(topics).intersection(item["topics"])]
        # Valid publication times come first, newest first; future then missing
        # timestamps cannot displace valid evidence merely because they sort high.
        def publication_order(item):
            rank = {"valid": 0, "future": 1, "missing": 2}[item["timestamp_status"]]
            published = _instant(item["published_at"]).timestamp() if item["published_at"] else 0
            return rank, -published, -_instant(item["known_at"]).timestamp(), item["id"]
        items.sort(key=publication_order)
        return {"items": items[:limit], "status": "observed" if receipts else "not_observed", "receipts": receipts,
                "coverage": "limited_recent_sample", "content_kind": "announcement_title", "limit": limit,
                "truncated": bounded or len(items) > limit, "known_at": known_at.isoformat(), "auto_approval_eligible": False}


def ingest_news(connection, evidence_dir, topics=("ai", "robotics", "new_energy"), timeout=45):
    """Archive current provider evidence; no caller-controlled receipt time."""
    capture = fetch_news(topics, timeout)
    if capture.get("topics") != _topics(topics):
        raise NewsError("news_capture_topic_mismatch")
    manifest, path, source_hash = _archive(capture, evidence_dir)
    data, receipt_id = manifest["normalized"], manifest["receipt_id"]
    metadata = {key: value for key, value in data.items() if key != "items"}
    with connection, connection.cursor() as cursor:
        cursor.execute("""INSERT INTO qd_ashare_news_receipts
            (id,received_at,source,source_url,source_hash,evidence_path,topics,item_count,metadata)
            VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb)""",
            (receipt_id, manifest["received_at"], SOURCE, INDEX_URL, source_hash, path,
             json.dumps(data["topics"]), len(data["items"]), json.dumps(metadata)))
        for item in data["items"]:
            db = _db_item(receipt_id, item)
            cursor.execute("""INSERT INTO qd_ashare_news_items
                (id,receipt_id,article_id,published_at,source_url,source_hash,content_hash,details)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                (db["id"], receipt_id, db["article_id"], db["published_at"], db["source_url"], db["source_hash"],
                 db["content_hash"], json.dumps(item)))
    return {"receipt_id": receipt_id, "received_at": manifest["received_at"], "source": SOURCE,
            "source_hash": source_hash, "evidence_path": path, "item_count": len(data["items"]), **metadata,
            "auto_approval_eligible": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("_fetch", help=argparse.SUPPRESS)
    ingest = sub.add_parser("ingest")
    ingest.add_argument("--evidence-dir", type=Path, required=True)
    ingest.add_argument("--topics", nargs="+", default=list(TOPIC_KEYWORDS))
    ingest.add_argument("--timeout", type=float, default=45)
    listing = sub.add_parser("list")
    listing.add_argument("--topics", nargs="+", default=list(TOPIC_KEYWORDS))
    listing.add_argument("--known-at")
    listing.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    try:
        if args.command == "_fetch":
            payload = _json(sys.stdin.read(4096))
            result = _fetch_direct(payload["topics"], payload["timeout"])
        else:
            import psycopg2
            from app.ashare.schema import get_database_url
            connection = psycopg2.connect(get_database_url(), connect_timeout=5)
            try:
                if args.command == "ingest":
                    result = ingest_news(connection, args.evidence_dir, args.topics, args.timeout)
                else:
                    connection.set_session(readonly=True, isolation_level="REPEATABLE READ")
                    result = NewsStore(connection).list_items(args.topics, args.known_at or _utcnow(), args.limit)
            finally:
                connection.close()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"error": safe_error_code(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
