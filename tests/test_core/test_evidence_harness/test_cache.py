"""Tests for HarnessCache and caching provider wrappers."""

import json
from pathlib import Path

import pytest

from core.evidence_harness.cache import (
    HarnessCache,
    CachingSearchProvider,
    CachingFetchProvider,
    ReplaySearchProvider,
    ReplayFetchProvider,
    ReplayCacheMiss,
)
from core.evidence_harness.schema import SearchQuery, SearchResult, FetchResult
from core.evidence_harness.ids import query_id as make_query_id


def _query(text: str = "WTI price move 2024-09-25") -> SearchQuery:
    qid = make_query_id("case_test", text, None, None, [])
    return SearchQuery(query_id=qid, case_id="case_test", text=text)


def _result(url: str, rank: int = 1) -> SearchResult:
    return SearchResult(
        query_id="q1", result_id=f"res_{rank}", provider="fixture",
        rank=rank, title="Test", url=url, domain="example.com",
    )


class TestHarnessCache:
    def test_search_roundtrip(self, tmp_path):
        cache = HarnessCache(str(tmp_path))
        q = _query()
        results = [_result("https://eia.gov/report")]

        assert cache.load_search(q) is None
        cache.save_search(q, results)
        loaded = cache.load_search(q)
        assert loaded is not None
        assert loaded[0].url == "https://eia.gov/report"

    def test_page_roundtrip(self, tmp_path):
        cache = HarnessCache(str(tmp_path))
        content = b"<html><body>EIA report</body></html>"
        content_hash = "sha256:abc123def456"

        assert cache.load_page(content_hash) is None
        cache.save_page(content, content_hash, "text/html")
        loaded = cache.load_page(content_hash)
        assert loaded == content

    def test_page_path_for_hash_returns_path_when_exists(self, tmp_path):
        cache = HarnessCache(str(tmp_path))
        content_hash = "sha256:abc"
        cache.save_page(b"content", content_hash)
        path = cache.page_path_for_hash(content_hash)
        assert path is not None
        assert path.exists()

    def test_page_path_for_hash_returns_none_when_missing(self, tmp_path):
        cache = HarnessCache(str(tmp_path))
        assert cache.page_path_for_hash("sha256:nonexistent") is None

    def test_extract_roundtrip(self, tmp_path):
        from core.evidence_harness.schema import ExtractedDocument
        cache = HarnessCache(str(tmp_path))
        doc = ExtractedDocument(
            document_id="doc1", fetch_id="f1",
            canonical_url="https://eia.gov/report", domain="eia.gov",
            accessed_at="2024-01-01T00:00:00Z",
            extracted_text="EIA report text", excerpt="EIA report",
            content_hash="sha256:abc", extraction_version="plaintext.v1",
        )
        assert cache.load_extract("sha256:abc", "plaintext.v1") is None
        cache.save_extract(doc)
        loaded = cache.load_extract("sha256:abc", "plaintext.v1")
        assert loaded is not None
        assert loaded.document_id == "doc1"

    def test_search_cache_path_is_deterministic(self, tmp_path):
        cache = HarnessCache(str(tmp_path))
        q = _query()
        p1 = cache.search_path(q)
        p2 = cache.search_path(q)
        assert p1 == p2

    def test_search_entry_format(self, tmp_path):
        cache = HarnessCache(str(tmp_path))
        q = _query()
        cache.save_search(q, [_result("https://eia.gov/report")])
        entry = cache.search_entry(q, "fixture")
        assert entry["type"] == "search"
        assert entry["query_id"] == q.query_id
        assert "cache_path" in entry


class TestCachingSearchProvider:
    def _make_mock(self, results):
        class MockProvider:
            name = "mock"
            call_count = 0
            def search(self, q, *, max_results=10, timeout_sec=15):
                self.call_count += 1
                return results
        return MockProvider()

    def test_caches_results_after_first_call(self, tmp_path):
        mock = self._make_mock([_result("https://eia.gov")])
        cache = HarnessCache(str(tmp_path))
        provider = CachingSearchProvider(mock, cache)
        q = _query()

        r1 = provider.search(q)
        r2 = provider.search(q)
        assert mock.call_count == 1
        assert r1[0].url == r2[0].url

    def test_returns_cached_results_without_inner_call(self, tmp_path):
        mock = self._make_mock([])
        cache = HarnessCache(str(tmp_path))
        q = _query()
        cache.save_search(q, [_result("https://cached.com")])

        provider = CachingSearchProvider(mock, cache)
        results = provider.search(q)
        assert mock.call_count == 0
        assert results[0].url == "https://cached.com"

    def test_cache_entries_recorded(self, tmp_path):
        mock = self._make_mock([_result("https://eia.gov")])
        cache = HarnessCache(str(tmp_path))
        provider = CachingSearchProvider(mock, cache)
        provider.search(_query("WTI price move 2024-09-25"))
        entries = provider.cache_entries()
        assert len(entries) == 1
        assert entries[0]["type"] == "search"


class TestReplaySearchProvider:
    def test_returns_cached_results(self, tmp_path):
        cache = HarnessCache(str(tmp_path))
        q = _query()
        cache.save_search(q, [_result("https://eia.gov")])

        provider = ReplaySearchProvider(cache)
        results = provider.search(q)
        assert results[0].url == "https://eia.gov"

    def test_raises_on_cache_miss(self, tmp_path):
        cache = HarnessCache(str(tmp_path))
        provider = ReplaySearchProvider(cache)
        with pytest.raises(ReplayCacheMiss):
            provider.search(_query("not in cache"))


class TestReplayFetchProvider:
    def test_fetches_from_page_cache(self, tmp_path):
        cache = HarnessCache(str(tmp_path))
        url = "https://eia.gov/report"
        content = b"<html>EIA content</html>"
        content_hash = "sha256:eiatest123"
        cache.save_page(content, content_hash, "text/html")

        manifest_entries = [{"type": "page", "url": url, "content_hash": content_hash}]
        provider = ReplayFetchProvider(cache, manifest_entries)
        result = provider.fetch(url)
        assert result.status_code == 200
        assert result.content_hash == content_hash

    def test_blocked_when_url_not_in_manifest(self, tmp_path):
        cache = HarnessCache(str(tmp_path))
        provider = ReplayFetchProvider(cache, [])
        result = provider.fetch("https://unknown.com/page")
        assert result.status_code == 404
        assert "replay" in (result.blocked_reason or "")

    def test_raises_on_missing_page_cache(self, tmp_path):
        cache = HarnessCache(str(tmp_path))
        url = "https://eia.gov/report"
        manifest_entries = [{"type": "page", "url": url, "content_hash": "sha256:missing"}]
        provider = ReplayFetchProvider(cache, manifest_entries)
        with pytest.raises(ReplayCacheMiss):
            provider.fetch(url)
