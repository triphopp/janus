"""Tests for citation verifier."""

import pytest
from core.evidence_harness.citations import verify_citations
from core.evidence_harness.schema import SourceRegistryRecord


def _src(doc_id: str) -> SourceRegistryRecord:
    return SourceRegistryRecord(
        document_id=doc_id, source_id=f"src_{doc_id}",
        url=f"https://eia.gov/{doc_id}", final_url=f"https://eia.gov/{doc_id}",
        domain="eia.gov", source_tier="tier1_official",
        fetched_at="2024-01-01T00:00:00Z", accessed_at="2024-01-01T00:00:00Z",
        content_hash="sha256:abc", extract_hash="sha256:def", provider="fixture",
    )


class TestCitationVerifier:
    def test_no_summary_returns_skip(self):
        report = verify_citations(summary={}, registry=[], verdict="insufficient_evidence")
        assert report["status"] == "skip"

    def test_pass_when_all_cited_ids_in_registry(self):
        registry = [_src("doc1"), _src("doc2")]
        summary = {
            "supporting_document_ids": ["doc1"],
            "contradicting_document_ids": [],
            "verdict": "supported_event",
        }
        report = verify_citations(summary=summary, registry=registry, verdict="supported_event")
        assert report["status"] == "pass"
        assert report["blocking_reason"] is None

    def test_fails_missing_document_id(self):
        registry = [_src("doc1")]
        summary = {"supporting_document_ids": ["doc_GHOST"], "contradicting_document_ids": []}
        report = verify_citations(summary=summary, registry=registry, verdict="supported_event")
        assert report["status"] == "fail"
        assert "doc_GHOST" in report["missing_document_ids"]
        assert report["blocking_reason"] is not None

    def test_fails_unfetched_url_used_as_support(self):
        registry = [_src("doc1")]
        summary = {
            "supporting_document_ids": ["doc1"],
            "contradicting_document_ids": [],
            "source_urls": ["https://unregistered-site.com/article"],
        }
        report = verify_citations(summary=summary, registry=registry, verdict="supported_event")
        assert report["status"] == "fail"
        assert "https://unregistered-site.com/article" in report["unfetched_urls"]

    def test_supported_event_requires_registered_supporting_source(self):
        registry = [_src("doc1")]
        summary = {
            "supporting_document_ids": [],
            "contradicting_document_ids": [],
        }
        report = verify_citations(summary=summary, registry=registry, verdict="supported_event")
        assert report["status"] == "fail"
        assert "supported_event" in (report["blocking_reason"] or "")

    def test_source_mismatch_flagged_as_warn(self):
        src = _src("doc1")
        src.title = "EIA Official Report"
        registry = [src]
        summary = {
            "supporting_document_ids": ["doc1"],
            "contradicting_document_ids": [],
            "source_titles": {"doc1": "Different Title From LLM"},
        }
        report = verify_citations(summary=summary, registry=registry, verdict="supported_event")
        assert len(report["source_mismatches"]) == 1
        assert report["source_mismatches"][0]["flag"] == "llm_source_mismatch"

    def test_registered_url_not_flagged_as_unfetched(self):
        registry = [_src("doc1")]
        summary = {
            "supporting_document_ids": ["doc1"],
            "contradicting_document_ids": [],
            "source_urls": ["https://eia.gov/doc1"],
        }
        report = verify_citations(summary=summary, registry=registry, verdict="supported_event")
        assert "https://eia.gov/doc1" not in report["unfetched_urls"]
