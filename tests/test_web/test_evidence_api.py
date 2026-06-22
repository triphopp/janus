"""Tests for the Evidence API router — mocked store, no DB required."""

import json
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from web.dashboard import app
from web.evidence_api import _reset_store


# ── Fixtures ──────────────────────────────────────────────────────────────────

CASE_ROW = {
    "case_id": "case_wti",
    "run_id": "run_test",
    "instrument": "CL1",
    "family": "futures",
    "as_of_date": "2024-09-25",
    "signal_type": "return_outlier",
    "severity": "high",
    "status": "unreviewed",
    "verdict": "supported_event",
    "confidence": "high",
    "event_type": "commodity_inventory",
    "created_at": "2024-09-25T10:00:00+00:00",
}

GRAPH = {
    "case": CASE_ROW,
    "nodes": [
        {
            "node_id": "node_root_case_wti",
            "case_id": "case_wti",
            "node_type": "outlier",
            "source_tier": None,
            "title": "Outlier: case_wti",
            "published_at": None,
            "observed_at": "2024-09-25",
            "effective_at": None,
            "confidence": None,
        },
        {
            "node_id": "node_src_doc_abc",
            "case_id": "case_wti",
            "source_id": "src_doc_abc",
            "node_type": "macro_release",
            "source_tier": "tier1_official",
            "title": "EIA Inventory Report",
            "published_at": "2024-09-25T09:00:00+00:00",
            "observed_at": None,
            "effective_at": None,
            "confidence": 0.85,
        },
    ],
    "edges": [
        {
            "edge_id": "edge_abc",
            "case_id": "case_wti",
            "from_node": "node_src_doc_abc",
            "to_node": "node_root_case_wti",
            "relation": "supports",
            "confidence": 0.85,
            "check_name": "source_quality",
            "rationale": "EIA tier1 source",
        }
    ],
    "sources": [
        {
            "source_id": "src_doc_abc",
            "url": "https://eia.gov/report",
            "domain": "eia.gov",
            "source_tier": "tier1_official",
            "title": "EIA Inventory Report",
        }
    ],
    "checks": [
        {"name": "source_quality", "status": "pass", "score": 0.9, "rationale": "tier1"},
        {"name": "temporal_consistency", "status": "pass", "score": 0.8, "rationale": "before move"},
    ],
}


def _mock_store(graph=GRAPH, cases=None):
    store = MagicMock()
    store.load_case_graph.return_value = graph
    store.list_cases.return_value = cases if cases is not None else [CASE_ROW]
    store.update_case_status.return_value = None
    store.append_event.return_value = None
    return store


@pytest.fixture(autouse=True)
def reset_store():
    """Clear the module-level store singleton before each test."""
    _reset_store()
    yield
    _reset_store()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def client_with_store(client):
    store = _mock_store()
    with patch("web.evidence_api._get_store", return_value=store):
        yield client, store


# ── GET /api/evidence/cases ───────────────────────────────────────────────────

class TestListCases:
    def test_returns_cases_list(self, client):
        store = _mock_store()
        with patch("web.evidence_api._get_store", return_value=store):
            resp = client.get("/api/evidence/cases")
        assert resp.status_code == 200
        data = resp.json()
        assert "cases" in data
        assert "count" in data
        assert data["count"] == 1

    def test_filter_by_status(self, client):
        store = _mock_store()
        with patch("web.evidence_api._get_store", return_value=store):
            resp = client.get("/api/evidence/cases?status=unreviewed")
        assert resp.status_code == 200
        store.list_cases.assert_called_once_with({"status": "unreviewed"})

    def test_filter_by_verdict(self, client):
        store = _mock_store()
        with patch("web.evidence_api._get_store", return_value=store):
            resp = client.get("/api/evidence/cases?verdict=supported_event")
        assert resp.status_code == 200
        store.list_cases.assert_called_with({"verdict": "supported_event"})

    def test_no_filter_passes_none(self, client):
        store = _mock_store()
        with patch("web.evidence_api._get_store", return_value=store):
            client.get("/api/evidence/cases")
        store.list_cases.assert_called_with(None)

    def test_503_when_db_not_configured(self, client):
        import os
        env_backup = os.environ.pop("JANUS_EVIDENCE_DATABASE_URL", None)
        try:
            resp = client.get("/api/evidence/cases")
            assert resp.status_code == 503
        finally:
            if env_backup:
                os.environ["JANUS_EVIDENCE_DATABASE_URL"] = env_backup


