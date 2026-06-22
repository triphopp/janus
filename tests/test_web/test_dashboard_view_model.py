"""Tests for the dashboard view-model layer (web/view_model.py)."""

import json
from pathlib import Path

import pytest

from web.view_model import (
    build_run_detail_v1,
    build_run_row_v1,
    detect_summary_schema,
    load_vol_surface_summary,
    pick,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "dashboard"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


legacy_summary = _load("legacy_summary_v0.json")
current_summary = _load("current_summary_v1.json")
future_summary = _load("future_summary_v2_with_vol_surface.json")
malformed_summary = _load("malformed_summary_missing_optional.json")


# ── pick() ────────────────────────────────────────────────────────────────────

def test_pick_returns_nested_value():
    assert pick({"a": {"b": 42}}, "a.b") == 42


def test_pick_falls_back_to_second_path():
    assert pick({"new_key": 7}, "old_key", "new_key") == 7


def test_pick_returns_default_when_missing():
    assert pick({}, "a.b.c", default="x") == "x"


# ── detect_summary_schema ─────────────────────────────────────────────────────

def test_detect_schema_returns_zero_for_legacy():
    assert detect_summary_schema(legacy_summary) == 0


def test_detect_schema_returns_version_when_present():
    assert detect_summary_schema(current_summary) == 1
    assert detect_summary_schema(future_summary) == 2


# ── build_run_row_v1 ──────────────────────────────────────────────────────────

def test_legacy_summary_normalizes_to_dashboard_run_row_v1():
    row = build_run_row_v1({"run_id": "legacy1", "summary": legacy_summary})
    assert row["schema_version"] == "dashboard.run_row.v1"
    assert row["run_id"] == "legacy1"
    assert row["n_rows"] == 100
    assert row["identity"]["family"] == "equity"


def test_current_summary_keeps_existing_frontend_compatibility_fields():
    row = build_run_row_v1({"run_id": "current1", "summary": current_summary})
    assert row["metrics_input"] == "market_diagnostic"
    assert row["strategy_metrics_available"] is False
    assert row["dq_status"] == "warn"
    assert any(m["id"] == "sharpe_mean" for m in row["metrics"])


def test_future_summary_preserves_unknown_blocks_in_extensions():
    row = build_run_row_v1({"run_id": "future1", "summary": future_summary})
    assert row["source_schema"]["summary_schema_version"] == 2
    assert "new_vendor_block" in row["extensions"]


def test_missing_optional_fields_do_not_crash_or_drop_run():
    row = build_run_row_v1({"run_id": "bad1", "summary": malformed_summary})
    assert row["run_id"] == "bad1"
    assert row["n_rows"] is None
    assert row["status"]["normalization"] == "ok"


def test_row_has_artifacts_block_with_vol_surface_flag():
    row_no_vs = build_run_row_v1({"run_id": "legacy1", "summary": legacy_summary})
    assert row_no_vs["artifacts"]["has_vol_surface"] is False

    row_with_vs = build_run_row_v1({"run_id": "future1", "summary": future_summary})
    assert row_with_vs["artifacts"]["has_vol_surface"] is True


def test_row_metrics_list_contains_n_rows_and_sharpe():
    row = build_run_row_v1({"run_id": "legacy1", "summary": legacy_summary})
    ids = {m["id"] for m in row["metrics"]}
    assert "n_rows" in ids
    assert "sharpe_mean" in ids


# ── build_run_detail_v1 ───────────────────────────────────────────────────────

def test_detail_sections_include_data_quality():
    detail = build_run_detail_v1({"run_id": "legacy1", "summary": legacy_summary})
    ids = {s["id"] for s in detail["sections"]}
    assert "data_quality" in ids


def test_detail_sections_include_vol_surface_when_artifact_exists():
    detail = build_run_detail_v1({
        "run_id": "future1",
        "summary": future_summary,
        "vol_surface_summary": {"rows": 5000, "latest_as_of": "2024-09-25"},
    })
    ids = {s["id"] for s in detail["sections"]}
    assert "vol_surface" in ids


def test_detail_sections_include_vol_surface_from_summary_ref():
    detail = build_run_detail_v1({"run_id": "future1", "summary": future_summary})
    ids = {s["id"] for s in detail["sections"]}
    assert "vol_surface" in ids


def test_detail_sections_include_option_quality_for_options_runs():
    detail = build_run_detail_v1({"run_id": "future1", "summary": future_summary})
    ids = {s["id"] for s in detail["sections"]}
    assert "option_quality" in ids


def test_detail_includes_unknown_extension_as_raw_json_section():
    detail = build_run_detail_v1({"run_id": "future1", "summary": future_summary})
    ext_sections = [s for s in detail["sections"] if s["kind"] == "raw_json"]
    ext_ids = {s["id"] for s in ext_sections}
    assert "ext_new_vendor_block" in ext_ids


def test_detail_backward_compat_fields_present():
    detail = build_run_detail_v1({"run_id": "legacy1", "summary": legacy_summary})
    assert "data_quality" in detail
    assert "breaks" in detail
    assert "changes_sample" in detail
    assert "stage_hops" in detail
    assert "tagged_return_outliers" in detail


# ── load_vol_surface_summary ──────────────────────────────────────────────────

def test_load_vol_surface_summary_returns_none_when_absent(tmp_path):
    assert load_vol_surface_summary(tmp_path) is None


def test_load_vol_surface_summary_reads_json(tmp_path):
    vs_dir = tmp_path / "vol_surface"
    vs_dir.mkdir()
    (vs_dir / "surface_summary.json").write_text(
        '{"rows": 999, "latest_as_of": "2024-12-31"}', encoding="utf-8"
    )
    result = load_vol_surface_summary(tmp_path)
    assert result["rows"] == 999
