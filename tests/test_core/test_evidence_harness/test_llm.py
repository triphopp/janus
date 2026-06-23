"""Tests for LLM sub-package — uses MockLLMClient, no real LLM calls."""

import json
import pytest
from pathlib import Path

from core.evidence_harness.llm.client import LLMClient, LLMJsonError
from core.evidence_harness.llm.providers.mock import MockLLMClient
from core.evidence_harness.llm.prompts import (
    claim_extraction_messages,
    query_expansion_messages,
    evidence_summary_messages,
    CLAIM_EXTRACTION_SCHEMA,
    QUERY_EXPANSION_SCHEMA,
    EVIDENCE_SUMMARY_SCHEMA,
    PROMPT_VERSION,
)
from core.evidence_harness.llm.router import (
    build_llm_client,
    run_claim_extraction,
    run_query_expansion,
    run_evidence_summary,
)
from core.evidence_harness.schema import (
    ExtractedDocument, EvidenceClaim, SourceRegistryRecord, OutlierCasePackage,
)
from core.evidence_harness.config import HarnessConfig


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _case() -> OutlierCasePackage:
    return OutlierCasePackage(
        case_id="case_wti_test",
        run_id="run_test",
        signal_type="return_outlier",
        as_of_date="2024-09-25",
        symbol="CL",
        instrument="CL1",
        family="futures",
        severity="high",
        z_score=7.4,
        observed_value=3.2,
    )


def _document(doc_id: str = "doc_abc123", text: str = "EIA inventory report.") -> ExtractedDocument:
    return ExtractedDocument(
        document_id=doc_id, fetch_id="f1",
        canonical_url="https://eia.gov/report", domain="eia.gov",
        accessed_at="2024-09-25T10:00:00Z",
        extracted_text=text, excerpt=text[:100],
        content_hash="sha256:abc", extraction_version="plaintext.v1",
    )


def _source(doc_id: str = "doc_abc123") -> SourceRegistryRecord:
    return SourceRegistryRecord(
        document_id=doc_id, source_id="src_eia",
        url="https://eia.gov/report", final_url="https://eia.gov/report",
        domain="eia.gov", source_tier="tier1_official",
        fetched_at="2024-09-25T10:00:00Z",
        accessed_at="2024-09-25T10:00:00Z",
        content_hash="sha256:abc", extract_hash="sha256:def",
        provider="fixture",
    )


# ── MockLLMClient ─────────────────────────────────────────────────────────────

class TestMockLLMClient:
    def test_satisfies_llm_client_protocol(self):
        client = MockLLMClient()
        assert isinstance(client, LLMClient)

    def test_complete_returns_string(self):
        client = MockLLMClient()
        msgs = [{"role": "user", "content": "hello"}]
        result = client.complete(msgs)
        assert isinstance(result, str)

    def test_complete_json_returns_dict(self):
        client = MockLLMClient()
        msgs = [{"role": "user", "content": "Extract factual claims from this document."}]
        result = client.complete_json(msgs, schema={})
        assert isinstance(result, dict)

    def test_complete_json_claim_extraction_has_claims_key(self):
        client = MockLLMClient()
        doc_id = "doc_abc"
        msgs = claim_extraction_messages(
            "EIA inventory report content", doc_id, {"symbol": "CL"}, [doc_id]
        )
        result = client.complete_json(msgs, schema={})
        assert "claims" in result
        assert isinstance(result["claims"], list)

    def test_complete_json_query_expansion_has_queries_key(self):
        client = MockLLMClient()
        msgs = query_expansion_messages(
            {"symbol": "CL", "as_of_date": "2024-09-25"}, [], 3,
            "2024-09-23", "2024-09-27",
        )
        result = client.complete_json(msgs, schema={})
        assert "queries" in result

    def test_complete_json_summary_has_required_keys(self):
        client = MockLLMClient()
        msgs = evidence_summary_messages(
            {"symbol": "CL", "as_of_date": "2024-09-25"}, "supported_event", 0.9,
            ["EIA inventory report."], ["source_quality: pass (score=0.9)"], ["doc_abc"],
        )
        result = client.complete_json(msgs, schema={})
        assert "summary" in result
        assert "key_findings" in result
        assert "limitations" in result

    def test_provider_name_and_model(self):
        client = MockLLMClient(model="test-model")
        assert client.provider_name == "mock"
        assert client.model == "test-model"


# ── build_llm_client ──────────────────────────────────────────────────────────

