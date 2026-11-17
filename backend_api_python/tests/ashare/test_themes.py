"""Offline provider adversaries and PostgreSQL in an exclusive temporary DB."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest
import requests

from app.ashare import themes as t

START = datetime(2026, 11, 17, 13, tzinfo=timezone.utc)
RECEIVED = START + timedelta(minutes=1)
BOARD, NAME = "BK1100", "人工智能"


def capture():
    catalog = [{"f12": f"BK{1000 + i}", "f14": f"主题{i}"} for i in range(100)] + [{"f12": BOARD, "f14": NAME}]
    members = [{"f12": str(600000 + i), "f13": 1, "f14": f"公司{i}"} for i in range(103)]
    members += [{"f12": "300001", "f13": 0, "f14": "创业板样本"}, {"f12": "832149", "f13": 0, "f14": "北交所样本"}]
    pages = []
    for kind, rows in [("catalog", catalog), ("members", members)]:
        for index, chunk in enumerate([rows[:100], rows[100:], rows[:100]]):
            number = index + 1 if index < 2 else 1
            raw = json.dumps({"rc": 0, "data": {"total": len(rows), "diff": chunk}}, ensure_ascii=False).encode()
            pages.append({"kind": kind, "page": number, "recheck": index == 2,
                          "url": t.CATALOG_URL if kind == "catalog" else t.MEMBERS_URL,
                          "params": t._params(kind, number, None if kind == "catalog" else BOARD),
                          "received_at": (START + timedelta(seconds=len(pages) + 1)).isoformat(),
                          "raw": base64.b64encode(raw).decode()})
    return {"started_at": START.isoformat(), "catalog_only": False, "board_code": BOARD,
            "board_name": NAME, "expected_name": NAME, "pages": pages}


def edit(value, index, change):
    page = value["pages"][index]
    body = json.loads(base64.b64decode(page["raw"]))
    change(body)
    page["raw"] = base64.b64encode(json.dumps(body, ensure_ascii=False).encode()).decode()


def test_complete_catalog_and_supported_member_scope():
    result = t._normalize_capture(capture(), RECEIVED)
    assert (result["provider_total"], result["member_count"], result["excluded_count"]) == (105, 104, 1)
    assert result["board_code"] == BOARD
    assert {row["symbol"] for row in result["members"]} == {f"sh.{600000+i}" for i in range(103)} | {"sz.300001"}
    assert result["excluded_members"][0]["provider_code"] == "832149"
    assert "outside_supported" in result["excluded_members"][0]["reason"]
    value = capture();value["catalog_only"] = True;value["pages"] = value["pages"][:3]
    catalog = t._normalize_capture(value, RECEIVED)["catalog"]
    assert len(catalog) == 101 and catalog[-1] == {"board_code": BOARD, "board_name": NAME}


@pytest.mark.parametrize("mutation", ["total", "missing", "short", "duplicate", "changed", "boundary", "url", "params", "old_time", "future_time", "bool_page", "name", "extra_kind"])
def test_inconsistent_pages_are_rejected(mutation):
    value = capture()
    if mutation == "total": edit(value, 4, lambda b: b["data"].update(total=106))
    if mutation == "missing": value["pages"].pop(4)
    if mutation == "short": edit(value, 4, lambda b: b["data"]["diff"].pop())
    if mutation == "duplicate": edit(value, 4, lambda b: b["data"]["diff"][0].update(f12="600000"))
    if mutation == "changed": edit(value, 5, lambda b: b["data"]["diff"][0].update(f14="变更"))
    if mutation == "boundary": value["pages"].pop()
    if mutation == "url": value["pages"][3]["url"] = "https://other.invalid"
    if mutation == "params": value["pages"][3]["params"]["fid"] = "f3"
    if mutation == "old_time": value["pages"][4]["received_at"] = START.isoformat()
    if mutation == "future_time": value["pages"][5]["received_at"] = (RECEIVED + timedelta(seconds=1)).isoformat()
    if mutation == "bool_page": value["pages"][3]["page"] = True
    if mutation == "name": value["expected_name"] = "新能源"
    if mutation == "extra_kind": value["pages"].append({"kind": "other"})
    with pytest.raises(t.ThemeError): t._normalize_capture(value, RECEIVED)


@pytest.mark.parametrize("body", [b'{}', b'{"rc":1}', b'{"rc":0,"data":{"total":true,"diff":[]}}', b'{"rc":0,"data":{"total":0,"diff":[]}}', b'{"rc":0,"rc":0}', b'not json'])
def test_invalid_or_duplicate_json_rejected(body):
    with pytest.raises(t.ThemeError): t._decode(body)


@pytest.mark.parametrize("code,name", [(None,None), ("https://other.invalid",NAME), ("BK1000/../",NAME), ("BK1",NAME), (BOARD,""), (BOARD,"人工智能\n"), (True,NAME)])
def test_no_arbitrary_url_or_guessed_theme(code, name, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("network must not start"))
    with pytest.raises(t.ThemeError): t.fetch_theme(code, name)


@pytest.mark.parametrize("timeout", [0,121,True,float("nan"),"5"])
def test_timeout_validation(timeout):
    with pytest.raises(t.ThemeError): t.fetch_theme(BOARD, NAME, timeout)


def test_child_total_timeout_and_no_public_backdate(monkeypatch):
    calls = []
    def blocked(args, **kwargs):
        calls.append((args, kwargs))
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])
    monkeypatch.setattr(subprocess, "run", blocked)
    with pytest.raises(t.ThemeError, match="theme_fetch_timeout"): t.fetch_theme(BOARD, NAME, 2)
    assert calls[0][1]["timeout"] == 2 and calls[0][0][-1] == "_fetch"
    assert list(inspect.signature(t.ingest_theme).parameters) == ["connection", "evidence_dir", "board_code", "expected_name", "timeout"]


def test_exact_name_binding_and_ambiguous_names():
    rows = [{"f12": BOARD, "f14": "新能源车"}, {"f12": "BK1111", "f14": "新能源车"}]
    with pytest.raises(t.ThemeError): t._select(rows, None, "新能源")
    with pytest.raises(t.ThemeError): t._select(rows, None, "新能源车")
    with pytest.raises(t.ThemeError): t._select(rows, BOARD, NAME)
    assert t._select(rows, BOARD, "新能源车") == (BOARD,"新能源车")


class Response:
    status_code = 200
    headers = {}
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *a): return None
    def iter_content(self, size): yield self.body


def test_http_fixed_endpoint_no_implicit_credentials_redirects_or_retry(monkeypatch):
    reader = t._HTTPReader(5)
    calls = []
    def get(url, **kwargs):
        calls.append((url,kwargs))
        return Response(b'{"rc":0,"data":{"total":1,"diff":[{"f12":"BK1100","f14":"AI"}]}}')
    monkeypatch.setattr(reader.session, "get", get)
    reader.get("catalog",1)
    assert reader.session.trust_env is False
    assert reader.session.get_adapter(t.CATALOG_URL).max_retries.total == 0
    assert calls[0][0] == t.CATALOG_URL and calls[0][1]["allow_redirects"] is False
    assert calls[0][1]["params"]["fid"] == "f12" and calls[0][1]["params"]["pz"] == "100"
    assert "verify" not in calls[0][1]
    reader.close()


@pytest.mark.parametrize("mode", ["redirect","encoding","bytes","requests","transport"])
def test_http_limits_fail_closed(mode, monkeypatch):
    reader = t._HTTPReader(5)
    response = Response(b'x'*100)
    if mode == "redirect": response.status_code = 302
    if mode == "encoding": response.headers = {"Content-Encoding":"gzip"}
    if mode == "bytes": monkeypatch.setattr(t,"MAX_TOTAL_BYTES",50)
    if mode == "requests": reader.requests = t.MAX_REQUESTS
    def send(*a, **k):
        if mode == "transport": raise requests.Timeout("must not be exposed")
        return response
    monkeypatch.setattr(reader.session,"get",send)
    with pytest.raises(t.ThemeError): reader.get("catalog",1)
    reader.close()


def test_collect_pages_actual_hundred_and_boundary_check():
    value = capture()
    class Reader:
        calls = []
        def get(self, kind, number, board_code=None, recheck=False):
            self.calls.append((kind,number,recheck))
            return deepcopy(next(p for p in value["pages"] if p["kind"] == kind and p["page"] == number and p["recheck"] == recheck))
    reader = Reader()
    pages, rows = t._collect(reader,"catalog")
    assert len(rows) == 101 and len(pages) == 3
    assert reader.calls == [("catalog",1,False),("catalog",2,False),("catalog",1,True)]


@pytest.fixture(scope="session")
def dedicated_database():
    """Only create/drop our unique DB; never migrate the supplied admin DB."""
    dsn = os.getenv("TEST_ASHARE_DATABASE_URL")
    if not dsn: pytest.skip("TEST_ASHARE_DATABASE_URL required")
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import make_dsn
    admin = psycopg2.connect(dsn,connect_timeout=5);admin.autocommit=True
    name = "themes_test_"+uuid.uuid4().hex
    with admin.cursor() as c: c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try: yield make_dsn(dsn,dbname=name)
    finally:
        with admin.cursor() as c:
            c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()",(name,))
            c.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        admin.close()


@pytest.fixture
def store(dedicated_database,tmp_path,monkeypatch):
    import psycopg2
    from psycopg2 import sql
    connection = psycopg2.connect(dedicated_database,connect_timeout=5)
    schema = "case_"+uuid.uuid4().hex
    with connection,connection.cursor() as c:
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        c.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        c.execute((Path(__file__).parents[2]/"migrations/20261117_ashare_themes.sql").read_text())
    monkeypatch.setattr(t,"_utcnow",lambda:RECEIVED)
    monkeypatch.setattr(t,"fetch_theme",lambda *a,**k:capture())
    yield t.ThemeStore(connection),tmp_path
    connection.close()


def ingest(store):
    repository,directory=store
    return t.ingest_theme(repository.connection,directory,BOARD,NAME)


def test_pg_raw_archive_known_at_and_full_scope(store):
    repository,_=store
    receipt=ingest(store)
    assert repository.snapshot(BOARD,RECEIVED-timedelta(microseconds=1))["status"] == "not_observed"
    result=repository.snapshot(BOARD,RECEIVED,limit=1,offset=103)
    assert result["members"][0]["symbol"] == "sz.300001"
    assert (result["member_count"],result["provider_total"],result["excluded_count"]) == (104,105,1)
    assert result["published_at"] is None and result["known_at"] == RECEIVED.isoformat()
    assert result["auto_approval_eligible"] is False and result["historical_membership_certified"] is False
    path=Path(receipt["evidence_path"])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == receipt["source_hash"]
    assert len(list(path.parent.glob("page-*.bin"))) == 6
    assert json.loads(path.read_bytes())["capture"]["expected_name"] == NAME


def test_pg_append_only_versions_keep_original_knowledge(store,monkeypatch):
    repository,_=store
    first=ingest(store)
    value=capture()
    for index in (3,5): edit(value,index,lambda b:b["data"]["diff"][0].update(f14="新公司名"))
    monkeypatch.setattr(t,"fetch_theme",lambda *a,**k:value)
    monkeypatch.setattr(t,"_utcnow",lambda:RECEIVED+timedelta(minutes=1))
    second=ingest(store)
    assert first["receipt_id"] != second["receipt_id"]
    assert repository.snapshot(BOARD,RECEIVED)["members"][0]["name"] == "公司0"
    assert repository.snapshot(BOARD,RECEIVED+timedelta(minutes=1))["members"][0]["name"] == "新公司名"
    assert hashlib.sha256(Path(first["evidence_path"]).read_bytes()).hexdigest() == first["source_hash"]


@pytest.mark.parametrize("mode", ["manifest","raw","missing","symlink","time","name","delete","edit","identity","extra"])
def test_pg_evidence_and_all_db_members_checked_before_pagination(store,mode):
    repository,_=store
    receipt=ingest(store);manifest=Path(receipt["evidence_path"]);page=manifest.parent/"page-004.bin"
    if mode == "manifest": manifest.write_text("{}")
    if mode == "raw": page.write_bytes(b'{}')
    if mode == "missing": page.unlink()
    if mode == "symlink": page.unlink();page.symlink_to(manifest)
    with repository.connection,repository.connection.cursor() as c:
        if mode == "time": c.execute("UPDATE qd_ashare_theme_receipts SET received_at=received_at+interval '1 minute'")
        if mode == "name": c.execute("UPDATE qd_ashare_theme_receipts SET board_name='机器人'")
        if mode == "delete": c.execute("DELETE FROM qd_ashare_theme_members WHERE symbol='sz.300001'")
        if mode == "edit": c.execute("UPDATE qd_ashare_theme_members SET details=details || '{\"name\":\"changed\"}'::jsonb WHERE symbol='sz.300001'")
        if mode == "identity": c.execute("UPDATE qd_ashare_theme_members SET symbol='sz.000001' WHERE symbol='sz.300001'")
        if mode == "extra": c.execute("INSERT INTO qd_ashare_theme_members SELECT receipt_id,'sz.000001',details FROM qd_ashare_theme_members LIMIT 1")
    with pytest.raises(t.ThemeError): repository.snapshot(BOARD,RECEIVED+timedelta(hours=1),limit=1)


def test_pg_invalid_capture_cannot_publish_receipt(store,monkeypatch):
    repository,directory=store
    value=capture();value["pages"].pop(4)
    monkeypatch.setattr(t,"fetch_theme",lambda *a,**k:value)
    with pytest.raises(t.ThemeError): ingest(store)
    assert not list(directory.iterdir())
    with repository.connection,repository.connection.cursor() as c:
        c.execute("SELECT count(*) FROM qd_ashare_theme_receipts")
        assert c.fetchone()[0] == 0


@pytest.mark.parametrize("limit,offset,known_at", [(501,0,None),(True,0,None),(1,-1,None),(1,0,datetime(2026,11,17))])
def test_pg_query_pagination_and_timezone_limits(store,limit,offset,known_at):
    with pytest.raises(t.ThemeError): store[0].snapshot(BOARD,known_at,limit=limit,offset=offset)


@pytest.fixture
def theme_client(store, dedicated_database, monkeypatch):
    """HTTP connections point only at the fixture's exclusive database/schema."""
    import psycopg2
    from flask import Flask
    from app.ashare import web_api
    from app.ashare.schema import MIGRATIONS
    repository, _ = store
    connection = repository.connection
    with connection, connection.cursor() as cursor:
        cursor.execute("SELECT current_schema()")
        schema = cursor.fetchone()[0]
        for migration in MIGRATIONS:
            cursor.execute((Path(__file__).parents[2] / "migrations" / migration).read_text())
    original_connect = psycopg2.connect
    def isolated_connect(*args, **kwargs):
        kwargs["options"] = kwargs.get("options", "") + " -c search_path=" + schema
        return original_connect(dedicated_database, **kwargs)
    monkeypatch.setattr(web_api.psycopg2, "connect", isolated_connect)
    monkeypatch.setattr(web_api, "get_database_url", lambda: "fixture-connection-only")
    def verify(token):
        if token not in {"user", "viewer", "other"}:
            return None
        return {"user_id": 1 if token != "other" else 999,
                "_verified_user_role": "viewer" if token == "viewer" else "user", "_verified_username": token}
    monkeypatch.setattr("app.utils.auth.verify_token", verify)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return RECEIVED + timedelta(hours=1)
    monkeypatch.setattr(web_api, "datetime", Clock)
    app = Flask(__name__)
    app.register_blueprint(web_api.blueprint)
    app.config["TESTING"] = True
    return app.test_client()


