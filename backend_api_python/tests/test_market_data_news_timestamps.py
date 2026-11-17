from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.market_data_collector import MarketDataCollector
from app.services.search_models import SearchResponse, SearchResult
from app.professional_report.snapshot import build_evidence_snapshot


@pytest.mark.parametrize("published", [None, "", "not-a-date", "2 hours ago"])
def test_unknown_news_time_cannot_become_fresh_report_evidence(published):
    snapshot = build_evidence_snapshot({
        "market": "CNStock", "symbol": "000001", "collected_at": "2026-11-17T12:00:00Z",
        "news": [{"headline": "Undated source item", "datetime": published,
                           "received_at": "2026-11-17T11:59:59Z", "url": "https://example.test/news"}],
    })
    assert not any(row["category"] == "news" for row in snapshot["observations"])
    assert "news_publication_time_unknown" in snapshot["quality_flags"]
    assert snapshot["collection"]["excluded_unknown_news_timestamp_items"] == 1


@pytest.mark.parametrize("method", ["_get_news_from_search", "_get_global_major_events"])
@pytest.mark.parametrize("published", [None, "", "2024-03-01", "2024-03-01T08:30:00+08:00"])
def test_search_news_keeps_unknown_publication_separate_from_receipt(monkeypatch, method, published):
    response = SearchResponse(query="fixture", provider="fixture", results=[SearchResult(
        title="Company sanctions update", snippet="Original source text",
        url="https://example.test/original", source="fixture", published_date=published,
    )])
    service = SimpleNamespace(is_available=True,
                              search_stock_news=lambda **kwargs: response,
                              search_with_fallback=lambda **kwargs: response)
    monkeypatch.setattr("app.services.search.get_search_service", lambda: service)
    collector = MarketDataCollector.__new__(MarketDataCollector)
    before = datetime.now(timezone.utc)
    args = ("CNStock", "000001") if method == "_get_news_from_search" else ()
    news = getattr(collector, method)(*args)
    after = datetime.now(timezone.utc)
    assert len(news) == 1
    assert news[0]["datetime"] == (published or "")
    assert before <= datetime.fromisoformat(news[0]["received_at"].replace("Z", "+00:00")) <= after
    assert news[0]["url"] == "https://example.test/original"


@pytest.mark.parametrize("timestamp", [None, 0, "", -1, "not-a-date", True, float("nan"), float("inf")])
def test_missing_finnhub_publication_is_unknown_not_epoch(monkeypatch, timestamp):
    collector = MarketDataCollector.__new__(MarketDataCollector)
    collector._finnhub_client = _FakeFinnhubClient(timestamp)
    collector._get_news_from_search = lambda *_args, **_kwargs: []
    monkeypatch.setenv("FAST_ANALYSIS_INCLUDE_GLOBAL_NEWS", "false")
    monkeypatch.setenv("FINNHUB_FREE_ONLY", "true")
    before = datetime.now(timezone.utc)
    news = collector._get_news("USStock", "TSLA")["news"]
    assert news[0]["datetime"] == ""
    assert before <= datetime.fromisoformat(news[0]["received_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc)


class _FakeFinnhubClient:
    def __init__(self, timestamp: int) -> None:
        self.timestamp = timestamp

    def company_news(self, *_args, **_kwargs):
        return [{
            "datetime": self.timestamp,
            "headline": "Confirmed company update",
            "summary": "A timestamp conversion fixture.",
            "source": "Finnhub",
            "url": "https://example.test/news",
        }]


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="requires POSIX timezone control")
def test_finnhub_unix_timestamp_is_utc_when_server_uses_asia_shanghai(monkeypatch):
    expected = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    collector = MarketDataCollector.__new__(MarketDataCollector)
    collector._finnhub_client = _FakeFinnhubClient(int(expected.timestamp()))
    collector._get_news_from_search = lambda *_args, **_kwargs: []
    monkeypatch.setenv("FINNHUB_FREE_ONLY", "true")

    previous_timezone = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "Asia/Shanghai"
        time.tzset()
        result = collector._get_news("USStock", "TSLA")
    finally:
        if previous_timezone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous_timezone
        time.tzset()

    assert result["news"][0]["datetime"] == "2026-09-08T12:00:00Z"


def test_undated_search_news_is_not_sorted_as_newest(monkeypatch):
    response = SearchResponse(query="fixture", provider="fixture", results=[
        SearchResult(title="Undated article", snippet="", url="https://example.test/undated", source="fixture"),
        SearchResult(title="Dated article", snippet="", url="https://example.test/dated", source="fixture",
                     published_date="2024-03-01"),
    ])
    service = SimpleNamespace(is_available=True, search_stock_news=lambda **kwargs: response)
    monkeypatch.setattr("app.services.search.get_search_service", lambda: service)
    monkeypatch.setenv("FAST_ANALYSIS_INCLUDE_GLOBAL_NEWS", "false")
    collector = MarketDataCollector.__new__(MarketDataCollector)
    collector._finnhub_client = None
    result = collector._get_news("CNStock", "000001")["news"]
    assert [item["headline"] for item in result] == ["Dated article", "Undated article"]
    assert result[1]["datetime"] == ""
