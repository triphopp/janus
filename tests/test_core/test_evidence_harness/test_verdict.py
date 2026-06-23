"""Tests for verdict reducer — precedence and support rules."""

import pytest
from core.evidence_harness.verdict import reduce_verdict
from core.evidence_harness.schema import SourceRegistryRecord, EvidenceClaim
from core.evidence_harness.ids import stable_id


def _src(doc_id: str, tier: str, published_at: str = "2024-01-25T12:00:00Z") -> SourceRegistryRecord:
    return SourceRegistryRecord(
        document_id=doc_id, source_id=f"src_{doc_id}", url=f"https://example.com/{doc_id}",
        final_url=f"https://example.com/{doc_id}", domain="example.com",
        source_tier=tier, fetched_at="2024-01-25T00:00:00Z",
        accessed_at="2024-01-25T00:00:00Z", content_hash="sha256:abc",
        extract_hash="sha256:def", provider="fixture",
        published_at=published_at,
    )


def _claim(doc_id: str, support: float, contradiction: float, conf: str = "medium") -> EvidenceClaim:
    return EvidenceClaim(
        claim_id=stable_id("clm", {"d": doc_id}),
        case_id="case_test", document_id=doc_id,
        claim_type="market_context", claim_text="test",
        support_score=support, contradiction_score=contradiction,
        confidence=conf,
    )


def _pass(name: str) -> dict:
    return {"name": name, "status": "pass", "score": 1.0, "rationale": "ok"}


def _fail(name: str) -> dict:
    return {"name": name, "status": "fail", "score": 0.0, "rationale": "failed"}


def _warn(name: str) -> dict:
    return {"name": name, "status": "warn", "score": 0.5, "rationale": "warn"}


class TestVerdictReducer:
    def test_failed_when_query_budget_fails(self):
        checks = [_fail("query_budget"), _pass("source_quality")]
        verdict, _ = reduce_verdict(checks, [], [], 0)
        assert verdict == "failed"

    def test_failed_when_fetch_budget_fails(self):
        checks = [_pass("query_budget"), _fail("fetch_budget")]
        verdict, _ = reduce_verdict(checks, [], [], 3)
        assert verdict == "failed"

    def test_conflict_precedes_supported_event(self):
        tier2_src1 = _src("doc1", "tier2_reputable")
        tier2_src2 = _src("doc2", "tier2_reputable")
        supporting_claim = _claim("doc1", 0.8, 0.1, "medium")
        contradicting_claim = _claim("doc2", 0.2, 0.8, "medium")

        checks = [
            _pass("query_budget"), _pass("fetch_budget"), _pass("source_policy"),
            _pass("source_quality"), _pass("temporal_consistency"),
            _pass("instrument_relevance"), _pass("event_relevance"),
            _warn("cross_source_consistency"), _warn("contradiction_detection"),
            _pass("policy_consistency"),
        ]
        verdict, _ = reduce_verdict(
            checks, [tier2_src1, tier2_src2],
            [supporting_claim, contradicting_claim], 5
        )
        assert verdict == "conflicting_evidence"

    def test_supported_event_requires_tier1_or_two_tier2(self):
        tier1_src = _src("doc1", "tier1_official")
        claim = _claim("doc1", 0.9, 0.0, "high")

        all_pass = [
            _pass("query_budget"), _pass("fetch_budget"), _pass("source_policy"),
            _pass("source_quality"), _pass("temporal_consistency"),
            _pass("instrument_relevance"), _pass("event_relevance"),
            _pass("cross_source_consistency"), _pass("contradiction_detection"),
            _pass("policy_consistency"),
        ]
        verdict, confidence = reduce_verdict(all_pass, [tier1_src], [claim], 5)
        assert verdict == "supported_event"
        assert confidence == "high"

    def test_supported_event_with_two_tier2_sources(self):
        src1 = _src("doc1", "tier2_reputable")
        src2 = _src("doc2", "tier2_reputable")

        all_pass = [
            _pass("query_budget"), _pass("fetch_budget"), _pass("source_policy"),
            _pass("source_quality"), _pass("temporal_consistency"),
            _pass("instrument_relevance"), _pass("event_relevance"),
            _pass("cross_source_consistency"), _pass("contradiction_detection"),
            _pass("policy_consistency"),
        ]
        verdict, confidence = reduce_verdict(all_pass, [src1, src2], [], 5)
        assert verdict == "supported_event"
        assert confidence == "medium"

    def test_supported_event_not_returned_with_only_one_tier2(self):
        src = _src("doc1", "tier2_reputable")
        all_pass = [
            _pass("query_budget"), _pass("fetch_budget"), _pass("source_policy"),
            _pass("source_quality"), _pass("temporal_consistency"),
            _pass("instrument_relevance"), _pass("event_relevance"),
            _pass("cross_source_consistency"), _pass("contradiction_detection"),
            _pass("policy_consistency"),
        ]
        verdict, _ = reduce_verdict(all_pass, [src], [], 5)
        assert verdict != "supported_event"

    def test_unsupported_requires_minimum_search_coverage(self):
        checks = [
            _pass("query_budget"), _pass("fetch_budget"), _pass("source_policy"),
            _fail("source_quality"),
        ]
        verdict, _ = reduce_verdict(checks, [], [], 3)
        assert verdict == "unsupported"

    def test_insufficient_evidence_when_no_coverage(self):
        checks = [
            _pass("query_budget"), _pass("fetch_budget"), _pass("source_policy"),
            _fail("source_quality"),
        ]
        verdict, _ = reduce_verdict(checks, [], [], 0)
        assert verdict == "insufficient_evidence"

    def test_suspected_data_issue_from_claims(self):
        src = _src("doc1", "tier2_reputable")
        claim = EvidenceClaim(
            claim_id="clm_1", case_id="c", document_id="doc1",
            claim_type="market_context", claim_text="bad tick detected",
            support_score=0.8, contradiction_score=0.0,
            confidence="medium", event_type="bad_tick",
        )
        checks = [
            _pass("query_budget"), _pass("fetch_budget"), _pass("source_policy"),
            _pass("source_quality"),
        ]
        verdict, _ = reduce_verdict(checks, [src], [claim], 3)
        assert verdict == "suspected_data_issue"
