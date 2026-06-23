"""Contract tests for the Evidence API run-scoped routes.

These tests verify:
- GET routes never trigger harness execution
- Run-scoped routes are isolated per run_id
- Source cards come from sources.jsonl registry, not LLM output
- Citation status is included in status response
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from web.dashboard import app
from web.evidence_api import _reset_store


@pytest.fixture(autouse=True)
def reset_store():
    _reset_store()
    yield
    _reset_store()


@pytest.fixture
def client():
    return TestClient(app)


def _write_verdict(run_dir: Path, case_id: str, run_id: str,
                   verdict: str = "supported_event",
                   citation_status: str = "pass") -> Path:
    """Write a minimal verdict.json and sources.jsonl to tmp_path."""
    harness_dir = run_dir / run_id / case_id / "hrn_000test"
    harness_dir.mkdir(parents=True)
    verdict_doc = {
        "schema_version": "evidence.verdict.v1",
        "run_id": run_id, "case_id": case_id,
        "harness_run_id": "hrn_000test",
        "verdict": verdict, "confidence": "high",
        "citation_status": citation_status,
        "llm_summary_valid": citation_status == "pass",
        "llm_summary": "Test summary.",
        "limitations": [],
        "started_at": "2024-01-25T10:00:00+00:00",
        "finished_at": "2024-01-25T10:01:00+00:00",
    }
    (harness_dir / "verdict.json").write_text(json.dumps(verdict_doc))

    sources = [
        {"document_id": "doc_aaa", "source_id": "src_aaa",
         "url": "https://sec.gov/test", "final_url": "https://sec.gov/test",
         "domain": "sec.gov", "source_tier": "tier1_official",
         "title": "SEC Filing", "fetched_at": "2024-01-25T10:00:00+00:00",
         "accessed_at": "2024-01-25T10:01:00+00:00",
         "content_hash": "sha256:abc", "extract_hash": "sha256:def",
         "provider": "fixture", "query_ids": [], "cache_paths": {}},
    ]
    (harness_dir / "sources.jsonl").write_text(
        "\n".join(json.dumps(s) for s in sources)
    )
    checks = [
        {"name": "source_quality", "status": "pass", "score": 0.9},
    ]
    (harness_dir / "checks.jsonl").write_text(
        "\n".join(json.dumps(c) for c in checks)
    )
    (harness_dir / "query_log.jsonl").write_text(
        json.dumps({"query_id": "q_001", "text": "TSLA stock move 2024-01-25"})
    )
    (harness_dir / "case_package.json").write_text(json.dumps({
        "case_id": case_id, "run_id": run_id, "signal_type": "return_outlier",
        "as_of_date": "2024-01-25", "family": "equity", "symbol": "TSLA",
        "metric_name": "return_std",
    }))
    return harness_dir


class TestRunScopedRoutes:
    def test_get_run_summary_returns_cases(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("JANUS_EVIDENCE_ARTIFACT_DIR", str(tmp_path))
        _write_verdict(tmp_path, "case_abc", "run_r1")
        _write_verdict(tmp_path, "case_def", "run_r1")
        resp = client.get("/api/evidence/runs/run_r1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "run_r1"
        assert data["count"] == 2

    def test_get_run_summary_404_for_unknown_run(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("JANUS_EVIDENCE_ARTIFACT_DIR", str(tmp_path))
        resp = client.get("/api/evidence/runs/no_such_run")
        assert resp.status_code == 404

    def test_case_detail_is_run_scoped(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("JANUS_EVIDENCE_ARTIFACT_DIR", str(tmp_path))
        _write_verdict(tmp_path, "case_xyz", "run_r1", verdict="supported_event")
        _write_verdict(tmp_path, "case_xyz", "run_r2", verdict="insufficient_evidence")

        resp1 = client.get("/api/evidence/runs/run_r1/cases/case_xyz")
        resp2 = client.get("/api/evidence/runs/run_r2/cases/case_xyz")
        assert resp1.json()["verdict"]["verdict"] == "supported_event"
        assert resp2.json()["verdict"]["verdict"] == "insufficient_evidence"

    def test_case_detail_404_when_not_found(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("JANUS_EVIDENCE_ARTIFACT_DIR", str(tmp_path))
        resp = client.get("/api/evidence/runs/run_r1/cases/nonexistent_case")
        assert resp.status_code == 404

    def test_case_status_includes_citation_status(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("JANUS_EVIDENCE_ARTIFACT_DIR", str(tmp_path))
        _write_verdict(tmp_path, "case_cit", "run_r1", citation_status="fail")
        resp = client.get("/api/evidence/runs/run_r1/cases/case_cit/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "job" in data
        assert data["job"].get("citation_status") == "fail"

    def test_sources_route_returns_registry_fields(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("JANUS_EVIDENCE_ARTIFACT_DIR", str(tmp_path))
        _write_verdict(tmp_path, "case_src", "run_r1")
        resp = client.get("/api/evidence/runs/run_r1/cases/case_src/sources")
        assert resp.status_code == 200
        data = resp.json()
        assert "sources" in data
        assert len(data["sources"]) == 1
        src = data["sources"][0]
        # Source card must come from registry, not LLM
        assert src["url"] == "https://sec.gov/test"
        assert src["source_tier"] == "tier1_official"
        assert src["title"] == "SEC Filing"

    def test_checks_route_returns_check_list(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("JANUS_EVIDENCE_ARTIFACT_DIR", str(tmp_path))
        _write_verdict(tmp_path, "case_chk", "run_r1")
        resp = client.get("/api/evidence/runs/run_r1/cases/case_chk/checks")
        assert resp.status_code == 200
        checks = resp.json()["checks"]
        assert len(checks) == 1
        assert checks[0]["name"] == "source_quality"

    def test_queries_route_returns_query_log(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("JANUS_EVIDENCE_ARTIFACT_DIR", str(tmp_path))
        _write_verdict(tmp_path, "case_q", "run_r1")
        resp = client.get("/api/evidence/runs/run_r1/cases/case_q/queries")
        assert resp.status_code == 200
        queries = resp.json()["queries"]
        assert len(queries) == 1


class TestGetRoutesDoNotTriggerHarness:
    def test_get_run_case_does_not_call_run_harness_task(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("JANUS_EVIDENCE_ARTIFACT_DIR", str(tmp_path))
        _write_verdict(tmp_path, "case_ro", "run_r1")
        with patch("web.evidence_api._run_harness_task") as mock_task:
            resp = client.get("/api/evidence/runs/run_r1/cases/case_ro")
            assert resp.status_code == 200
            mock_task.assert_not_called()

    def test_get_case_status_does_not_call_run_harness_task(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("JANUS_EVIDENCE_ARTIFACT_DIR", str(tmp_path))
        _write_verdict(tmp_path, "case_ro2", "run_r1")
        with patch("web.evidence_api._run_harness_task") as mock_task:
            resp = client.get("/api/evidence/runs/run_r1/cases/case_ro2/status")
            assert resp.status_code == 200
            mock_task.assert_not_called()

    def test_get_sources_does_not_call_run_harness_task(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("JANUS_EVIDENCE_ARTIFACT_DIR", str(tmp_path))
        _write_verdict(tmp_path, "case_ro3", "run_r1")
        with patch("web.evidence_api._run_harness_task") as mock_task:
            client.get("/api/evidence/runs/run_r1/cases/case_ro3/sources")
            mock_task.assert_not_called()


class TestInvestigateRoute:
    def test_investigate_queues_job(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("JANUS_EVIDENCE_ARTIFACT_DIR", str(tmp_path))
        _write_verdict(tmp_path, "case_inv", "run_r1")
        with patch("web.evidence_api._run_harness_task"):
            resp = client.post("/api/evidence/runs/run_r1/cases/case_inv/investigate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert "run_r1" in data["poll_url"]
        assert "case_inv" in data["poll_url"]

    def test_investigate_returns_already_running(self, client, tmp_path, monkeypatch):
        from web.evidence_api import _job_set
        monkeypatch.setenv("JANUS_EVIDENCE_ARTIFACT_DIR", str(tmp_path))
        _write_verdict(tmp_path, "case_inv2", "run_r1")
        _job_set("case_inv2", status="running")
        with patch("web.evidence_api._run_harness_task"):
            resp = client.post("/api/evidence/runs/run_r1/cases/case_inv2/investigate")
        assert resp.json()["status"] == "already_running"