def theme_get(client, path="/themes", token="user"):
    headers = {} if token is None else {"Authorization": "Bearer " + token}
    return client.get("/api/ashare-paper" + path, headers=headers)


@pytest.mark.parametrize("path", ["/themes", "/themes/BK1100"])
@pytest.mark.parametrize("token", [None, "invalid", "qd_agent_not_a_human_jwt"])
def test_theme_http_requires_human_auth(theme_client, path, token):
    assert theme_get(theme_client, path, token).status_code == 401


def test_theme_http_unknown_observation_is_explicit_without_account_creation(theme_client, store):
    for path in ["/themes", "/themes/BK1100"]:
        response = theme_get(theme_client, path + "?_t=1794916800000", token="viewer")
        assert response.status_code == 200
        assert response.json["data"]["status"] == "not_observed"
        assert "evidence_path" not in response.text
    with store[0].connection, store[0].connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM qd_ashare_accounts")
        assert cursor.fetchone()[0] == 0


@pytest.mark.parametrize("path", ["/themes?limit=1", "/themes?known_at=2099-01-01T00:00:00Z", "/themes?unexpected=1", "/themes?_t=1&_t=2", "/themes/BK1100?known_at=2099-01-01", "/themes/BK1100?trading_day=2026-11-17", "/themes/BK1100?limit=1&limit=2", "/themes/BK1100?offset=1&offset=2", "/themes/BK1100?limit=0", "/themes/BK1100?limit=501", "/themes/BK1100?offset=-1", "/themes/BK1100?offset=10001", "/themes/BK1100?limit=1.0", "/themes/BK1100?limit=+1", "/themes/BK1100?limit=100000000000000000000", "/themes/BK1", "/themes/BK1100?limit=%D9%A1"])
def test_theme_http_rejects_unknown_duplicate_future_and_invalid_parameters(theme_client, path):
    response = theme_get(theme_client, path)
    assert response.status_code == 400 and response.json["data"] is None


