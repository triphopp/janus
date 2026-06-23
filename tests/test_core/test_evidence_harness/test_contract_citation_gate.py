"""Contract tests: citation verification is a hard gate for supported_event.

These tests ensure the verdict cannot be 'supported_event' when citation
verification fails — even if the deterministic check logic would otherwise
produce that verdict.
"""

from __future__ import annotations

import pytest

from core.evidence_harness.citations import verify_citations
from core.evidence_harness.schema import SourceRegistryRecord


def _reg(doc_id: str, url: str = "https://sec.gov/test",
         source_tier: str = "tier1_official") -> SourceRegistryRecord:
    return SourceRegistryRecord(
        document_id=doc_id,
        source_id=f"src_{doc_id}",
        url=url,
        final_url=url,
        domain=url.split("/")[2],
        source_tier=source_tier,
        fetched_at="2024-01-25T10:00:00+00:00",
        accessed_at="2024-01-25T10:01:00+00:00",
        content_hash="sha256:abc",
        extract_hash="sha256:def",
        provider="fixture",
        title="Test Source",
        published_at=None,
        query_ids=[],
        cache_paths={},
    )


class TestCitationGateContract:
    def test_supported_event_with_missing_doc_id_is_fail(self):
        summary = {"supporting_document_ids": ["doc_missing"], "contradicting_document_ids": []}
        report = verify_citations(summary=summary, registry=[], verdict="supported_event")
        assert report["status"] == "fail"
        assert "doc_missing" in report["missing_document_ids"]

    def test_supported_event_with_unfetched_url_is_fail(self):
        summary = {
            "supporting_document_ids": [],
            "contradicting_document_ids": [],
            "source_urls": ["https://unfetched.example.com/article"],
        }
        report = verify_citations(summary=summary, registry=[], verdict="supported_event")
        assert report["status"] == "fail"

    def test_supported_event_with_zero_registered_support_is_fail(self):
        summary = {"supporting_document_ids": [], "contradicting_document_ids": []}
        report = verify_citations(summary=summary, registry=[], verdict="supported_event")
        assert report["status"] == "fail"

    def test_no_llm_summary_yields_skip(self):
        report = verify_citations(summary={}, registry=[], verdict="insufficient_evidence")
        assert report["status"] == "skip"

    def test_registered_doc_id_is_pass(self):
        reg = [_reg("doc_aaa")]
        summary = {"supporting_document_ids": ["doc_aaa"], "contradicting_document_ids": []}
        report = verify_citations(summary=summary, registry=reg, verdict="supported_event")
        assert report["status"] == "pass"
        assert report["supporting_registered_sources"] == 1

    def test_title_mismatch_is_warn_not_fail(self):
        reg = [_reg("doc_aaa")]
        summary = {
            "supporting_document_ids": ["doc_aaa"],
            "contradicting_document_ids": [],
            "source_titles": {"doc_aaa": "Wrong Title"},
        }
        report = verify_citations(summary=summary, registry=reg, verdict="supported_event")
        assert report["status"] == "warn"

    def test_legacy_cited_document_ids_not_used(self):
        """supporting_document_ids is canonical; cited_document_ids is a legacy alias."""
        reg = [_reg("doc_aaa")]
        summary = {
            "cited_document_ids": ["doc_aaa"],  # legacy — should NOT be used by verifier
            "supporting_document_ids": [],       # canonical — empty → fail
            "contradicting_document_ids": [],
        }
        report = verify_citations(summary=summary, registry=reg, verdict="supported_event")
        # verifier should check supporting_document_ids, not cited_document_ids
        assert report["status"] == "fail"


class TestCitationGateInController:
    """Integration: controller must not write supported_event when citation fails."""

    def _run_mock_harness(self, llm_summary: dict, tmp_path):
        from unittest.mock import patch, MagicMock
        from core.evidence_harness.schema import OutlierCasePackage
        from core.evidence_harness.config import HarnessConfig
        from core.evidence_harness.controller import run_harness

        cfg = HarnessConfig(
            mode="mock", enabled=True,
            artifact_dir=str(tmp_path),
            cache_dir=str(tmp_path),
            llm_enabled=True,
            llm_provider="mock",
        )
        case = OutlierCasePackage(
            case_id="case_test_cg", run_id="run_cg",
            signal_type="return_outlier", as_of_date="2024-01-25",
            family="equity", symbol="TSLA", metric_name="return_std",
        )

        mock_search = MagicMock()
        mock_search.name = "mock_search"
        mock_search.search.return_value = []

        mock_fetch = MagicMock()
        mock_fetch.name = "mock_fetch"

        with patch("core.evidence_harness.llm.build_llm_client"), \
             patch("core.evidence_harness.llm.run_claim_extraction", return_value=[]), \
             patch("core.evidence_harness.llm.run_evidence_summary", return_value=llm_summary):
            result = run_harness(case, cfg,
                                 search_provider=mock_search,
                                 fetch_provider=mock_fetch)
        return result

    def test_llm_summary_with_missing_doc_does_not_produce_supported_event(self, tmp_path):
        llm_summary = {
            "summary": "TSLA fell on news.",
            "supporting_document_ids": ["doc_nonexistent"],
            "contradicting_document_ids": [],
        }
        result = self._run_mock_harness(llm_summary, tmp_path)
        # Sources registry is empty → cited doc is missing → citation fail → downgrade
        assert result.verdict != "supported_event"

    def test_llm_summary_valid_false_when_citation_fails(self, tmp_path):
        import json
        llm_summary = {
            "summary": "TSLA fell.",
            "supporting_document_ids": ["doc_nonexistent"],
            "contradicting_document_ids": [],
        }
        result = self._run_mock_harness(llm_summary, tmp_path)
        verdict_path = result.artifact_paths.get("verdict")
        assert verdict_path is not None, "verdict.json not written"
        vd = json.loads(open(verdict_path).read())
        assert vd["llm_summary_valid"] is False


