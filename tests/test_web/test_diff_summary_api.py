"""Tests for /api/runs/{run_id}/diff-summary and extended diff-meta."""

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from web import dashboard, scanner


def _patch(monkeypatch, tmp_path):
    diff_dir = tmp_path / "diff"
    diff_dir.mkdir()
    monkeypatch.setattr(scanner, "DIFF_DIR", diff_dir)
    return diff_dir


def _write_ledger(diff_dir: Path, run_id: str, records: list[dict] | None = None) -> Path:
    p = diff_dir / f"{run_id}_changes.jsonl"
    recs = records or [
        {"stage_from": "adapter", "stage_to": "validators",
         "change_type": "cell_mod", "column": "price_std",
         "reason": "outlier_cap", "delta": -0.1, "key": {}}
    ]
    p.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    return p


def _write_summary(diff_dir: Path, run_id: str, status: str = "pass",
                   findings: list | None = None) -> Path:
    p = diff_dir / f"{run_id}_summary.json"
    p.write_text(json.dumps({
        "run_id": run_id, "status": status, "findings": findings or [],
        "policy_version": 1, "ledger": {}, "rollups": {}, "rates": {},
        "protected": {}, "budgets": {}, "numeric_delta_stats": {}, "samples": {},
        "context": {}, "baseline": {"available": False},
    }), encoding="utf-8")
    return p


# ── _diff_meta includes review fields ────────────────────────────────────────

def test_diff_meta_includes_review_status_when_summary_exists(tmp_path, monkeypatch):
    diff_dir = _patch(monkeypatch, tmp_path)
    _write_ledger(diff_dir, "meta_run")
    _write_summary(diff_dir, "meta_run", status="warn",
                   findings=[{"code": "UNATTRIBUTED_BUDGET_WARN", "status": "warn",
                               "detail": "1 UNATTRIBUTED", "example": None}])
    meta = dashboard._diff_meta("meta_run")
    assert meta["review_status"] == "warn"
    assert meta["findings_count"] == 1
    assert meta["has_summary"] is True
    assert len(meta["top_findings"]) == 1


def test_diff_meta_review_status_none_when_no_summary(tmp_path, monkeypatch):
    diff_dir = _patch(monkeypatch, tmp_path)
    _write_ledger(diff_dir, "no_sum")
    meta = dashboard._diff_meta("no_sum")
    assert meta["review_status"] is None
    assert meta["has_summary"] is False


# ── api_diff_summary ──────────────────────────────────────────────────────────

def test_diff_summary_returns_existing_summary(tmp_path, monkeypatch):
    diff_dir = _patch(monkeypatch, tmp_path)
    _write_ledger(diff_dir, "sum_run")
    _write_summary(diff_dir, "sum_run", status="pass")
    result = dashboard.api_diff_summary("sum_run")
    assert result["run_id"] == "sum_run"
    assert result["status"] == "pass"


def test_diff_summary_generates_on_demand_when_missing(tmp_path, monkeypatch):
    diff_dir = _patch(monkeypatch, tmp_path)
    _write_ledger(diff_dir, "gen_run")
    result = dashboard.api_diff_summary("gen_run")
    assert result["status"] == "pass"
    assert (diff_dir / "gen_run_summary.json").exists()


def test_diff_summary_404_when_no_artifacts(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    with pytest.raises(HTTPException) as exc_info:
        dashboard.api_diff_summary("ghost")
    assert exc_info.value.status_code == 404


def test_diff_summary_regenerate_flag(tmp_path, monkeypatch):
    diff_dir = _patch(monkeypatch, tmp_path)
    _write_ledger(diff_dir, "regen_run")
    _write_summary(diff_dir, "regen_run", status="pass")
    result = dashboard.api_diff_summary("regen_run", regenerate=True)
    assert result["status"] == "pass"


def test_diff_summary_fail_status_propagates(tmp_path, monkeypatch):
    diff_dir = _patch(monkeypatch, tmp_path)
    _write_ledger(diff_dir, "fail_run", [
        {"stage_from": "a", "stage_to": "b", "change_type": "cell_mod",
         "column": "strike", "reason": "outlier_cap", "delta": -1.0, "key": {}}
    ])
    result = dashboard.api_diff_summary("fail_run")
    assert result["status"] == "fail"
    assert any(f["code"] == "KEY_MUTATION" for f in result["findings"])


def test_diff_summary_api_endpoint_exists_on_app(tmp_path, monkeypatch):
    routes = {r.path for r in dashboard.app.routes}
    assert "/api/runs/{run_id}/diff-summary" in routes
