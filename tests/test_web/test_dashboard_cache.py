"""Tests for tools/rebuild_dashboard_cache.py."""

import json
import sys
from pathlib import Path

import pytest

# Make tools/ importable
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

import tools.rebuild_dashboard_cache as cache_tool


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_summary(run_dir: Path, payload: dict) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    p = run_dir / "summary.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _write_manifest(outputs: Path, run_id: str, payload: dict | None = None) -> Path:
    manifest_dir = outputs / "manifest"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    p = manifest_dir / f"{run_id}.json"
    p.write_text(json.dumps(payload or {}), encoding="utf-8")
    return p


def _minimal_summary(run_id: str, family: str = "equity") -> dict:
    return {
        "run_id": run_id,
        "symbol": run_id.upper(),
        "instrument": run_id,
        "family": family,
        "date_range": ["2024-01-01", "2024-12-31"],
        "n_rows_prepared": 100,
        "n_folds": 4,
        "n_folds_passed": 4,
        "metrics_input": "market_diagnostic",
        "strategy_metrics_available": False,
        "stability_score": {"sharpe_mean": 0.5},
        "data_quality": {"status": "pass", "worst_dimension": None, "enforcement": "strict", "dimensions": []},
    }


def _fake_outputs(tmp_path: Path, runs: list[str]) -> Path:
    """Create minimal outputs/ layout under tmp_path."""
    outputs = tmp_path / "outputs"
    for rid in runs:
        _write_summary(outputs / "runs" / rid, _minimal_summary(rid))
        _write_manifest(outputs, rid)
    (outputs / "manifest").mkdir(parents=True, exist_ok=True)
    (outputs / "breaks").mkdir(parents=True, exist_ok=True)
    (outputs / "diff").mkdir(parents=True, exist_ok=True)
    return outputs


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_rebuild_creates_cache_file(tmp_path):
    outputs = _fake_outputs(tmp_path, ["run_a"])
    result = cache_tool.rebuild_cache(outputs_dir=outputs)
    assert "run_a" in result["rebuilt"]
    cache_path = outputs / "dashboard_cache" / "run_a.dashboard.json"
    assert cache_path.exists()


def test_cache_file_has_correct_schema_and_run_id(tmp_path):
    outputs = _fake_outputs(tmp_path, ["run_b"])
    cache_tool.rebuild_cache(outputs_dir=outputs)
    cache_path = outputs / "dashboard_cache" / "run_b.dashboard.json"
    entry = json.loads(cache_path.read_text(encoding="utf-8"))
    assert entry["cache_schema_version"] == "dashboard.cache.v1"
    assert entry["run_id"] == "run_b"
    assert "view_model" in entry
    vm = entry["view_model"]
    assert vm["schema_version"] == "dashboard.run_detail.v1"


def test_rebuild_skips_fresh_cache_on_second_call(tmp_path):
    outputs = _fake_outputs(tmp_path, ["run_c"])
    result1 = cache_tool.rebuild_cache(outputs_dir=outputs)
    assert "run_c" in result1["rebuilt"]
    result2 = cache_tool.rebuild_cache(outputs_dir=outputs)
    assert "run_c" in result2["skipped"]
    assert result2["rebuilt"] == []


def test_force_flag_rebuilds_even_when_fresh(tmp_path):
    outputs = _fake_outputs(tmp_path, ["run_d"])
    cache_tool.rebuild_cache(outputs_dir=outputs)
    result = cache_tool.rebuild_cache(outputs_dir=outputs, force=True)
    assert "run_d" in result["rebuilt"]
    assert result["skipped"] == []


def test_check_returns_true_when_cache_is_fresh(tmp_path):
    outputs = _fake_outputs(tmp_path, ["run_e"])
    cache_tool.rebuild_cache(outputs_dir=outputs)
    assert cache_tool.check_cache(outputs_dir=outputs) is True


def test_check_returns_false_when_summary_modified(tmp_path):
    outputs = _fake_outputs(tmp_path, ["run_f"])
    cache_tool.rebuild_cache(outputs_dir=outputs)
    # Modify the source file after caching
    summary_path = outputs / "runs" / "run_f" / "summary.json"
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    data["n_rows_prepared"] = 9999
    summary_path.write_text(json.dumps(data), encoding="utf-8")
    assert cache_tool.check_cache(outputs_dir=outputs) is False


def test_rebuild_never_calls_run_pipeline(tmp_path, monkeypatch):
    """Cache tool must not import or invoke run_pipeline."""
    import builtins
    orig_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        assert "run_pipeline" not in name, "cache tool must not import run_pipeline"
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    outputs = _fake_outputs(tmp_path, ["run_g"])
    cache_tool.rebuild_cache(outputs_dir=outputs)
