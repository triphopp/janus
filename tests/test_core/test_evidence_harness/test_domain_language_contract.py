"""Domain-language guardrails for Evidence Graph identity boundaries."""

from pathlib import Path

from core.evidence_harness.graph_builder import build_graph
from core.evidence_harness.schema import (
    EvidenceClaim,
    ExtractedDocument,
    HarnessRunResult,
    SearchQuery,
    SourceRegistryRecord,
)


ROOT = Path(__file__).resolve().parents[3]


def _source() -> SourceRegistryRecord:
    return SourceRegistryRecord(
        document_id="doc_domain",
        source_id="src_domain_revision",
        url="https://eia.gov/report",
        final_url="https://eia.gov/report",
        domain="eia.gov",
        source_tier="tier1_official",
        fetched_at="2024-09-25T10:00:00Z",
        accessed_at="2024-09-25T10:00:00Z",
        content_hash="sha256:content",
        extract_hash="sha256:extract",
        provider="fixture",
        title="EIA Report",
        published_at="2024-09-25T09:00:00Z",
    )


def _result() -> HarnessRunResult:
    return HarnessRunResult(
        case_id="case_domain",
        run_id="pipeline_run_001",
        harness_run_id="investigation_run_001",
        status="supported_event",
        verdict="supported_event",
        confidence="high",
        queries=[SearchQuery(query_id="query_domain", case_id="case_domain", text="WTI price")],
        documents=[ExtractedDocument(
            document_id="doc_domain",
            fetch_id="fetch_domain",
            canonical_url="https://eia.gov/report",
            domain="eia.gov",
            accessed_at="2024-09-25T10:00:00Z",
            extracted_text="EIA report text.",
            excerpt="EIA report text.",
            content_hash="sha256:content",
            extraction_version="plaintext.v1",
        )],
        sources=[_source()],
        claims=[EvidenceClaim(
            claim_id="claim_domain",
            case_id="case_domain",
            document_id="doc_domain",
            claim_type="market_event",
            claim_text="EIA report supports the move.",
            support_score=0.9,
            event_type="commodity_inventory",
        )],
        checks=[],
        audit={
            "signal_type": "return_outlier",
            "as_of_date": "2024-09-25",
            "local_context": {"identity_key": "WTI-2024-09-25"},
        },
    )


def test_sql_source_revision_uniqueness_is_not_url_only():
    sql = (ROOT / "db" / "migrations" / "evidence_graph" / "002_evidence_sources.sql").read_text()
    compact = " ".join(sql.split())

    assert "drop index if exists evidence_sources_url_idx" in compact
    assert "evidence_sources_url_content_idx" in compact
    assert "on evidence_sources(canonical_url, content_hash)" in compact
    assert "create index if not exists evidence_sources_url_idx on evidence_sources(url)" in compact


def test_graph_payload_distinguishes_pipeline_case_investigation_source_document():
    graph = build_graph(_result())

    assert graph["case"]["run_id"] == "pipeline_run_001"
    assert graph["case"]["case_id"] == "case_domain"
    assert graph["case"]["payload"]["harness_run_id"] == "investigation_run_001"

    source = graph["sources"][0]
    assert source["source_id"] == "src_domain_revision"
    assert source["payload"]["document_id"] == "doc_domain"
    assert source["content_hash"] == "sha256:content"
    assert source["payload"]["extract_hash"] == "sha256:extract"


def test_graph_uses_domain_language_for_relationships():
    graph = build_graph(_result())
    relations = {edge["relation"] for edge in graph["edges"]}
    node_types = {node["node_type"] for node in graph["nodes"]}

    assert "supports" in relations
    assert "context_for" in relations
    assert "local_context" in node_types