def test_theme_http_viewer_verified_members_and_path_redaction(theme_client, store, monkeypatch):
    receipt = ingest(store)
    monkeypatch.setattr(t, "fetch_theme", lambda *a, **k: pytest.fail("GET must never fetch"))
    listing = theme_get(theme_client, "/themes?_t=1", token="viewer")
    assert listing.status_code == 200
    summary = listing.json["data"]["themes"][0]
    assert summary == {"board_code": BOARD, "board_name": NAME, "received_at": RECEIVED.isoformat(),
                       "source": "eastmoney", "source_hash": receipt["source_hash"], "member_count": 104,
                       "provider_total": 105, "excluded_count": 1, "membership_semantics": "current_observation",
                       "historical_membership_certified": False}
    response = theme_get(theme_client, "/themes/BK1100?limit=1&offset=103&_t=2", token="other")
    assert response.status_code == 200
    assert response.json["data"]["members"][0]["symbol"] == "sz.300001"
    assert response.json["data"]["auto_approval_eligible"] is False
    assert "evidence_path" not in response.text and "manifest.json" not in response.text


def test_theme_http_corruption_does_not_publish_partial_or_unverified_members(theme_client, store):
    receipt = ingest(store)
    (Path(receipt["evidence_path"]).parent / "page-004.bin").write_bytes(b'corrupt source')
    for path in ["/themes", "/themes/BK1100?limit=1"]:
        response = theme_get(theme_client, path)
        assert response.status_code == 503
        assert response.json == {"code": 0, "msg": "theme_evidence_unavailable", "data": None}
        assert receipt["evidence_path"] not in response.text


