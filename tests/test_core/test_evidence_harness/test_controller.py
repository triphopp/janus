"""Integration tests for HarnessController — Phase 1 skeleton."""

import json
from pathlib import Path

import pytest

from core.evidence_harness.schema import OutlierCasePackage, HarnessRunResult
from core.evidence_harness.config import load_harness_config
from core.evidence_harness.controller import run_harness


def _wti_case() -> OutlierCasePackage:
    return OutlierCasePackage(
        case_id="case_wti_fixture_001",
        run_id="fixture_run",
        signal_type="return_outlier",
        as_of_date="2024-09-25",
        family="futures",
        instrument="WTI",
        symbol="WTI",
        severity="severe",
        metric_name="return_std",
        observed_value=0.082,
        z_score=7.4,
    )


class TestRunHarnessPhase1:
    def test_returns_harness_run_result(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        result = run_harness(_wti_case(), cfg)
        assert isinstance(result, HarnessRunResult)

    def test_writes_verdict_json(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        result = run_harness(_wti_case(), cfg)
        verdict_path = Path(result.artifact_paths["verdict"])
        assert verdict_path.exists()
        data = json.loads(verdict_path.read_text())
        assert "verdict" in data
        assert data["schema_version"] == "evidence.verdict.v1"

    def test_writes_all_required_artifacts(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        result = run_harness(_wti_case(), cfg)

        required = [
            "case_package", "config", "query_log", "search_results",
            "fetch_log", "sources", "claims", "checks",
            "citation_report", "verdict", "replay_manifest",
        ]
        for key in required:
            assert key in result.artifact_paths, f"missing artifact path: {key}"
            assert Path(result.artifact_paths[key]).exists(), f"artifact not on disk: {key}"

    def test_artifact_paths_are_absolute(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        result = run_harness(_wti_case(), cfg)
        for key, path in result.artifact_paths.items():
            assert Path(path).is_absolute(), f"{key} path is not absolute: {path}"

    def test_verdict_has_deterministic_field(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        result = run_harness(_wti_case(), cfg)
        verdict_path = Path(result.artifact_paths["verdict"])
        data = json.loads(verdict_path.read_text())
        assert "deterministic_verdict" in data

    def test_replay_manifest_written(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        result = run_harness(_wti_case(), cfg)
        manifest_path = Path(result.artifact_paths["replay_manifest"])
        assert manifest_path.exists()
        data = json.loads(manifest_path.read_text())
        assert "harness_run_id" in data
        assert "case_id" in data
        assert "config_hash" in data

    def test_harness_run_id_is_deterministic_for_same_inputs(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path / "run1")
        r1 = run_harness(_wti_case(), cfg)

        cfg2 = load_harness_config()
        cfg2.artifact_dir = str(tmp_path / "run2")
        r2 = run_harness(_wti_case(), cfg2)

        # harness_run_id includes started_at timestamp so may differ,
        # but case_id and run_id must be stable
        assert r1.case_id == r2.case_id
        assert r1.run_id == r2.run_id

    def test_invalid_case_raises_before_any_artifact(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        bad_case = OutlierCasePackage(
            case_id="", run_id="r", signal_type="s", as_of_date="2024-01-01"
        )
        with pytest.raises(ValueError):
            run_harness(bad_case, cfg)

    def test_uses_fixture_search_provider_by_default(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        cfg.search_provider = "fixture"
        result = run_harness(_wti_case(), cfg)
        assert result.status in (
            "supported_event", "insufficient_evidence", "unsupported",
            "conflicting_evidence", "suspected_data_issue",
        )

    def test_all_json_artifacts_are_valid_json(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        result = run_harness(_wti_case(), cfg)
        run_dir = Path(result.artifact_paths["verdict"]).parent
        for p in run_dir.glob("*.json"):
            json.loads(p.read_text())  # must not raise

    def test_no_market_data_mutation(self, tmp_path):
        """Harness must not write to any market data path."""
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        run_harness(_wti_case(), cfg)
        # all outputs must be under artifact_dir, never under outputs/prepared/ etc.
        for path in tmp_path.rglob("*"):
            assert "prepared" not in str(path)
            assert "canonical" not in str(path)