class TestNoLLMCitationGate:
    """B3: no-LLM supported_event must never produce citation_status=skip."""

    def _run_no_llm_harness(self, tmp_path, *, inject_sources=True):
        """Run harness in no-LLM mode, optionally injecting credible sources."""
        from unittest.mock import patch, MagicMock
        from core.evidence_harness.schema import (
            OutlierCasePackage, FetchResult, SearchResult, SourceRegistryRecord,
        )
        from core.evidence_harness.config import HarnessConfig
        from core.evidence_harness.controller import run_harness

        cfg = HarnessConfig(
            mode="mock", enabled=True,
            artifact_dir=str(tmp_path),
            cache_dir=str(tmp_path),
            llm_enabled=False,
        )
        case = OutlierCasePackage(
            case_id="case_b3_nollm", run_id="run_b3",
            signal_type="return_outlier", as_of_date="2024-01-25",
            family="equity", symbol="TSLA", metric_name="return_std",
        )

        if inject_sources:
            # Patch extract to return a real document so the harness
            # builds a source registry entry with content/extract hash.
            from core.evidence_harness.schema import ExtractedDocument

            fake_doc = ExtractedDocument(
                document_id="doc_tier1",
                fetch_id="fid1",
                canonical_url="https://sec.gov/8k",
                domain="sec.gov",
                title="SEC 8-K",
                extracted_text="TSLA reports Q4 earnings.",
                excerpt="TSLA reports Q4 earnings.",
                extraction_version="plaintext.v1",
                content_hash="sha256:abc",
                source_tier="tier1_official",
                accessed_at="2024-01-25T10:01:00+00:00",
            )
            mock_search = MagicMock()
            mock_search.name = "fixture"
            mock_search.search.return_value = [
                SearchResult(
                    query_id="q1", result_id="r1",
                    url="https://sec.gov/8k", title="SEC 8-K",
                    snippet="TSLA earnings", domain="sec.gov",
                    published_at="2024-01-25T09:00:00+00:00",
                    provider="fixture", rank=1,
                )
            ]
            fetch_result = FetchResult(
                fetch_id="fid1", url="https://sec.gov/8k", final_url="https://sec.gov/8k",
                status_code=200, content_type="text/html",
                fetched_at="2024-01-25T10:00:00+00:00",
                bytes_read=100, content_hash="sha256:abc", blocked_reason=None,
                text_or_html_path=None,
            )
            mock_fetch = MagicMock()
            mock_fetch.name = "fixture"
            mock_fetch.fetch.return_value = fetch_result

            with patch("core.evidence_harness.extract.PlainTextExtractor.extract",
                       return_value=fake_doc):
                result = run_harness(case, cfg,
                                     search_provider=mock_search,
                                     fetch_provider=mock_fetch)
        else:
            mock_search = MagicMock()
            mock_search.name = "fixture"
            mock_search.search.return_value = []
            mock_fetch = MagicMock()
            mock_fetch.name = "fixture"
            result = run_harness(case, cfg,
                                 search_provider=mock_search,
                                 fetch_provider=mock_fetch)

        return result

    def test_no_llm_supported_event_never_has_skip_citation(self, tmp_path):
        result = self._run_no_llm_harness(tmp_path, inject_sources=True)
        if result.verdict == "supported_event":
            # If verdict is supported_event, citation_status must NOT be skip
            import json
            vd = json.loads(open(result.artifact_paths["verdict"]).read())
            assert vd["citation_status"] != "skip", \
                "supported_event must not have citation_status=skip"

    def test_no_llm_no_sources_does_not_produce_supported_event(self, tmp_path):
        result = self._run_no_llm_harness(tmp_path, inject_sources=False)
        assert result.verdict != "supported_event", \
            "With no sources, verdict cannot be supported_event"

    def test_no_llm_supported_verdict_has_pass_citation_when_tier1_present(self, tmp_path):
        """When tier1 source present, no-LLM supported_event must have citation_status=pass."""
        import json
        result = self._run_no_llm_harness(tmp_path, inject_sources=True)
        vd = json.loads(open(result.artifact_paths["verdict"]).read())
        if vd["verdict"] == "supported_event":
            assert vd["citation_status"] == "pass"