def test_theme_http_readonly_session_blocks_writes_and_preserves_trading_tables(theme_client, store, monkeypatch):
    from app.ashare.web_store import WebStore
    repository, _ = store
    WebStore(repository.connection, "unchanged-fixture").initialize("10000")
    ingest(store)
    def fingerprint():
        result = []
        with repository.connection, repository.connection.cursor() as cursor:
            for table in ["accounts", "orders", "fills", "lots", "audit", "web"]:
                cursor.execute("SELECT row_to_json(r)::text FROM qd_ashare_" + table + " r ORDER BY row_to_json(r)::text")
                result.append(cursor.fetchall())
        return result
    before = fingerprint()
    assert theme_get(theme_client, "/themes").status_code == 200
    assert theme_get(theme_client, "/themes/BK1100").status_code == 200
    original = t.ThemeStore.latest_snapshots
    readonly = []
    def prohibited(self, **kwargs):
        with self.connection.cursor() as cursor:
            cursor.execute("SHOW transaction_read_only")
            readonly.append(cursor.fetchone()[0])
            cursor.execute("SHOW transaction_isolation")
            readonly.append(cursor.fetchone()[0])
            cursor.execute("DELETE FROM qd_ashare_accounts")
        return original(self, **kwargs)
    monkeypatch.setattr(t.ThemeStore, "latest_snapshots", prohibited)
    response = theme_get(theme_client, "/themes")
    assert response.status_code == 503 and response.json["msg"] == "paper_database_unavailable"
    assert readonly == ["on", "repeatable read"]
    assert fingerprint() == before


