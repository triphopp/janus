"""Tests for DuckDuckGoSearchProvider — mocked, no real network calls."""

import pytest
from unittest.mock import MagicMock, patch

from core.evidence_harness.search_ddg import DuckDuckGoSearchProvider, _result_id, _extract_domain
from core.evidence_harness.schema import SearchQuery
from core.evidence_harness.ids import query_id as make_query_id


def _query(text: str = "WTI crude oil price 2024-09-25") -> SearchQuery:
    qid = make_query_id("case_test", text, None, None, [])
    return SearchQuery(query_id=qid, case_id="case_test", text=text)


def _ddg_hit(title: str, url: str, body: str = "") -> dict:
    return {"href": url, "title": title, "body": body}


class TestDuckDuckGoSearchProvider:
    def _mock_ddgs(self, items):
        """Patch DDGS so .text() returns the given items."""
        mock_ddgs_inst = MagicMock()
        mock_ddgs_inst.__enter__ = MagicMock(return_value=mock_ddgs_inst)
        mock_ddgs_inst.__exit__ = MagicMock(return_value=False)
        mock_ddgs_inst.text.return_value = iter(items)

        patcher = patch(
            "core.evidence_harness.search_ddg.DDGS",
            return_value=mock_ddgs_inst,
        )
        return patcher

    def test_returns_search_results(self):
        provider = DuckDuckGoSearchProvider(min_delay_sec=0)
        hits = [
            _ddg_hit("EIA Inventory Report", "https://eia.gov/report", "Weekly crude oil inventory"),
            _ddg_hit("Reuters WTI", "https://reuters.com/wti", "WTI fell sharply"),
        ]
        with self._mock_ddgs(hits):
            results = provider.search(_query())

        assert len(results) == 2
        assert results[0].url == "https://eia.gov/report"
        assert results[0].title == "EIA Inventory Report"
        assert results[0].provider == "duckduckgo"

    def test_ranks_start_at_1(self):
        provider = DuckDuckGoSearchProvider(min_delay_sec=0)
        hits = [_ddg_hit("A", "https://a.com"), _ddg_hit("B", "https://b.com")]
        with self._mock_ddgs(hits):
            results = provider.search(_query())
        assert results[0].rank == 1
        assert results[1].rank == 2

    def test_query_id_propagated(self):
        provider = DuckDuckGoSearchProvider(min_delay_sec=0)
        q = _query()
        hits = [_ddg_hit("A", "https://a.com")]
        with self._mock_ddgs(hits):
            results = provider.search(q)
        assert results[0].query_id == q.query_id

    def test_result_id_is_deterministic(self):
        provider = DuckDuckGoSearchProvider(min_delay_sec=0)
        q = _query()
        hits = [_ddg_hit("A", "https://a.com")]
        with self._mock_ddgs(hits):
            r1 = provider.search(q)
        with self._mock_ddgs(hits):
            r2 = provider.search(q)
        assert r1[0].result_id == r2[0].result_id

    def test_skips_results_with_no_url(self):
        provider = DuckDuckGoSearchProvider(min_delay_sec=0)
        hits = [{"href": "", "title": "No URL", "body": ""}, _ddg_hit("Good", "https://good.com")]
        with self._mock_ddgs(hits):
            results = provider.search(_query())
        assert len(results) == 1
        assert results[0].url == "https://good.com"

    def test_respects_max_results(self):
        provider = DuckDuckGoSearchProvider(min_delay_sec=0)
        hits = [_ddg_hit(f"Item {i}", f"https://example.com/{i}") for i in range(20)]
        with self._mock_ddgs(hits):
            results = provider.search(_query(), max_results=5)
        assert len(results) <= 5

    def test_returns_empty_list_on_ddg_exception(self):
        provider = DuckDuckGoSearchProvider(min_delay_sec=0)

        mock_ddgs_inst = MagicMock()
        mock_ddgs_inst.__enter__ = MagicMock(return_value=mock_ddgs_inst)
        mock_ddgs_inst.__exit__ = MagicMock(return_value=False)
        mock_ddgs_inst.text.side_effect = Exception("DDG rate limit")

        with patch("core.evidence_harness.search_ddg.DDGS", return_value=mock_ddgs_inst):
            results = provider.search(_query())
        assert results == []

    def test_snippet_captured(self):
        provider = DuckDuckGoSearchProvider(min_delay_sec=0)
        hits = [_ddg_hit("Reuters", "https://reuters.com/wti", "WTI crude fell 3%")]
        with self._mock_ddgs(hits):
            results = provider.search(_query())
        assert results[0].snippet == "WTI crude fell 3%"

    def test_domain_extracted(self):
        provider = DuckDuckGoSearchProvider(min_delay_sec=0)
        hits = [_ddg_hit("EIA", "https://www.eia.gov/report")]
        with self._mock_ddgs(hits):
            results = provider.search(_query())
        assert results[0].domain == "eia.gov"


class TestHelpers:
    def test_result_id_is_deterministic(self):
        r1 = _result_id("q1", "https://eia.gov", 1)
        r2 = _result_id("q1", "https://eia.gov", 1)
        assert r1 == r2

    def test_result_id_differs_by_rank(self):
        assert _result_id("q1", "https://eia.gov", 1) != _result_id("q1", "https://eia.gov", 2)

    def test_extract_domain_strips_www(self):
        assert _extract_domain("https://www.reuters.com/article") == "reuters.com"

    def test_extract_domain_plain(self):
        assert _extract_domain("https://eia.gov/report") == "eia.gov"


class TestConfigIntegration:
    def test_controller_selects_ddg_provider(self):
        from core.evidence_harness.config import load_harness_config
        cfg = load_harness_config()
        cfg.search_provider = "duckduckgo"
        cfg.mode = "live"
        from core.evidence_harness.controller import _make_search_provider
        provider = _make_search_provider(cfg)
        assert isinstance(provider, DuckDuckGoSearchProvider)

    def test_controller_selects_httpx_provider(self):
        from core.evidence_harness.config import load_harness_config
        from core.evidence_harness.fetch_http import HttpxFetchProvider
        cfg = load_harness_config()
        cfg.fetch_provider = "httpx"
        cfg.mode = "live"
        from core.evidence_harness.controller import _make_fetch_provider
        provider = _make_fetch_provider(cfg)
        assert isinstance(provider, HttpxFetchProvider)

    def test_config_validates_unknown_search_provider(self):
        from core.evidence_harness.config import HarnessConfig
        cfg = HarnessConfig(search_provider="nonexistent")
        with pytest.raises(ValueError, match="search_provider"):
            cfg.validate()

    def test_config_validates_unknown_fetch_provider(self):
        from core.evidence_harness.config import HarnessConfig
        cfg = HarnessConfig(fetch_provider="nonexistent")
        with pytest.raises(ValueError, match="fetch_provider"):
            cfg.validate()