# ── GET /api/evidence/cases/{case_id} ─────────────────────────────────────────

class TestGetCase:
    def test_returns_case_sources_checks(self, client):
        store = _mock_store()
        with patch("web.evidence_api._get_store", return_value=store):
            resp = client.get("/api/evidence/cases/case_wti")
        assert resp.status_code == 200
        data = resp.json()
        assert data["case"]["case_id"] == "case_wti"
        assert "sources" in data
        assert "checks" in data

    def test_404_when_not_found(self, client):
        store = _mock_store(graph={})
        with patch("web.evidence_api._get_store", return_value=store):
            resp = client.get("/api/evidence/cases/nonexistent")
        assert resp.status_code == 404


# ── GET /api/evidence/cases/{case_id}/graph ───────────────────────────────────

class TestGetCaseGraph:
    def test_returns_nodes_and_edges(self, client):
        store = _mock_store()
        with patch("web.evidence_api._get_store", return_value=store):
            resp = client.get("/api/evidence/cases/case_wti/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "edges" in data
        assert data["case_id"] == "case_wti"

    def test_404_when_not_found(self, client):
        store = _mock_store(graph={})
        with patch("web.evidence_api._get_store", return_value=store):
            resp = client.get("/api/evidence/cases/missing/graph")
        assert resp.status_code == 404


# ── GET /api/evidence/cases/{case_id}/timeline ────────────────────────────────

class TestGetCaseTimeline:
    def test_returns_ordered_timeline(self, client):
        store = _mock_store()
        with patch("web.evidence_api._get_store", return_value=store):
            resp = client.get("/api/evidence/cases/case_wti/timeline")
        assert resp.status_code == 200
        data = resp.json()
        assert "timeline" in data
        assert data["timeline"][0]["node_type"] == "outlier"

    def test_outlier_node_first(self, client):
        store = _mock_store()
        with patch("web.evidence_api._get_store", return_value=store):
            resp = client.get("/api/evidence/cases/case_wti/timeline")
        timeline = resp.json()["timeline"]
        assert timeline[0]["node_id"] == "node_root_case_wti"

    def test_404_when_not_found(self, client):
        store = _mock_store(graph={})
        with patch("web.evidence_api._get_store", return_value=store):
            resp = client.get("/api/evidence/cases/missing/timeline")
        assert resp.status_code == 404


# ── POST /api/evidence/cases/{case_id}/review ─────────────────────────────────

class TestReviewCase:
    def test_valid_action_returns_new_status(self, client):
        store = _mock_store()
        with patch("web.evidence_api._get_store", return_value=store):
            resp = client.post(
                "/api/evidence/cases/case_wti/review",
                json={"action": "mark_supported_event", "actor": "test_analyst"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["new_status"] == "supported_event"
        assert data["actor"] == "test_analyst"

    def test_close_action_sets_closed_status(self, client):
        store = _mock_store()
        with patch("web.evidence_api._get_store", return_value=store):
            resp = client.post(
                "/api/evidence/cases/case_wti/review",
                json={"action": "close", "actor": "analyst"},
            )
        assert resp.status_code == 200
        assert resp.json()["new_status"] == "closed"

    def test_invalid_action_returns_422(self, client):
        store = _mock_store()
        with patch("web.evidence_api._get_store", return_value=store):
            resp = client.post(
                "/api/evidence/cases/case_wti/review",
                json={"action": "delete_everything", "actor": "hacker"},
            )
        assert resp.status_code == 422

    def test_event_appended(self, client):
        store = _mock_store()
        with patch("web.evidence_api._get_store", return_value=store):
            client.post(
                "/api/evidence/cases/case_wti/review",
                json={"action": "waive_with_reason", "actor": "analyst", "reason": "known holiday"},
            )
        store.append_event.assert_called_once()
        call_args = store.append_event.call_args
        assert call_args[0][0] == "case_wti"
        assert call_args.kwargs["action"] == "waive_with_reason"

    def test_status_updated(self, client):
        store = _mock_store()
        with patch("web.evidence_api._get_store", return_value=store):
            client.post(
                "/api/evidence/cases/case_wti/review",
                json={"action": "escalate", "actor": "analyst"},
            )
        store.update_case_status.assert_called_once_with("case_wti", "investigating")

    def test_404_when_case_not_found(self, client):
        store = _mock_store(graph={})
        with patch("web.evidence_api._get_store", return_value=store):
            resp = client.post(
                "/api/evidence/cases/missing/review",
                json={"action": "close", "actor": "analyst"},
            )
        assert resp.status_code == 404

    def test_all_valid_actions_accepted(self, client):
        from web.evidence_api import _VALID_ACTIONS
        store = _mock_store()
        for action in _VALID_ACTIONS:
            with patch("web.evidence_api._get_store", return_value=store):
                resp = client.post(
                    "/api/evidence/cases/case_wti/review",
                    json={"action": action, "actor": "test"},
                )
            assert resp.status_code == 200, f"action {action!r} should be valid"


# ── GET /healthz/evidence ─────────────────────────────────────────────────────

class TestEvidenceHealthz:
    def test_returns_ok_when_store_reachable(self, client):
        store = _mock_store()
        with patch("web.evidence_api._get_store", return_value=store):
            resp = client.get("/api/evidence/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_returns_503_when_unconfigured(self, client):
        import os
        env_backup = os.environ.pop("JANUS_EVIDENCE_DATABASE_URL", None)
        try:
            resp = client.get("/api/evidence/healthz")
            assert resp.status_code == 503
            assert resp.json()["status"] == "unconfigured"
        finally:
            if env_backup:
                os.environ["JANUS_EVIDENCE_DATABASE_URL"] = env_backup


# ── POST /api/evidence/run ────────────────────────────────────────────────────

class TestTriggerRun:
    RUN_REQ = {
        "run_id": "run_test",
        "case_id": "case_aapl_20170201",
        "instrument": "AAPL",
        "family": "equity",
        "symbol": "AAPL",
        "as_of_date": "2017-02-01",
        "signal_type": "return_outlier",
        "z_score": 8.47,
        "severity": "borderline",
        "observed_value": 0.061,
    }

    def test_queues_run_and_returns_poll_url(self, client):
        from unittest.mock import patch
        with patch("web.evidence_api._run_harness_task"):
            resp = client.post("/api/evidence/run", json=self.RUN_REQ)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert "poll_url" in data
        assert data["case_id"] == "case_aapl_20170201"

    def test_returns_already_running_if_job_in_flight(self, client):
        from web.evidence_api import _job_set
        _job_set("case_aapl_20170201", status="running")
        with patch("web.evidence_api._run_harness_task"):
            resp = client.post("/api/evidence/run", json=self.RUN_REQ)
        assert resp.status_code == 200
        assert resp.json()["status"] == "already_running"

    def test_allows_rerun_after_done(self, client):
        from web.evidence_api import _job_set
        from unittest.mock import patch
        _job_set("case_aapl_20170201", status="done", verdict="supported_event")
        with patch("web.evidence_api._run_harness_task"):
            resp = client.post("/api/evidence/run", json=self.RUN_REQ)
        assert resp.json()["status"] == "queued"


# ── GET /api/evidence/cases/{case_id}/status ──────────────────────────────────

class TestCaseStatus:
    def test_returns_not_investigated_for_unknown_case(self, client):
        resp = client.get("/api/evidence/cases/unknown_case_xyz/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job"]["status"] == "not_investigated"

    def test_reflects_running_job(self, client):
        from web.evidence_api import _job_set
        _job_set("case_running", status="running", started_at="2024-01-01T00:00:00Z")
        resp = client.get("/api/evidence/cases/case_running/status")
        assert resp.json()["job"]["status"] == "running"

    def test_reflects_done_job_with_verdict(self, client):
        from web.evidence_api import _job_set
        _job_set("case_done", status="done", verdict="supported_event", confidence="high")
        resp = client.get("/api/evidence/cases/case_done/status")
        data = resp.json()
        assert data["job"]["status"] == "done"
        assert data["job"]["verdict"] == "supported_event"


# ── GET /api/evidence/runs/{run_id}/outliers ──────────────────────────────────

# INTC/20260619_062444 is a real run present in outputs/runs/ with 41 outliers.
_REAL_RUN_ID = "20260619_062444"


class TestRunOutliers:
    def test_returns_outliers_from_real_parquet(self, client):
        resp = client.get(f"/api/evidence/runs/{_REAL_RUN_ID}/outliers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        first = data["outliers"][0]
        assert "case_id" in first
        assert "z_score" in first
        assert "evidence_status" in first
        assert first["evidence_status"] == "not_investigated"

    def test_outliers_sorted_by_abs_zscore(self, client):
        resp = client.get(f"/api/evidence/runs/{_REAL_RUN_ID}/outliers")
        zs = [abs(o["z_score"]) for o in resp.json()["outliers"]]
        assert zs == sorted(zs, reverse=True)

    def test_returns_404_for_unknown_run(self, client):
        resp = client.get("/api/evidence/runs/nonexistent_run_id/outliers")
        assert resp.status_code == 404

    def test_evidence_status_reflects_job(self, client):
        from web.evidence_api import _job_set
        resp = client.get(f"/api/evidence/runs/{_REAL_RUN_ID}/outliers")
        first = resp.json()["outliers"][0]
        _job_set(first["case_id"], status="done", verdict="supported_event")
        resp2 = client.get(f"/api/evidence/runs/{_REAL_RUN_ID}/outliers")
        updated = next(o for o in resp2.json()["outliers"] if o["case_id"] == first["case_id"])
        assert updated["evidence_status"] == "done"
        assert updated["verdict"] == "supported_event"

    def test_investigate_url_present(self, client):
        resp = client.get(f"/api/evidence/runs/{_REAL_RUN_ID}/outliers")
        for o in resp.json()["outliers"]:
            assert o["investigate_url"] == "/api/evidence/run"


# ── Config env var interpolation ──────────────────────────────────────────────

class TestEnvVarInterpolation:
    def test_interpolates_env_var(self, monkeypatch):
        monkeypatch.setenv("TEST_API_KEY", "sk-test-123")
        from core.evidence_harness.config import _expand
        assert _expand("${TEST_API_KEY}") == "sk-test-123"

    def test_uses_default_when_var_missing(self):
        from core.evidence_harness.config import _expand
        result = _expand("${NONEXISTENT_VAR:-fallback_value}")
        assert result == "fallback_value"

    def test_leaves_unset_var_intact_without_default(self):
        import os
        os.environ.pop("NONEXISTENT_VAR_XYZ", None)
        from core.evidence_harness.config import _expand
        result = _expand("${NONEXISTENT_VAR_XYZ}")
        assert result == "${NONEXISTENT_VAR_XYZ}"

    def test_interpolates_in_loaded_config(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TEST_LLM_KEY", "sk-test-456")
        cfg_file = tmp_path / "evidence_search.yaml"
        cfg_file.write_text(
            "evidence_search:\n  llm_api_key: ${TEST_LLM_KEY}\n  llm_provider: mock\n"
        )
        from core.evidence_harness.config import load_harness_config
        cfg = load_harness_config(str(cfg_file))
        assert cfg.llm_api_key == "sk-test-456"

    def test_interpolates_with_default_in_loaded_config(self, tmp_path):
        cfg_file = tmp_path / "evidence_search.yaml"
        cfg_file.write_text(
            "evidence_search:\n  llm_api_key: ${MISSING_KEY:-default-key}\n  llm_provider: mock\n"
        )
        from core.evidence_harness.config import load_harness_config
        cfg = load_harness_config(str(cfg_file))
        assert cfg.llm_api_key == "default-key"