@pytest.mark.parametrize("error", [t.ThemeError("private-path-and-secret-must-not-escape"), OSError("/private/token-file")])
def test_theme_http_error_details_are_redacted(theme_client, monkeypatch, error):
    def broken(*a, **k): raise error
    monkeypatch.setattr(t.ThemeStore, "latest_snapshots", broken)
    response = theme_get(theme_client, "/themes")
    assert response.status_code == 503 and response.json["data"] is None
    assert response.json["msg"] == "theme_evidence_unavailable"


def test_pg_latest_distinct_boards_cap_and_all_or_nothing_validation(store, monkeypatch):
    repository, directory = store
    receipts = {}
    for index in range(21):
        code = f"BK{1080 + index}"
        name = NAME if code == BOARD else f"主题{80 + index}"
        value = capture()
        value.update(board_code=code, board_name=name, expected_name=name)
        for page in value["pages"]:
            if page["kind"] == "members":
                page["params"] = t._params("members", page["page"], code)
        monkeypatch.setattr(t, "fetch_theme", lambda *a, **k: value)
        monkeypatch.setattr(t, "_utcnow", lambda: RECEIVED + timedelta(minutes=index))
        receipts[code] = t.ingest_theme(repository.connection, directory, code, name)
    latest = repository.latest_snapshots(known_at=RECEIVED + timedelta(hours=1))
    codes = [item["board_code"] for item in latest["themes"]]
    assert len(codes) == len(set(codes)) == 20 and "BK1080" not in codes
    # A later observation for one board must replace its summary, not add a row.
    monkeypatch.setattr(t, "_utcnow", lambda: RECEIVED + timedelta(minutes=30))
    newer = t.ingest_theme(repository.connection, directory, BOARD, NAME)
    latest = repository.latest_snapshots(known_at=RECEIVED + timedelta(hours=1))
    assert len(latest["themes"]) == 20
    assert latest["themes"][0]["source_hash"] == newer["source_hash"]
    page = Path(receipts["BK1082"]["evidence_path"]).parent / "page-004.bin"
    page.write_bytes(b"corrupt after valid first summaries")
    with pytest.raises(t.ThemeError): repository.latest_snapshots(known_at=RECEIVED + timedelta(hours=1))
