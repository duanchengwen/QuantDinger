"""Synthetic evidence; optional PostgreSQL tests create their own database."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import uuid
import pytest
import requests
from app.ashare import news as n

RECEIVED = datetime(2026, 11, 18, 3, tzinfo=timezone.utc)


def article(number="1225670001", title="关于人工智能机器人新能源项目的公告"):
    return {"announcementId": number, "announcementTitle": title,
            "announcementTime": int((RECEIVED-timedelta(hours=1)).timestamp()*1000),
            "adjunctUrl": f"finalpage/2026-11-18/{number}.PDF", "adjunctType": "PDF",
            "secCode": "600001", "secName": "测试公司", "pageColumn": "SHZB"}


def capture(topics=("ai",), rows=None):
    topics = sorted(topics)
    rows = rows if rows is not None else [article()]
    pages = []
    for topic in topics:
        raw = n._canonical({"totalAnnouncement": len(rows), "announcements": rows})
        pages.append({"kind": "index", "topic": topic, "url": n.INDEX_URL, "params": n._params(topic),
                      "received_at": (RECEIVED-timedelta(seconds=2)).isoformat(), "raw": base64.b64encode(raw).decode()})
    for row in sorted(rows, key=lambda r: r["announcementId"]):
        if any(word in row["announcementTitle"] for t in topics for word in n.TOPIC_KEYWORDS[t]):
            pages.append({"kind": "pdf", "topic": None, "url": n.PDF_ORIGIN+row["adjunctUrl"], "params": None,
                          "received_at": (RECEIVED-timedelta(seconds=1)).isoformat(),
                          "raw": base64.b64encode(b"%PDF-1.7\nsynthetic test only").decode()})
    return {"started_at": (RECEIVED-timedelta(seconds=3)).isoformat(), "topics": topics, "pages": pages}


def edit(value, mutate):
    page = value["pages"][0]
    raw = json.loads(base64.b64decode(page["raw"]))
    mutate(raw)
    page["raw"] = base64.b64encode(n._canonical(raw)).decode()


def test_title_only_explicit_issuer_and_keyword_semantics():
    item = n._normalize(capture(("ai", "robotics", "new_energy")), RECEIVED)["items"][0]
    assert item["content_kind"] == "announcement_title" and item["content"] == item["title"]
    assert item["topics"] == ["ai", "new_energy", "robotics"]
    assert item["stock_symbols"] == ["sh.600001"]
    assert item["securities"][0]["identity_basis"] == "provider_explicit_security"
    assert item["coverage"] == "limited_recent_sample" and not item["auto_approval_eligible"]
    assert item["timestamp_status"] == "valid"
    assert len(item["source_hash"]) == len(item["pdf_hash"]) == len(item["content_hash"]) == 64


def test_search_hit_does_not_imply_title_relevance():
    data = n._normalize(capture(rows=[article(title="普通股东大会公告")]), RECEIVED)
    assert data["items"] == [] and data["provider_totals"] == {"ai": 1}
    assert n._normalize(capture(rows=[]), RECEIVED)["items"] == []


@pytest.mark.parametrize("stamp,status", [(None,"missing"), (int((RECEIVED+timedelta(days=1)).timestamp()*1000),"future")])
def test_unknown_future_never_fresh(stamp,status):
    value = capture()
    edit(value, lambda d: d["announcements"][0].update(announcementTime=stamp))
    item = n._normalize(value, RECEIVED)["items"][0]
    assert item["timestamp_status"] == status
    if stamp is None: assert item["published_at"] is None
    else: assert item["published_at"] > RECEIVED.isoformat()


@pytest.mark.parametrize("topics", [[],["unknown"],["ai","ai"],"ai",None,[True],["https://evil.invalid"]])
def test_topic_scope_before_network(topics,monkeypatch):
    monkeypatch.setattr(subprocess,"run",lambda *a,**kw:pytest.fail("network must not run"))
    with pytest.raises(n.NewsError): n.fetch_news(topics)


@pytest.mark.parametrize("timeout", [0,121,True,float("nan"),"5"])
def test_timeout_before_network(timeout):
    with pytest.raises(n.NewsError): n.fetch_news(timeout=timeout)


def test_hard_deadline_no_public_backdating(monkeypatch):
    def timeout(*a,**kw):
        assert kw["timeout"] == 2
        raise subprocess.TimeoutExpired(a[0],2)
    monkeypatch.setattr(subprocess,"run",timeout)
    with pytest.raises(n.NewsError,match="news_fetch_timeout"): n.fetch_news(timeout=2)
    assert list(inspect.signature(n.ingest_news).parameters) == ["connection","evidence_dir","topics","timeout"]


@pytest.mark.parametrize("mode", ["missing_pdf","extra_pdf","wrong_index","arbitrary_url","redirect_pdf","stamp","missing_topic","double_topic","pdf_body","base64","partial","duplicate_id","future_page","naive","html","bad_code"])
def test_capture_fail_closed(mode):
    value = capture()
    if mode == "missing_pdf": value["pages"].pop()
    if mode == "extra_pdf": value["pages"].append(deepcopy(value["pages"][-1]))
    if mode == "wrong_index": value["pages"][0]["params"]["pageNum"] = "2"
    if mode == "arbitrary_url": value["pages"][0]["url"] = "https://evil.invalid"
    if mode == "redirect_pdf": edit(value,lambda d:d["announcements"][0].update(adjunctUrl="//evil.invalid/file.pdf"))
    if mode == "stamp": edit(value,lambda d:d["announcements"][0].update(announcementTime=True))
    if mode == "missing_topic": value["topics"] = ["ai","robotics"]
    if mode == "double_topic": value["pages"].insert(0,deepcopy(value["pages"][0]))
    if mode == "pdf_body": value["pages"][1]["raw"] = base64.b64encode(b'<html>login</html>').decode()
    if mode == "base64": value["pages"][0]["raw"] = "@"
    if mode == "partial": edit(value,lambda d:d.update(totalAnnouncement=10))
    if mode == "duplicate_id": edit(value,lambda d:(d["announcements"].append(deepcopy(d["announcements"][0])),d.update(totalAnnouncement=2)))
    if mode == "future_page": value["pages"][0]["received_at"] = (RECEIVED+timedelta(seconds=1)).isoformat()
    if mode == "naive": value["started_at"] = "2026-11-18T00:00:00"
    if mode == "html": edit(value,lambda d:d["announcements"][0].update(announcementTitle="<script>人工智能</script>"))
    if mode == "bad_code": edit(value,lambda d:d["announcements"][0].update(secCode="600001; DROP"))
    with pytest.raises(n.NewsError): n._normalize(value,RECEIVED)


def test_conflicting_article_across_queries_rejected():
    value = capture(("ai","robotics"))
    page = value["pages"][1]
    raw = json.loads(base64.b64decode(page["raw"]))
    raw["announcements"][0]["secName"] = "变更公司"
    page["raw"] = base64.b64encode(n._canonical(raw)).decode()
    with pytest.raises(n.NewsError,match="changed_during_capture"): n._normalize(value,RECEIVED)


def test_unsupported_identity_never_guessed():
    value = capture()
    edit(value,lambda d:d["announcements"][0].update(secCode="900001",pageColumn="SHZB"))
    assert n._normalize(value,RECEIVED)["items"][0]["securities"] == []


@pytest.mark.parametrize("body", [b'{}',b'{"totalAnnouncement":true}',b'{"a":1,"a":2}',b'not json',b'{"n":NaN}'])
def test_invalid_json(body):
    with pytest.raises(n.NewsError): n._rows(body)


class Response:
    status_code = 200
    headers = {}
    def __init__(self,body): self.body = body
    def __enter__(self): return self
    def __exit__(self,*a): pass
    def iter_content(self,size): yield self.body


def test_fixed_http_no_credentials_redirect_retry(monkeypatch):
    reader,calls = n._HTTPReader(5),[]
    def post(url,**kw):
        calls.append((url,kw))
        return Response(b'{}')
    monkeypatch.setattr(reader.session,"post",post)
    reader.get("index","ai")
    assert reader.session.trust_env is False
    assert reader.session.get_adapter(n.INDEX_URL).max_retries.total == 0
    assert calls[0][0] == n.INDEX_URL and calls[0][1]["allow_redirects"] is False
    assert calls[0][1]["data"] == n._params("ai") and "verify" not in calls[0][1]
    reader.close()


@pytest.mark.parametrize("mode", ["status","encoding","bytes","requests","transport","url"])
def test_http_limits(mode,monkeypatch):
    reader,response = n._HTTPReader(5),Response(b'x'*100)
    if mode == "status": response.status_code = 302
    if mode == "encoding": response.headers = {"Content-Encoding":"gzip"}
    if mode == "bytes": monkeypatch.setattr(n,"MAX_TOTAL_BYTES",50)
    if mode == "requests": reader.requests = n.MAX_REQUESTS
    def post(*a,**kw):
        if mode == "transport": raise requests.Timeout("private details")
        return response
    monkeypatch.setattr(reader.session,"post",post)
    with pytest.raises(n.NewsError):
        reader.get("pdf",pdf_url="https://evil.invalid") if mode == "url" else reader.get("index","ai")
    reader.close()


@pytest.fixture(scope="session")
def dedicated_database():
    """Never migrate supplied admin DB; only create/drop our unique database."""
    dsn = os.getenv("TEST_ASHARE_DATABASE_URL")
    if not dsn: pytest.skip("TEST_ASHARE_DATABASE_URL required")
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import make_dsn
    admin = psycopg2.connect(dsn,connect_timeout=5);admin.autocommit=True
    name = "news_test_"+uuid.uuid4().hex
    with admin.cursor() as c:c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:yield make_dsn(dsn,dbname=name)
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
    with connection,connection.cursor() as c:
        schema = "case_"+uuid.uuid4().hex
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        c.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        c.execute((Path(__file__).parents[2]/"migrations/20261118_ashare_news.sql").read_text())
    monkeypatch.setattr(n,"_utcnow",lambda:RECEIVED)
    monkeypatch.setattr(n,"fetch_news",lambda *a,**kw:capture())
    yield n.NewsStore(connection),tmp_path
    connection.close()


def ingest(store):
    return n.ingest_news(store[0].connection,store[1],topics=["ai"])


def test_pg_archive_readonly_asof_and_hashes(store):
    repository,_ = store
    receipt = ingest(store)
    assert repository.list_items(["ai"],RECEIVED-timedelta(microseconds=1))["status"] == "not_observed"
    result = repository.list_items(["ai"],RECEIVED)
    assert len(result["items"]) == 1 and result["coverage"] == "limited_recent_sample"
    item = result["items"][0]
    assert item["received_at"] == item["known_at"] == RECEIVED.isoformat()
    assert item["id"] == receipt["receipt_id"]+":"+item["article_id"]
    path = Path(receipt["evidence_path"])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == receipt["source_hash"]
    assert hashlib.sha256((path.parent/"page-000.bin").read_bytes()).hexdigest() == item["source_hash"]
    assert hashlib.sha256((path.parent/"page-001.bin").read_bytes()).hexdigest() == item["pdf_hash"]
    repository.connection.set_session(readonly=True,isolation_level="REPEATABLE READ")
    assert repository.list_items(["ai"],RECEIVED)["items"] == result["items"]


@pytest.mark.parametrize("table", ["receipts","items"])
@pytest.mark.parametrize("operation", ["DELETE FROM","TRUNCATE","UPDATE"])
def test_pg_enforces_append_only(store,table,operation):
    import psycopg2
    repository,_ = store
    ingest(store)
    query = operation+" qd_ashare_news_"+table+(" SET id=id" if operation == "UPDATE" else "")
    if operation == "TRUNCATE" and table == "receipts": query += " CASCADE"
    with pytest.raises(psycopg2.Error,match="news_evidence_is_append_only"):
        with repository.connection,repository.connection.cursor() as c:c.execute(query)


def test_pg_revisions_preserved_duplicates_deduplicated(store,monkeypatch):
    repository,_ = store
    first = ingest(store)
    monkeypatch.setattr(n,"_utcnow",lambda:RECEIVED+timedelta(seconds=10))
    second = ingest(store)
    assert first["receipt_id"] != second["receipt_id"]
    assert len(repository.list_items(["ai"],RECEIVED+timedelta(seconds=10))["items"]) == 1
    value = capture(rows=[article(title="人工智能项目修订公告")])
    monkeypatch.setattr(n,"fetch_news",lambda *a,**kw:value)
    monkeypatch.setattr(n,"_utcnow",lambda:RECEIVED+timedelta(seconds=20))
    ingest(store)
    latest = repository.list_items(["ai"],RECEIVED+timedelta(seconds=20))["items"]
    assert len(latest) == 1 and latest[0]["title"] == "人工智能项目修订公告"
    with repository.connection,repository.connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM qd_ashare_news_items")
        assert cursor.fetchone()[0] == 3
    assert repository.list_items(["ai"],RECEIVED)["items"][0]["title"] == article()["announcementTitle"]


@pytest.mark.parametrize("mode", ["manifest","raw","missing","symlink","time","metadata","delete","details","identity","source_hash","extra"])
def test_pg_all_evidence_and_db_items_checked_before_limit(store,monkeypatch,mode):
    repository,_ = store
    monkeypatch.setattr(n,"fetch_news",lambda *a,**kw:capture(rows=[article(),article("1225670002")]))
    receipt = ingest(store)
    manifest = Path(receipt["evidence_path"])
    page = manifest.parent/"page-002.bin"
    if mode == "manifest":manifest.write_text("{}")
    if mode == "raw":page.write_bytes(b'corruption')
    if mode == "missing":page.unlink()
    if mode == "symlink":page.unlink();page.symlink_to(manifest)
    if mode in {"time","metadata","delete","details","identity","source_hash","extra"}:
        with repository.connection,repository.connection.cursor() as c:
            c.execute("ALTER TABLE qd_ashare_news_receipts DISABLE TRIGGER USER")
            c.execute("ALTER TABLE qd_ashare_news_items DISABLE TRIGGER USER")
            if mode == "time":c.execute("UPDATE qd_ashare_news_receipts SET received_at=received_at-interval '1 second'")
            if mode == "metadata":c.execute("UPDATE qd_ashare_news_receipts SET metadata='{}'::jsonb")
            if mode == "delete":c.execute("DELETE FROM qd_ashare_news_items WHERE article_id='1225670002'")
            if mode == "details":c.execute("UPDATE qd_ashare_news_items SET details=details || '{\"title\":\"changed\"}'::jsonb WHERE article_id='1225670002'")
            if mode == "identity":c.execute("UPDATE qd_ashare_news_items SET article_id='other' WHERE article_id='1225670002'")
            if mode == "source_hash":c.execute("UPDATE qd_ashare_news_items SET source_hash=repeat('0',64) WHERE article_id='1225670002'")
            if mode == "extra":c.execute("INSERT INTO qd_ashare_news_items SELECT id||'x',receipt_id,'extra',published_at,source_url,source_hash,content_hash,details FROM qd_ashare_news_items LIMIT 1")
    with pytest.raises(n.NewsError):repository.list_items(["ai"],RECEIVED,limit=1)


def test_pg_invalid_capture_cannot_publish(store,monkeypatch):
    repository,directory=store
    value=capture();value["pages"].pop()
    monkeypatch.setattr(n,"fetch_news",lambda *a,**kw:value)
    with pytest.raises(n.NewsError):ingest(store)
    assert list(directory.iterdir()) == []
    with repository.connection,repository.connection.cursor() as c:
        c.execute("SELECT count(*) FROM qd_ashare_news_receipts")
        assert c.fetchone()[0] == 0


@pytest.mark.parametrize("known_at,limit", [(RECEIVED,True),(RECEIVED,101),(RECEIVED,0),(RECEIVED.replace(tzinfo=None),1),(RECEIVED+timedelta(days=1),1)])
def test_pg_query_validation(store,known_at,limit):
    with pytest.raises(n.NewsError):store[0].list_items(["ai"],known_at,limit)


def test_pg_latest_revision_selected_before_topic_filter(store,monkeypatch):
    repository,directory=store
    ingest(store)
    value=capture(topics=["robotics"],rows=[article(title="机器人项目修订公告")])
    monkeypatch.setattr(n,"fetch_news",lambda *a,**kw:value)
    monkeypatch.setattr(n,"_utcnow",lambda:RECEIVED+timedelta(seconds=10))
    n.ingest_news(repository.connection,directory,topics=["robotics"])
    assert repository.list_items(["ai"],RECEIVED+timedelta(seconds=10))["items"] == []
    assert repository.list_items(["ai"],RECEIVED)["items"]
    assert repository.list_items(["robotics"],RECEIVED+timedelta(seconds=10))["items"][0]["title"] == "机器人项目修订公告"


def test_child_failure_preserves_only_known_machine_error(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(subprocess,"run",lambda *a,**kw:SimpleNamespace(returncode=1,stdout='{"error":"news_pdf_response_invalid"}'))
    with pytest.raises(n.NewsError,match="^news_pdf_response_invalid$"):n.fetch_news(["ai"])


def test_cli_failure_preserves_only_known_machine_error(monkeypatch,capsys):
    import io
    monkeypatch.setattr(n.sys,"stdin",io.StringIO('{"topics":["ai"],"timeout":2}'))
    def fail(*a,**kw):raise n.NewsError("news_pdf_response_invalid")
    monkeypatch.setattr(n,"_fetch_direct",fail)
    assert n.main(["_fetch"]) == 1
    assert json.loads(capsys.readouterr().out) == {"error":"news_pdf_response_invalid"}


@pytest.mark.parametrize("code", ["/private/secret", "news_fake_secret", "http token=secret", None, 3])
def test_child_unknown_error_is_redacted(monkeypatch,code):
    from types import SimpleNamespace
    monkeypatch.setattr(subprocess,"run",lambda *a,**kw:SimpleNamespace(returncode=1,stdout=json.dumps({"error":code})))
    with pytest.raises(n.NewsError,match="^news_fetch_failed$"):n.fetch_news(["ai"])


@pytest.mark.parametrize("error", [n.NewsError("/private/secret"), OSError("/private/secret"), ValueError("news_http_rejected")])
def test_only_explicit_typed_machine_errors_are_public(error):
    assert n.safe_error_code(error) == "news_operation_failed"


def test_declared_oversized_pdf_excluded_before_download_and_from_items():
    rows=[article(),article("1225670002")]
    rows[0]["adjunctSize"]=135
    rows[1]["adjunctSize"]=18649
    value=capture(rows=rows)
    value["pages"].pop()
    result=n._normalize(value,RECEIVED)
    assert [item["article_id"] for item in result["items"]] == ["1225670001"]
    assert result["excluded_items"] == [{"article_id":"1225670002","title":rows[1]["announcementTitle"],
        "topics":["ai"],"source_url":n.PDF_ORIGIN+rows[1]["adjunctUrl"],"declared_size_kb":18649,
        "size_unit_bytes":1024,"reason":"provider_declared_pdf_over_limit","original_pdf_archived":False}]


def test_fetch_does_not_request_provider_declared_oversized_pdf(monkeypatch):
    rows=[article(),article("1225670002")]
    rows[1]["adjunctSize"]=18649
    value=capture(rows=rows)
    calls=[]
    class Reader:
        def __init__(self,timeout):pass
        def close(self):pass
        def get(self,kind,topic=None,pdf_url=None):
            calls.append((kind,topic,pdf_url))
            if kind == "index":return value["pages"][0]
            assert pdf_url == value["pages"][1]["url"]
            return value["pages"][1]
    monkeypatch.setattr(n,"_HTTPReader",Reader)
    result=n._fetch_direct(["ai"],2)
    assert len(calls)==2 and len(result["pages"])==2


@pytest.mark.parametrize("size", [True,-1,1.5,"18649"])
def test_invalid_declared_pdf_size_rejected(size):
    value=capture()
    edit(value,lambda d:d["announcements"][0].update(adjunctSize=size))
    with pytest.raises(n.NewsError,match="invalid_news_attachment_size"):n._normalize(value,RECEIVED)


@pytest.mark.parametrize("size", [None,1])
def test_missing_or_underreported_size_still_enforces_actual_pdf_bytes(size,monkeypatch):
    value=capture()
    edit(value,lambda d:d["announcements"][0].update(adjunctSize=size))
    monkeypatch.setattr(n,"MAX_PDF_BYTES",2048)
    value["pages"][1]["raw"]=base64.b64encode(b'%PDF-'+b'x'*2048).decode()
    with pytest.raises(n.NewsError,match="news_response_budget_exceeded"):n._normalize(value,RECEIVED)


def test_pg_exclusion_is_archived_but_never_a_research_item(store,monkeypatch):
    repository,_=store
    rows=[article(),article("1225670002")]
    rows[1]["adjunctSize"]=23517
    value=capture(rows=rows);value["pages"].pop()
    monkeypatch.setattr(n,"fetch_news",lambda *a,**kw:value)
    receipt=ingest(store)
    assert receipt["item_count"]==1 and receipt["excluded_items"][0]["article_id"]=="1225670002"
    result=repository.list_items(["ai"],RECEIVED)
    assert [item["article_id"] for item in result["items"]]==["1225670001"]
    assert result["receipts"][0]["excluded_items"]==receipt["excluded_items"]
    manifest=json.loads(Path(receipt["evidence_path"]).read_bytes())
    assert manifest["normalized"]["excluded_items"]==receipt["excluded_items"]


def test_all_declared_oversize_publishes_no_fake_research_items():
    row=article();row["adjunctSize"]=18649
    value=capture(rows=[row]);value["pages"].pop()
    normalized=n._normalize(value,RECEIVED)
    assert normalized["items"]==[] and len(normalized["excluded_items"])==1


def test_pg_publication_sort_before_limit_with_future_and_missing_last(store,monkeypatch):
    repository,_=store
    rows=[article(str(1225670001+i)) for i in range(4)]
    rows[0]["announcementTime"]=int((RECEIVED-timedelta(days=60)).timestamp()*1000)
    rows[1]["announcementTime"]=int((RECEIVED-timedelta(hours=1)).timestamp()*1000)
    rows[2]["announcementTime"]=None
    rows[3]["announcementTime"]=int((RECEIVED+timedelta(days=1)).timestamp()*1000)
    monkeypatch.setattr(n,"fetch_news",lambda *a,**kw:capture(rows=rows))
    ingest(store)
    result=repository.list_items(["ai"],RECEIVED)
    assert [item["article_id"] for item in result["items"]] == ["1225670002","1225670001","1225670004","1225670003"]
    assert repository.list_items(["ai"],RECEIVED,limit=1)["items"][0]["article_id"] == "1225670002"
    assert result["items"][2]["timestamp_status"] == "future"


def test_pg_sort_does_not_resurrect_older_revision_with_newer_publication(store,monkeypatch):
    repository,_=store
    rows=[article("1225670001"),article("1225670002")]
    rows[1]["announcementTime"]=int((RECEIVED-timedelta(hours=2)).timestamp()*1000)
    monkeypatch.setattr(n,"fetch_news",lambda *a,**kw:capture(rows=rows))
    ingest(store)
    revision=article("1225670001",title="人工智能公告更正日期")
    revision["announcementTime"]=int((RECEIVED-timedelta(hours=3)).timestamp()*1000)
    monkeypatch.setattr(n,"fetch_news",lambda *a,**kw:capture(rows=[revision]))
    later=RECEIVED+timedelta(seconds=10)
    monkeypatch.setattr(n,"_utcnow",lambda:later)
    ingest(store)
    result=repository.list_items(["ai"],later)["items"]
    assert [item["article_id"] for item in result] == ["1225670002","1225670001"]
    assert result[1]["title"] == "人工智能公告更正日期"
    assert repository.list_items(["ai"],RECEIVED,limit=1)["items"][0]["article_id"] == "1225670001"
