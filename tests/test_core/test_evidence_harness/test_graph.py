"""Tests for Phase 6 — graph_builder, JsonGraphSink, PostgresGraphStore."""

import json
import os
from pathlib import Path

import pytest

from core.evidence_harness.schema import (
    HarnessRunResult, OutlierCasePackage, SourceRegistryRecord,
    ExtractedDocument, EvidenceClaim, SearchQuery, SearchResult, FetchResult,
)
from core.evidence_harness.graph_builder import build_graph, _build_timeline
from core.evidence_harness.graph_adapter import (
    JsonGraphSink, NullGraphSink, make_graph_sink, EvidenceGraphSink,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _source(doc_id="doc_abc", tier="tier1_official", domain="eia.gov") -> SourceRegistryRecord:
    return SourceRegistryRecord(
        document_id=doc_id, source_id=f"src_{doc_id}",
        url=f"https://{domain}/report",
        final_url=f"https://{domain}/report",
        domain=domain, source_tier=tier,
        fetched_at="2024-09-25T10:00:00Z",
        accessed_at="2024-09-25T10:00:00Z",
        content_hash="sha256:abc", extract_hash="sha256:def",
        provider="fixture", title="EIA Inventory Report",
        published_at="2024-09-25T09:00:00Z",
    )


def _document(doc_id="doc_abc") -> ExtractedDocument:
    return ExtractedDocument(
        document_id=doc_id, fetch_id="f1",
        canonical_url="https://eia.gov/report", domain="eia.gov",
        accessed_at="2024-09-25T10:00:00Z",
        extracted_text="EIA inventory report shows crude draw.",
        excerpt="EIA inventory report",
        content_hash="sha256:abc", extraction_version="plaintext.v1",
    )


def _claim(doc_id="doc_abc", support=0.85, contra=0.0) -> EvidenceClaim:
    return EvidenceClaim(
        claim_id="clm_test", case_id="case_wti",
        document_id=doc_id,
        claim_type="market_event",
        claim_text="EIA crude inventory draw supports WTI rally.",
        support_score=support,
        contradiction_score=contra,
        confidence="high",
        event_type="commodity_inventory",
    )


def _result(with_claims=True, with_sources=True) -> HarnessRunResult:
    srcs = [_source()] if with_sources else []
    docs = [_document()] if with_sources else []
    claims = [_claim()] if (with_claims and with_sources) else []
    return HarnessRunResult(
        case_id="case_wti",
        run_id="run_test",
        harness_run_id="hrn_abc123",
        status="supported_event",
        verdict="supported_event",
        confidence="high",
        queries=[SearchQuery(query_id="q1", case_id="case_wti", text="WTI price 2024-09-25")],
        search_results=[],
        fetched=[],
        documents=docs,
        sources=srcs,
        claims=claims,
        checks=[
            {"name": "source_quality", "status": "pass", "score": 0.9, "rationale": "tier1 source"},
            {"name": "temporal_consistency", "status": "pass", "score": 0.8, "rationale": "published before move"},
        ],
        limitations=[],
        artifact_paths={"verdict": "/tmp/verdict.json"},
        audit={
            "signal_type": "return_outlier",
            "as_of_date": "2024-09-25",
            "z_score": 7.4,
            "instrument": "CL1",
            "family": "futures",
            "severity": "high",
            "observed_value": 3.2,
            "local_context": {
                "_return_validation_status": "validated",
                "_return_outlier_reason": "large absolute move",
            },
        },
    )


# ── graph_builder ─────────────────────────────────────────────────────────────

class TestBuildGraph:
    def test_returns_required_top_level_keys(self):
        g = build_graph(_result())
        for key in ("case", "sources", "nodes", "edges", "checks", "queries", "timeline", "audit"):
            assert key in g, f"missing key: {key}"

    def test_case_has_required_fields(self):
        g = build_graph(_result())
        case = g["case"]
        assert case["case_id"] == "case_wti"
        assert case["run_id"] == "run_test"
        assert case["verdict"] == "supported_event"
        assert case["status"] == "unreviewed"

    def test_root_outlier_node_exists(self):
        g = build_graph(_result())
        root = next((n for n in g["nodes"] if n["node_type"] == "outlier"), None)
        assert root is not None
        assert root["node_id"] == "node_root_case_wti"

    def test_source_node_created_for_each_source(self):
        g = build_graph(_result())
        source_nodes = [n for n in g["nodes"] if n["source_id"] is not None]
        assert len(source_nodes) == 1

    def test_macro_release_node_type_for_eia(self):
        g = build_graph(_result())
        eia_node = next((n for n in g["nodes"] if "eia" in (n.get("payload") or {}).get("domain", "")), None)
        assert eia_node is not None
        assert eia_node["node_type"] == "macro_release"

    def test_supports_edge_created_for_high_support_claim(self):
        g = build_graph(_result())
        supports = [e for e in g["edges"] if e["relation"] == "supports"]
        assert len(supports) >= 1

    def test_no_edge_for_low_support_claim(self):
        r = _result()
        r.claims[0].support_score = 0.3
        r.claims[0].contradiction_score = 0.1
        g = build_graph(r)
        supports = [e for e in g["edges"] if e["relation"] == "supports"]
        assert len(supports) == 0

    def test_contradicts_edge_for_high_contradiction_claim(self):
        r = _result()
        r.claims[0].support_score = 0.2
        r.claims[0].contradiction_score = 0.8
        g = build_graph(r)
        contradicts = [e for e in g["edges"] if e["relation"] == "contradicts"]
        assert len(contradicts) >= 1

    def test_checks_list_populated(self):
        g = build_graph(_result())
        assert len(g["checks"]) == 2
        names = {c["name"] for c in g["checks"]}
        assert "source_quality" in names

    def test_queries_list_populated(self):
        g = build_graph(_result())
        assert len(g["queries"]) == 1
        assert g["queries"][0]["query"] == "WTI price 2024-09-25"

    def test_audit_has_schema_version(self):
        g = build_graph(_result())
        assert g["audit"]["schema_version"] == "evidence.case.v1"

    def test_timeline_starts_with_outlier_node(self):
        g = build_graph(_result())
        assert g["timeline"][0]["node_type"] == "outlier"

    def test_no_sources_produces_minimal_graph(self):
        g = build_graph(_result(with_sources=False))
        assert g["case"]["case_id"] == "case_wti"
        assert len([n for n in g["nodes"] if n["source_id"] is not None]) == 0
        assert not any(e["relation"] in ("supports", "contradicts") for e in g["edges"])

    def test_dominant_event_type_captured(self):
        g = build_graph(_result())
        assert g["case"]["event_type"] == "commodity_inventory"

    def test_local_context_node_exists_when_validation_status_present(self):
        g = build_graph(_result())
        node = next((n for n in g["nodes"] if n["node_type"] == "data_quality_finding"), None)
        assert node is not None
        assert node["payload"]["_return_validation_status"] == "validated"
        assert any(e["relation"] == "context_for" for e in g["edges"])

    def test_check_nodes_and_edges_explain_verdict_checks(self):
        g = build_graph(_result())
        check_nodes = [n for n in g["nodes"] if n["node_type"] == "check"]
        assert {n["payload"]["name"] for n in check_nodes} >= {
            "source_quality", "temporal_consistency"
        }
        assert any(e["relation"] == "checks" and e["check_name"] == "source_quality"
                   for e in g["edges"])

    def test_valid_llm_summary_node_created(self):
        r = _result()
        r.audit.update({
            "llm_summary": "Registered sources support the WTI move.",
            "llm_summary_valid": True,
            "citation_status": "pass",
            "llm_key_findings": ["EIA inventory draw"],
            "supporting_document_ids": ["doc_abc"],
            "contradicting_document_ids": [],
        })
        g = build_graph(r)
        llm = next((n for n in g["nodes"] if n["node_type"] == "llm_summary"), None)
        assert llm is not None
        assert llm["payload"]["citation_status"] == "pass"
        assert any(e["relation"] == "summarizes" for e in g["edges"])

    def test_invalid_llm_summary_does_not_create_support_node(self):
        r = _result()
        r.audit.update({
            "llm_summary": "Unsupported summary.",
            "llm_summary_valid": False,
            "citation_status": "fail",
        })
        g = build_graph(r)
        assert not any(n["node_type"] == "llm_summary" for n in g["nodes"])

    def test_review_event_appears_as_human_decision_node(self):
        r = _result()
        r.audit["review_events"] = [{
            "actor": "analyst@example.com",
            "action": "mark_supported_event",
            "reason": "Source chain is credible.",
            "created_at": "2024-09-26T10:00:00Z",
        }]
        g = build_graph(r)
        human = next((n for n in g["nodes"] if n["node_type"] == "human_decision"), None)
        assert human is not None
        assert human["payload"]["actor"] == "analyst@example.com"
        assert any(e["relation"] == "reviewed_by" for e in g["edges"])

    def test_timeline_order_follows_chain_contract(self):
        r = _result()
        r.audit.update({
            "llm_summary": "Registered sources support the WTI move.",
            "llm_summary_valid": True,
            "citation_status": "pass",
            "review_events": [{
                "actor": "analyst",
                "action": "mark_supported_event",
                "created_at": "2024-09-26T10:00:00Z",
            }],
        })
        g = build_graph(r)
        order = [n["node_type"] for n in g["timeline"]]
        assert order.index("outlier") < order.index("data_quality_finding")
        assert order.index("data_quality_finding") < order.index("macro_release")
        assert order.index("macro_release") < order.index("check")
        assert order.index("check") < order.index("llm_summary")
        assert order.index("llm_summary") < order.index("human_decision")


class TestBuildTimeline:
    def test_outlier_node_sorts_first(self):
        nodes = [
            {"node_id": "n1", "node_type": "news_article", "source_tier": "tier2_reputable",
             "published_at": "2024-09-24", "observed_at": None, "effective_at": None, "confidence": 0.6},
            {"node_id": "n0", "node_type": "outlier", "source_tier": None,
             "published_at": None, "observed_at": "2024-09-25", "effective_at": None, "confidence": None},
        ]
        timeline = _build_timeline(nodes)
        assert timeline[0]["node_type"] == "outlier"

    def test_tier1_source_sorts_before_tier2(self):
        nodes = [
            {"node_id": "n2", "node_type": "news_article", "source_tier": "tier2_reputable",
             "published_at": "2024-09-25", "observed_at": None, "effective_at": None, "confidence": 0.65},
            {"node_id": "n1", "node_type": "macro_release", "source_tier": "tier1_official",
             "published_at": "2024-09-25", "observed_at": None, "effective_at": None, "confidence": 0.9},
        ]
        timeline = _build_timeline(nodes)
        assert timeline[0]["source_tier"] == "tier1_official"

    def test_order_is_stable_for_equal_timestamps(self):
        nodes = [
            {"node_id": "b", "node_type": "news_article", "source_tier": "tier2_reputable",
             "published_at": "2024-09-25", "observed_at": None, "effective_at": None, "confidence": 0.6},
            {"node_id": "a", "node_type": "news_article", "source_tier": "tier2_reputable",
             "published_at": "2024-09-25", "observed_at": None, "effective_at": None, "confidence": 0.6},
        ]
        t1 = _build_timeline(nodes)
        t2 = _build_timeline(list(reversed(nodes)))
        assert [n["node_id"] for n in t1] == [n["node_id"] for n in t2]


# ── JsonGraphSink ─────────────────────────────────────────────────────────────

class TestJsonGraphSink:
    def test_writes_json_file(self, tmp_path):
        sink = JsonGraphSink(graph_dir=str(tmp_path))
        sink.write(_result())
        out = tmp_path / "run_test" / "case_wti.json"
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["case"]["case_id"] == "case_wti"

    def test_json_has_nodes_and_edges(self, tmp_path):
        sink = JsonGraphSink(graph_dir=str(tmp_path))
        sink.write(_result())
        data = json.loads((tmp_path / "run_test" / "case_wti.json").read_text())
        assert "nodes" in data
        assert "edges" in data

    def test_json_is_valid_evidence_case_v1(self, tmp_path):
        sink = JsonGraphSink(graph_dir=str(tmp_path))
        sink.write(_result())
        data = json.loads((tmp_path / "run_test" / "case_wti.json").read_text())
        assert data["audit"]["schema_version"] == "evidence.case.v1"

    def test_last_path_returned(self, tmp_path):
        sink = JsonGraphSink(graph_dir=str(tmp_path))
        sink.write(_result())
        assert sink.last_path() is not None
        assert "case_wti.json" in sink.last_path()

    def test_overwrites_on_second_write(self, tmp_path):
        sink = JsonGraphSink(graph_dir=str(tmp_path))
        sink.write(_result())
        r2 = _result()
        r2.verdict = "unsupported"
        sink.write(r2)
        data = json.loads((tmp_path / "run_test" / "case_wti.json").read_text())
        assert data["case"]["verdict"] == "unsupported"

    def test_sink_satisfies_protocol(self, tmp_path):
        sink = JsonGraphSink(graph_dir=str(tmp_path))
        assert isinstance(sink, EvidenceGraphSink)

    def test_case_snapshot_contains_nodes_edges_checks_and_audit(self, tmp_path):
        sink = JsonGraphSink(graph_dir=str(tmp_path))
        sink.write(_result())
        data = json.loads((tmp_path / "run_test" / "case_wti.json").read_text())
        assert len(data["nodes"]) >= 1
        assert "edges" in data
        assert "checks" in data
        assert "audit" in data


class TestNullGraphSink:
    def test_write_does_nothing(self, tmp_path):
        sink = NullGraphSink()
        sink.write(_result())  # must not raise

    def test_satisfies_protocol(self):
        assert isinstance(NullGraphSink(), EvidenceGraphSink)


class TestMakeGraphSink:
    def test_null_backend(self):
        sink = make_graph_sink("null")
        assert isinstance(sink, NullGraphSink)

    def test_json_backend(self, tmp_path):
        sink = make_graph_sink("json", graph_dir=str(tmp_path))
        assert isinstance(sink, JsonGraphSink)

    def test_default_is_null(self):
        sink = make_graph_sink()
        assert isinstance(sink, NullGraphSink)


# ── PostgresGraphStore — integration (skipped if no DB) ──────────────────────

_PG_DSN = os.environ.get("JANUS_EVIDENCE_DATABASE_URL")
_SKIP_PG = pytest.mark.skipif(
    not _PG_DSN,
    reason="JANUS_EVIDENCE_DATABASE_URL not set — skipping PostgreSQL integration tests",
)


@_SKIP_PG
class TestPostgresGraphStore:
    @pytest.fixture(autouse=True)
    def _apply_migrations(self):
        from core.evidence_harness.graph_store import run_migrations
        run_migrations(_PG_DSN)

    @pytest.fixture
    def store(self):
        from core.evidence_harness.graph_store import PostgresGraphStore
        with PostgresGraphStore(dsn=_PG_DSN) as s:
            yield s

    @pytest.fixture(autouse=True)
    def _cleanup(self, store):
        yield
        # Remove test rows after each test
        with store._conn.cursor() as cur:
            cur.execute("delete from evidence_cases where case_id = 'case_pg_test'")
        store._conn.commit()

    def _pg_result(self) -> HarnessRunResult:
        r = _result()
        r.case_id = "case_pg_test"
        r.claims[0].case_id = "case_pg_test"
        return r

    def test_upsert_case_and_load(self, store):
        from core.evidence_harness.graph_builder import build_graph
        g = build_graph(self._pg_result())
        store.upsert_case(g["case"])
        cases = store.list_cases({"run_id": "run_test"})
        ids = [c["case_id"] for c in cases]
        assert "case_pg_test" in ids

    def test_create_case_node_edge_and_load_graph(self, store):
        from core.evidence_harness.graph_builder import build_graph
        g = build_graph(self._pg_result())
        store.upsert_case(g["case"])
        for src in g["sources"]:
            store.upsert_source(src)
        for node in g["nodes"]:
            store.add_node(node)
        for edge in g["edges"]:
            store.add_edge(edge)
        for chk in g["checks"]:
            store.add_check(chk)

        loaded = store.load_case_graph("case_pg_test")
        assert loaded["case"]["case_id"] == "case_pg_test"
        assert len(loaded["nodes"]) >= 1
        assert "edges" in loaded

    def test_source_dedup_by_url_and_content_hash(self, store):
        from core.evidence_harness.graph_builder import build_graph
        g = build_graph(self._pg_result())
        store.upsert_case(g["case"])
        for src in g["sources"]:
            store.upsert_source(src)
            store.upsert_source(src)  # second upsert must not raise
        loaded = store.load_case_graph("case_pg_test")
        # Only one source_id
        assert len({s["source_id"] for s in loaded["sources"]}) == len(loaded["sources"])

    def test_case_event_log_is_append_only(self, store):
        from core.evidence_harness.graph_builder import build_graph
        g = build_graph(self._pg_result())
        store.upsert_case(g["case"])
        store.append_event("case_pg_test", "harness", "run_completed", {"verdict": "supported_event"})
        store.append_event("case_pg_test", "analyst", "mark_supported_event", {})
        with store._conn.cursor() as cur:
            cur.execute(
                "select count(*) from evidence_case_events where case_id = %s",
                ("case_pg_test",),
            )
            count = cur.fetchone()[0]
        assert count == 2

    def test_update_case_status(self, store):
        from core.evidence_harness.graph_builder import build_graph
        g = build_graph(self._pg_result())
        store.upsert_case(g["case"])
        store.update_case_status("case_pg_test", "investigating")
        cases = store.list_cases({"status": "investigating"})
        assert any(c["case_id"] == "case_pg_test" for c in cases)


# ── Controller integration with graph sink ────────────────────────────────────

class TestControllerWithGraphSink:
    def test_json_sink_wired_end_to_end(self, tmp_path):
        from core.evidence_harness.controller import run_harness
        from core.evidence_harness.config import load_harness_config
        from core.evidence_harness.fetch import FixtureFetchProvider
        from core.evidence_harness.search import FixtureSearchProvider

        FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "evidence_harness"
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path / "artifacts")
        cfg.cache_dir = str(tmp_path / "cache")
        cfg.graph_backend = "json"
        cfg.graph_dir = str(tmp_path / "graph")

        import json as _json
        case_data = _json.loads((FIXTURES / "cases" / "wti_inventory_supported.json").read_text())
        from core.evidence_harness.schema import OutlierCasePackage
        case = OutlierCasePackage.from_dict(case_data)

        from core.evidence_harness.graph_adapter import JsonGraphSink
        sink = JsonGraphSink(graph_dir=str(tmp_path / "graph"))
        result = run_harness(
            case, cfg,
            search_provider=FixtureSearchProvider(fixture_dir=str(FIXTURES / "search")),
            fetch_provider=FixtureFetchProvider(fixture_dir=str(FIXTURES / "pages")),
        )
        sink.write(result)

        graph_file = tmp_path / "graph" / case.run_id / f"{case.case_id}.json"
        assert graph_file.exists()
        data = _json.loads(graph_file.read_text())
        assert data["case"]["verdict"] == result.verdict