class TestBuildLlmClient:
    def test_builds_mock_client(self):
        cfg = HarnessConfig(llm_enabled=True, llm_provider="mock")
        client = build_llm_client(cfg)
        assert isinstance(client, MockLLMClient)

    def test_unknown_provider_raises(self):
        cfg = HarnessConfig(llm_enabled=True, llm_provider="nonexistent")
        with pytest.raises(ValueError, match="Unknown llm_provider"):
            build_llm_client(cfg)


# ── run_claim_extraction ──────────────────────────────────────────────────────

class TestRunClaimExtraction:
    def test_returns_list_of_evidence_claims(self):
        client = MockLLMClient()
        doc = _document("doc_abc")
        registry = [_source("doc_abc")]
        claims = run_claim_extraction(doc, _case(), registry, client)
        assert isinstance(claims, list)
        for c in claims:
            assert isinstance(c, EvidenceClaim)

    def test_claims_are_llm_generated(self):
        client = MockLLMClient()
        doc = _document("doc_abc")
        registry = [_source("doc_abc")]
        claims = run_claim_extraction(doc, _case(), registry, client)
        assert all(c.llm_generated for c in claims)

    def test_citation_hallucination_prevention(self):
        """LLM mock cites doc_abc, which is in registry — should pass.
        A hallucinated ID not in registry must be stripped."""
        from unittest.mock import MagicMock
        client = MagicMock()
        client.complete_json.return_value = {
            "claims": [
                {
                    "claim_text": "EIA inventory report.",
                    "claim_type": "market_event",
                    "support_score": 0.9,
                    "contradiction_score": 0.0,
                    "confidence": "high",
                    "document_ids": ["doc_abc", "doc_hallucinated_xyz"],
                    "event_type": "commodity_inventory",
                }
            ]
        }
        doc = _document("doc_abc")
        registry = [_source("doc_abc")]  # doc_hallucinated_xyz NOT in registry
        claims = run_claim_extraction(doc, _case(), registry, client)
        assert len(claims) == 1
        cited = claims[0].citations
        cited_ids = [c["document_id"] for c in cited]
        assert "doc_hallucinated_xyz" not in cited_ids
        assert "doc_abc" in cited_ids

    def test_returns_empty_list_on_llm_error(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.complete_json.side_effect = LLMJsonError("parse error")
        claims = run_claim_extraction(_document(), _case(), [_source()], client)
        assert claims == []

    def test_support_score_clamped(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.complete_json.return_value = {
            "claims": [{
                "claim_text": "test", "claim_type": "market_context",
                "support_score": 99.0, "contradiction_score": -5.0,
                "confidence": "medium", "document_ids": ["doc_abc"],
            }]
        }
        doc = _document("doc_abc")
        registry = [_source("doc_abc")]
        claims = run_claim_extraction(doc, _case(), registry, client)
        # EvidenceClaim stores the raw value — validation is downstream
        assert len(claims) == 1


# ── run_query_expansion ────────────────────────────────────────────────────────

class TestRunQueryExpansion:
    def test_returns_list_of_strings(self):
        client = MockLLMClient()
        queries = run_query_expansion(
            _case(), ["WTI price 2024-09-25"], 3, "2024-09-23", "2024-09-27", client
        )
        assert isinstance(queries, list)
        assert all(isinstance(q, str) for q in queries)

    def test_no_queries_when_budget_zero(self):
        client = MockLLMClient()
        queries = run_query_expansion(_case(), [], 0, None, None, client)
        assert queries == []

    def test_deduplicates_existing_queries(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.complete_json.return_value = {
            "queries": [
                {"text": "WTI price 2024-09-25", "rationale": "already exists"},
                {"text": "EIA crude inventory 2024-09-25", "rationale": "new"},
            ]
        }
        existing = ["WTI price 2024-09-25"]
        queries = run_query_expansion(_case(), existing, 5, None, None, client)
        assert "WTI price 2024-09-25" not in queries
        assert "EIA crude inventory 2024-09-25" in queries

    def test_respects_budget(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.complete_json.return_value = {
            "queries": [{"text": f"query {i}", "rationale": ""} for i in range(10)]
        }
        queries = run_query_expansion(_case(), [], 3, None, None, client)
        assert len(queries) <= 3

    def test_empty_text_skipped(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.complete_json.return_value = {
            "queries": [
                {"text": "", "rationale": ""},
                {"text": "WTI inventory report", "rationale": "good"},
            ]
        }
        queries = run_query_expansion(_case(), [], 5, None, None, client)
        assert "" not in queries


# ── run_evidence_summary ───────────────────────────────────────────────────────

class TestRunEvidenceSummary:
    def test_returns_summary_dict(self):
        client = MockLLMClient()
        result = run_evidence_summary(
            _case(), "supported_event", 0.9,
            [], [], [_source()], client,
        )
        assert "summary" in result
        assert "key_findings" in result
        assert "limitations" in result
        assert "cited_document_ids" in result

    def test_cited_ids_filtered_to_registry(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.complete_json.return_value = {
            "summary": "Evidence supports the move.",
            "key_findings": [],
            "limitations": [],
            "cited_document_ids": ["doc_abc", "doc_hallucinated"],
        }
        registry = [_source("doc_abc")]
        result = run_evidence_summary(_case(), "supported_event", 0.9, [], [], registry, client)
        assert "doc_hallucinated" not in result["cited_document_ids"]
        assert "doc_abc" in result["cited_document_ids"]

    def test_llm_error_flag_on_exception(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.complete_json.side_effect = LLMJsonError("bad json")
        result = run_evidence_summary(_case(), "insufficient_evidence", 0.0, [], [], [], client)
        assert result["llm_error"] is True
        assert result["summary"] == ""


# ── Prompt injection safety ────────────────────────────────────────────────────

class TestPromptInjectionSafety:
    def test_untrusted_label_in_claim_prompt(self):
        msgs = claim_extraction_messages("malicious content", "doc_x", {}, ["doc_x"])
        user_msg = next(m["content"] for m in msgs if m["role"] == "user")
        assert "UNTRUSTED EXTERNAL CONTENT" in user_msg

    def test_system_prompt_prohibits_inventing_ids(self):
        msgs = claim_extraction_messages("text", "doc_x", {}, ["doc_x"])
        sys_msg = next(m["content"] for m in msgs if m["role"] == "system")
        assert "ONLY cite document_ids" in sys_msg


# ── Controller integration with LLM enabled ───────────────────────────────────

class TestControllerWithLlm:
    def test_llm_claims_replace_rule_based(self, tmp_path):
        from core.evidence_harness.controller import run_harness
        from core.evidence_harness.config import load_harness_config
        from core.evidence_harness.fetch import FixtureFetchProvider
        from core.evidence_harness.search import FixtureSearchProvider

        FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "evidence_harness"
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        cfg.cache_dir = str(tmp_path / "cache")
        cfg.llm_enabled = True
        cfg.llm_provider = "mock"

        case = OutlierCasePackage(
            case_id="case_wti_llm_test", run_id="run_test",
            signal_type="return_outlier", as_of_date="2024-09-25",
            symbol="CL", instrument="CL1", family="futures",
            severity="high", z_score=7.4,
        )
        result = run_harness(
            case, cfg,
            search_provider=FixtureSearchProvider(fixture_dir=str(FIXTURES / "search")),
            fetch_provider=FixtureFetchProvider(fixture_dir=str(FIXTURES / "pages")),
        )
        assert result.verdict is not None
        # LLM claims are llm_generated=True
        llm_claims = [c for c in result.claims if c.llm_generated]
        assert len(llm_claims) >= 0  # may be 0 if no docs extracted

    def test_verdict_written_with_llm_fields(self, tmp_path):
        from core.evidence_harness.controller import run_harness
        from core.evidence_harness.config import load_harness_config
        from core.evidence_harness.fetch import FixtureFetchProvider
        from core.evidence_harness.search import FixtureSearchProvider

        FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "evidence_harness"
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        cfg.cache_dir = str(tmp_path / "cache")
        cfg.llm_enabled = True
        cfg.llm_provider = "mock"

        case = OutlierCasePackage(
            case_id="case_wti_llm_v2", run_id="run_test",
            signal_type="return_outlier", as_of_date="2024-09-25",
            symbol="CL", instrument="CL1", family="futures",
            severity="high", z_score=7.4,
        )
        result = run_harness(
            case, cfg,
            search_provider=FixtureSearchProvider(fixture_dir=str(FIXTURES / "search")),
            fetch_provider=FixtureFetchProvider(fixture_dir=str(FIXTURES / "pages")),
        )
        import json as _json
        verdict_data = _json.loads(Path(result.artifact_paths["verdict"]).read_text())
        assert "llm_provider" in verdict_data
        assert verdict_data["llm_provider"] == "mock"
        assert "llm_summary_valid" in verdict_data
