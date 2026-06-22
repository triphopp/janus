"""Dashboard view-model layer.

Normalizes pipeline artifacts into stable DashboardRunRowV1 / DashboardRunDetailV1
shapes regardless of which summary_schema_version they were written with.

This module never touches the filesystem directly — callers pass an artifact bundle
dict.  New optional sections can be added here without changing scanner.py routes or
React component layout.

Artifact bundle keys (all optional):
  run_id              str
  summary             dict | None       — parsed summary.json
  summary_path        str | None        — path string for raw_artifact_refs
  manifest            dict | None       — parsed manifest/<run_id>.json
  has_diff            bool
  has_report          bool
  breaks_open         int
  unattributed        int
  changes_sample      list[dict]
  stage_hops          list[dict]
  tagged_return_outliers  dict          — {rows, summary}
  price_adjustments   dict | None
  vol_surface_summary dict | None       — parsed vol_surface/surface_summary.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# ── Known scalar path aliases ─────────────────────────────────────────────────

N_ROWS_PATHS = [
    "n_rows_prepared",
    "n_rows_raw",
    "row_counts.prepared",
    "data.rows_prepared",
]

SHARPE_MEAN_PATHS = [
    "stability_score.sharpe_mean",
    "metrics.stability.sharpe_mean",
]

DQ_STATUS_PATHS = [
    "data_quality.status",
    "quality.data.status",
]

DQ_WORST_PATHS = [
    "data_quality.worst_dimension",
    "quality.data.worst_dimension",
]

# Top-level keys that belong to known sections — not treated as extensions.
_KNOWN_TOP_KEYS = {
    "run_id", "instrument", "symbol", "family", "date_range", "created_at",
    "n_rows_prepared", "n_rows_raw", "n_folds", "n_folds_passed",
    "metrics_input", "strategy_metrics_available",
    "stability_score", "data_quality", "price_adjustments",
    "option_quality", "quarantine", "contract_gate", "coverage",
    "cdc", "lineage_purge", "summary_schema_version",
    "vol_surface_ref",
}


# ── Safe path traversal ───────────────────────────────────────────────────────

def pick(summary: dict, *paths: str, default: Any = None) -> Any:
    """Return the first non-None value found at any dotted path in summary."""
    for path in paths:
        node = summary
        for key in path.split("."):
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if node is not None:
            return node
    return default


# ── Schema version detection ──────────────────────────────────────────────────

def detect_summary_schema(summary: dict) -> int:
    """Return the numeric schema version; 0 means legacy (key absent)."""
    v = summary.get("summary_schema_version")
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# ── Vol surface artifact loader (Step 5) ─────────────────────────────────────

def load_vol_surface_summary(run_dir: Path) -> dict | None:
    """Return vol surface metadata dict from run artifacts, or None."""
    path = run_dir / "vol_surface" / "surface_summary.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _vol_surface_from_summary(summary: dict) -> dict | None:
    """Extract embedded vol surface ref from summary (vol_surface_ref key)."""
    ref = summary.get("vol_surface_ref")
    return ref if isinstance(ref, dict) else None


# ── Sections builder ──────────────────────────────────────────────────────────

def _build_sections(summary: dict, artifacts: dict) -> list[dict]:
    sections: list[dict] = []

    # Data quality
    dq = summary.get("data_quality")
    sections.append({
        "id": "data_quality",
        "title": "Data quality scorecard",
        "kind": "scorecard",
        "status": dq.get("status") if isinstance(dq, dict) else None,
        "metrics": [],
        "payload": dq,
        "source_artifacts": ["summary.json"],
        "empty_reason": None if dq else "no data quality scorecard recorded",
    })

    # Price adjustments
    pa = summary.get("price_adjustments") or artifacts.get("price_adjustments")
    sections.append({
        "id": "price_adjustments",
        "title": "Price adjustments",
        "kind": "metric_grid",
        "status": pa.get("status") if isinstance(pa, dict) else "not_applicable",
        "metrics": [],
        "payload": pa,
        "source_artifacts": ["summary.json"],
        "empty_reason": None if pa else "no price adjustment data",
    })

    # Option quality
    oq = summary.get("option_quality")
    if oq:
        sections.append({
            "id": "option_quality",
            "title": "Option universe quality",
            "kind": "metric_grid",
            "status": "available",
            "metrics": [],
            "payload": oq,
            "source_artifacts": ["summary.json"],
            "empty_reason": None,
        })

    # Vol surface (compatibility hook — plotting is in vol_surface_visualization_plan)
    vs = artifacts.get("vol_surface_summary") or _vol_surface_from_summary(summary)
    if vs:
        sections.append({
            "id": "vol_surface",
            "title": "Vol surface",
            "kind": "artifact_link",
            "status": "available",
            "metrics": [],
            "payload": vs,
            "source_artifacts": ["vol_surface/surface_summary.json"],
            "empty_reason": None,
        })

    # Extensions — unknown top-level keys preserved as raw_json panels
    for ext_key, ext_val in summary.items():
        if ext_key not in _KNOWN_TOP_KEYS:
            sections.append({
                "id": f"ext_{ext_key}",
                "title": ext_key,
                "kind": "raw_json",
                "status": None,
                "metrics": [],
                "payload": ext_val,
                "source_artifacts": ["summary.json"],
                "empty_reason": None,
            })

    return sections


# ── Metrics list ──────────────────────────────────────────────────────────────

def _build_metrics(summary: dict) -> list[dict]:
    out: list[dict] = []
    n_rows = pick(summary, *N_ROWS_PATHS)
    if n_rows is not None:
        out.append({"id": "n_rows", "label": "Rows", "value": n_rows, "format": "integer"})
    sharpe = pick(summary, *SHARPE_MEAN_PATHS)
    if sharpe is not None:
        out.append({"id": "sharpe_mean", "label": "Sharpe", "value": sharpe, "format": "number"})
    dq = summary.get("data_quality") or {}
    dq_status = dq.get("status") if isinstance(dq, dict) else None
    if dq_status:
        out.append({"id": "dq_status", "label": "Data quality", "value": dq_status,
                    "format": "text", "status": dq_status})
    n_folds = summary.get("n_folds")
    if n_folds is not None:
        out.append({"id": "n_folds", "label": "Folds", "value": n_folds, "format": "integer"})
    n_folds_passed = summary.get("n_folds_passed")
    if n_folds_passed is not None:
        out.append({"id": "n_folds_passed", "label": "Folds passed",
                    "value": n_folds_passed, "format": "integer"})
    return out


def _build_extensions(summary: dict) -> dict:
    return {k: v for k, v in summary.items() if k not in _KNOWN_TOP_KEYS}


# ── Normalizers ───────────────────────────────────────────────────────────────

def _legacy_compat(summary: dict, artifacts: dict) -> dict:
    """Extract legacy fields expected by the current frontend."""
    dq = summary.get("data_quality") or {}
    ss = summary.get("stability_score") or {}
    pa = summary.get("price_adjustments") or artifacts.get("price_adjustments") or {}
    return {
        "n_rows": pick(summary, *N_ROWS_PATHS),
        "n_folds": summary.get("n_folds"),
        "n_folds_passed": summary.get("n_folds_passed"),
        "metrics_input": summary.get("metrics_input"),
        "strategy_metrics_available": summary.get("strategy_metrics_available"),
        "sharpe_mean": (ss.get("sharpe_mean") if isinstance(ss, dict) else None),
        "dq_status": (dq.get("status") if isinstance(dq, dict) else None),
        "dq_worst_dimension": (dq.get("worst_dimension") if isinstance(dq, dict) else None),
        "dq_fail_count": sum(
            1 for d in (dq.get("dimensions") or []) if d.get("status") == "fail"
        ),
        "price_adjustments": pa or None,
        "adjustment_factor_rows": int(pa.get("factor_rows") or 0),
        "adjustment_warning_rows": int(pa.get("warning_rows") or 0),
        "adjustment_policy": pa.get("policy"),
        "adjustment_status": pa.get("status", "not_applicable"),
        "adjustment_max_abs_price_diff": pa.get("max_abs_price_std_vs_provider_adjusted"),
    }


def normalize_summary_legacy_v0(summary: dict, artifacts: dict) -> dict:
    base = _legacy_compat(summary, artifacts)
    base["source_schema"] = {"summary_schema_version": 0, "dashboard_adapter": "legacy_v0"}
    base["extensions"] = _build_extensions(summary)
    return base


def normalize_summary_v1(summary: dict, artifacts: dict) -> dict:
    base = _legacy_compat(summary, artifacts)
    base["source_schema"] = {"summary_schema_version": 1, "dashboard_adapter": "v1"}
    base["extensions"] = _build_extensions(summary)
    return base


def normalize_summary_future(summary: dict, artifacts: dict) -> dict:
    base = _legacy_compat(summary, artifacts)
    v = detect_summary_schema(summary)
    base["source_schema"] = {"summary_schema_version": v, "dashboard_adapter": "future_best_effort"}
    base["extensions"] = _build_extensions(summary)
    return base


# ── Public builders ───────────────────────────────────────────────────────────

def build_run_row_v1(artifacts: dict) -> dict:
    """Build a stable DashboardRunRowV1 from an artifact bundle.

    Merges new stable fields with legacy compat fields so existing frontend
    field accesses continue to work without changes.
    """
    run_id = artifacts.get("run_id") or ""
    summary = artifacts.get("summary") or {}
    manifest = artifacts.get("manifest") or {}
    schema_v = detect_summary_schema(summary)

    if schema_v == 0:
        normalized = normalize_summary_legacy_v0(summary, artifacts)
    elif schema_v == 1:
        normalized = normalize_summary_v1(summary, artifacts)
    else:
        normalized = normalize_summary_future(summary, artifacts)

    dq = summary.get("data_quality") or {}
    identity = {
        "symbol": summary.get("symbol"),
        "instrument": summary.get("instrument"),
        "family": summary.get("family"),
        "date_range": summary.get("date_range"),
    }
    has_vol = (
        artifacts.get("vol_surface_summary") is not None
        or _vol_surface_from_summary(summary) is not None
    )

    row: dict = {
        "schema_version": "dashboard.run_row.v1",
        "run_id": run_id,
        "created_at": summary.get("created_at") or manifest.get("created_at"),
        # legacy top-level identity fields (frontend uses these directly)
        "instrument": identity["instrument"],
        "symbol": identity["symbol"],
        "family": identity["family"],
        "date_range": identity["date_range"],
        # new stable identity block
        "identity": identity,
        "metrics": _build_metrics(summary),
        "status": {
            "data_quality": {
                "status": dq.get("status") if isinstance(dq, dict) else None,
                "worst_dimension": dq.get("worst_dimension") if isinstance(dq, dict) else None,
            },
            "breaks_open": artifacts.get("breaks_open", 0),
            "unattributed": artifacts.get("unattributed", 0),
            "normalization": "ok",
        },
        "artifacts": {
            "has_diff": bool(artifacts.get("has_diff")),
            "has_report": bool(artifacts.get("has_report")),
            "has_vol_surface": has_vol,
        },
        "sections_summary": [],
        "source_schema": normalized.get("source_schema", {}),
        "extensions": normalized.get("extensions", {}),
    }
    # Merge legacy compat fields — must not overwrite the new stable keys above
    for k, v in normalized.items():
        if k not in row:
            row[k] = v

    # Legacy scanner fields that come from non-summary sources
    for k in ("code_version", "config_hash", "knowledge_cutoff", "n_trials",
              "env", "input_data_hashes", "output_data_hashes", "contract_versions",
              "changes", "unattributed", "has_diff", "has_report",
              "breaks_total", "breaks_open", "sev_high", "sev_medium", "sev_low"):
        if k in artifacts and k not in row:
            row[k] = artifacts[k]

    return row


def build_run_detail_v1(artifacts: dict) -> dict:
    """Build a stable DashboardRunDetailV1.  Superset of build_run_row_v1."""
    row = build_run_row_v1(artifacts)
    summary = artifacts.get("summary") or {}
    sections = _build_sections(summary, artifacts)

    detail = dict(row)
    detail["schema_version"] = "dashboard.run_detail.v1"
    detail["sections"] = sections
    detail["raw_artifact_refs"] = {"summary": artifacts.get("summary_path")}
    # backward compat detail fields
    detail["data_quality"] = summary.get("data_quality")
    detail["breaks"] = artifacts.get("breaks", [])
    detail["changes_sample"] = artifacts.get("changes_sample", [])
    detail["stage_hops"] = artifacts.get("stage_hops", [])
    tagged = artifacts.get("tagged_return_outliers") or {}
    detail["tagged_return_outliers"] = tagged.get("rows", [])
    detail["tagged_return_outlier_summary"] = tagged.get("summary", {})

    return detail
