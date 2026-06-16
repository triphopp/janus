"""Filesystem scanner — turns outputs/ artifacts into a live run index.

No DB. Every call re-globs outputs/ so new pipeline runs appear without a restart
(GitHub "repo insights" feel: the manifest is the commit, breaks are the issues).

A run row is assembled from up to four artifacts, joined on run_id:
  outputs/manifest/<rid>.json          → provenance (git SHA, hashes, knowledge cutoff)
  outputs/diff/<rid>_changes.jsonl     → CDC change + UNATTRIBUTED counts
  outputs/breaks/<rid>.jsonl           → break lifecycle counts + the break objects
  outputs/<...>_summary.json           → instrument/family/folds/stability (has its own run_id)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd

from core import cdc as _cdc

OUTPUTS = Path("outputs")
MANIFEST_DIR = OUTPUTS / "manifest"
BREAKS_DIR = OUTPUTS / "breaks"
DIFF_DIR = OUTPUTS / "diff"
RUNS_DIR = OUTPUTS / "runs"

_OPEN_STATES = {"DETECTED", "TRIAGED", "ESCALATED"}


def _blank(rid: str) -> dict:
    return {
        "run_id": rid,
        "created_at": None,
        "symbol": None,
        "instrument": None,
        "family": None,
        "code_version": None,
        "config_hash": None,
        "knowledge_cutoff": None,
        "n_trials": None,
        "n_rows": None,
        "n_folds": None,
        "n_folds_passed": None,
        "sharpe_mean": None,
        "changes": 0,
        "unattributed": 0,
        "breaks_total": 0,
        "breaks_open": 0,
        "sev_high": 0,
        "sev_medium": 0,
        "sev_low": 0,
        "has_diff": False,
        "adjustment_factor_rows": 0,
        "adjustment_warning_rows": 0,
        "adjustment_policy": None,
        "adjustment_status": "not_applicable",
        "adjustment_max_abs_price_diff": None,
        "price_adjustments": None,
    }


def _read_json(p: Path) -> Optional[dict]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _iter_jsonl(p: Path):
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                yield json.loads(line)
    except Exception:
        return


def scan_runs() -> list[dict]:
    """Return run rows, newest first (by created_at, then run_id)."""
    runs: dict[str, dict] = {}

    # 1. manifests (provenance backbone)
    if MANIFEST_DIR.exists():
        for mp in MANIFEST_DIR.glob("*.json"):
            m = _read_json(mp)
            if not m:
                continue
            rid = m.get("run_id") or mp.stem
            r = runs.setdefault(rid, _blank(rid))
            r.update({
                "run_id": rid,
                "created_at": m.get("created_at"),
                "symbol": m.get("symbol"),
                "code_version": m.get("code_version"),
                "config_hash": m.get("config_hash"),
                "knowledge_cutoff": m.get("knowledge_time_cutoff"),
                "n_trials": m.get("n_trials"),
                "env": m.get("env"),
                "input_data_hashes": m.get("input_data_hashes"),
                "output_data_hashes": m.get("output_data_hashes"),
                "contract_versions": m.get("contract_versions"),
            })

    # 2. CDC change ledgers
    if DIFF_DIR.exists():
        for cp in DIFF_DIR.glob("*_changes.jsonl"):
            rid = cp.name[: -len("_changes.jsonl")]
            r = runs.setdefault(rid, _blank(rid))
            ch = un = 0
            for d in _iter_jsonl(cp):
                ch += 1
                if d.get("reason") == _cdc.UNATTRIBUTED:
                    un += 1
            r["changes"] = ch
            r["unattributed"] = un
            r["has_diff"] = (DIFF_DIR / f"{rid}_diff.html").exists()

    # 3. break ledgers
    if BREAKS_DIR.exists():
        for bp in BREAKS_DIR.glob("*.jsonl"):
            rid = bp.stem
            r = runs.setdefault(rid, _blank(rid))
            total = open_ = h = mday = low = 0
            for b in _iter_jsonl(bp):
                total += 1
                if b.get("status") in _OPEN_STATES:
                    open_ += 1
                sev = b.get("severity")
                if sev == "high":
                    h += 1
                elif sev == "medium":
                    mday += 1
                elif sev == "low":
                    low += 1
            r.update(breaks_total=total, breaks_open=open_,
                     sev_high=h, sev_medium=mday, sev_low=low)

    # 4. run summaries (instrument/family/stability) — keyed by their own run_id.
    # Two layouts: legacy top-level outputs/<rid>_summary.json AND the current
    # per-run outputs/runs/<rid>__.../summary.json. Glob both or Sharpe shows blank.
    summary_paths = list(OUTPUTS.glob("*_summary.json")) + list(OUTPUTS.glob("runs/*/summary.json"))
    for sp in summary_paths:
        s = _read_json(sp)
        if not s:
            continue
        rid = s.get("run_id") or sp.parent.name.split("__")[0]
        r = runs.setdefault(rid, _blank(rid))
        r["instrument"] = s.get("instrument")
        r["family"] = s.get("family")
        r["n_rows"] = s.get("n_rows_prepared") or s.get("n_rows_raw")
        r["n_folds"] = s.get("n_folds")
        r["n_folds_passed"] = s.get("n_folds_passed")
        ss = s.get("stability_score") or {}
        r["sharpe_mean"] = ss.get("sharpe_mean")
        _apply_price_adjustments(r, s.get("price_adjustments"))
        if r.get("created_at") is None:
            r["created_at"] = s.get("created_at")

    # Older runs do not have price_adjustments in summary.json. Read only the
    # needed prepared.csv columns so the dashboard still exposes adjustment drift.
    for rid, r in runs.items():
        if r.get("price_adjustments") is None:
            _apply_price_adjustments(r, _load_price_adjustments_from_prepared(rid))

    rows = list(runs.values())
    rows.sort(key=lambda x: (x.get("created_at") or "", x["run_id"]), reverse=True)
    return rows


def run_detail(run_id: str) -> Optional[dict]:
    for r in scan_runs():
        if r["run_id"] == run_id:
            r = dict(r)
            r["breaks"] = load_breaks(run_id)
            r["changes_sample"] = _changes_sample(run_id)
            return r
    return None


def _changes_sample(run_id: str, limit: int = 200) -> list[dict]:
    cp = DIFF_DIR / f"{run_id}_changes.jsonl"
    out = []
    for d in _iter_jsonl(cp):
        out.append(d)
        if len(out) >= limit:
            break
    return out


def load_breaks(run_id: str) -> list[dict]:
    return list(_iter_jsonl(BREAKS_DIR / f"{run_id}.jsonl"))


def breaks_path(run_id: str) -> Path:
    return BREAKS_DIR / f"{run_id}.jsonl"


def all_breaks() -> list[dict]:
    """Every break across every run, newest detection first."""
    out: list[dict] = []
    if BREAKS_DIR.exists():
        for bp in BREAKS_DIR.glob("*.jsonl"):
            out.extend(_iter_jsonl(bp))
    out.sort(key=lambda b: b.get("detected_at") or "", reverse=True)
    return out


def break_trend() -> list[dict]:
    """Break count per detection day, split by severity — feeds the timeline chart."""
    by_day: dict[str, dict] = {}
    for b in all_breaks():
        day = (b.get("detected_at") or "")[:10]
        if not day:
            continue
        slot = by_day.setdefault(day, {"day": day, "high": 0, "medium": 0, "low": 0, "total": 0})
        sev = b.get("severity")
        if sev in ("high", "medium", "low"):
            slot[sev] += 1
        slot["total"] += 1
    return [by_day[k] for k in sorted(by_day)]


def find_run_dir(run_id: str) -> Optional[Path]:
    if not RUNS_DIR.exists():
        return None
    hits = sorted(RUNS_DIR.glob(f"{run_id}__*"))
    return hits[0] if hits else None


def load_prepared(run_id: str) -> Optional[pd.DataFrame]:
    """Load a run's prepared output frame (the post-pipeline dataset)."""
    d = find_run_dir(run_id)
    if d is None:
        return None
    p = d / "data" / "prepared.csv"
    if not p.exists():
        return None
    return pd.read_csv(p)


def _apply_price_adjustments(row: dict, summary: Optional[dict]) -> None:
    if not summary:
        return
    row["price_adjustments"] = summary
    row["adjustment_factor_rows"] = int(summary.get("factor_rows") or 0)
    row["adjustment_warning_rows"] = int(summary.get("warning_rows") or 0)
    row["adjustment_policy"] = summary.get("policy")
    row["adjustment_status"] = summary.get("status")
    row["adjustment_max_abs_price_diff"] = summary.get("max_abs_price_std_vs_provider_adjusted")


def _bool_series(values: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(values):
        return values.fillna(False)
    return values.map(
        lambda v: str(v).strip().lower() in {"1", "true", "t", "yes", "y"}
    ).fillna(False)


def _load_price_adjustments_from_prepared(run_id: str) -> Optional[dict]:
    d = find_run_dir(run_id)
    if d is None:
        return None
    p = d / "data" / "prepared.csv"
    if not p.exists():
        return None

    wanted = {
        "adj_factor",
        "adj_factor_is_pit",
        "price_adjustment_warning",
        "adjusted_price_provider",
        "price_std",
    }
    try:
        df = pd.read_csv(p, usecols=lambda c: c in wanted)
    except Exception:
        return None
    if "adj_factor" not in df.columns:
        return None

    factors = pd.to_numeric(df["adj_factor"], errors="coerce")
    factor_rows = factors.notna() & ((factors - 1.0).abs() > 1e-12)
    if "price_adjustment_warning" in df.columns:
        warnings = _bool_series(df["price_adjustment_warning"])
    else:
        warnings = pd.Series(False, index=df.index)
    if "adj_factor_is_pit" in df.columns:
        pit_rows = _bool_series(df["adj_factor_is_pit"]) & factor_rows
    else:
        pit_rows = pd.Series(False, index=df.index)

    diff = pd.Series(dtype=float)
    if {"adjusted_price_provider", "price_std"}.issubset(df.columns):
        diff = (
            pd.to_numeric(df["adjusted_price_provider"], errors="coerce")
            - pd.to_numeric(df["price_std"], errors="coerce")
        ).abs()

    warning_rows = int(warnings.sum())
    factor_row_count = int(factor_rows.sum())
    return {
        "status": "warning" if warning_rows else ("pass" if factor_row_count else "not_applicable"),
        "policy": "retro_adjustment_blocked" if warning_rows else (
            "provider_factor_observed" if factor_row_count else "no_provider_adjustments"
        ),
        "rows": int(len(df)),
        "factor_rows": factor_row_count,
        "warning_rows": warning_rows,
        "pit_factor_rows": int(pit_rows.sum()),
        "adj_factor_min": float(factors.min()) if factors.notna().any() else None,
        "adj_factor_max": float(factors.max()) if factors.notna().any() else None,
        "max_abs_price_std_vs_provider_adjusted": float(diff.max()) if not diff.empty else None,
        "mean_abs_price_std_vs_provider_adjusted": float(diff.mean()) if not diff.empty else None,
    }


def compare_runs(run_a: str, run_b: str, *,
                 identity_cols=("as_of_date", "symbol"),
                 tol: float = 1e-9, limit: int = 1000) -> dict:
    """Cross-run vintage diff: what changed in the DATA between two runs (A=before, B=after).

    This is the 'did the source data get revised since last import' view — distinct from the
    within-run adapter->validators diff. Reuses cdc.diff_frames on the prepared frames.
    """
    da = load_prepared(run_a)
    db = load_prepared(run_b)
    if da is None or db is None:
        missing = run_a if da is None else run_b
        return {"error": f"prepared.csv not found for run {missing}"}

    ident = [c for c in identity_cols if c in da.columns and c in db.columns]
    recs = _cdc.diff_frames(
        da, db, f"run:{run_a}", f"run:{run_b}",
        identity_cols=ident or None,
        tol={c: {"atol": tol} for c in da.columns if da[c].dtype.kind == "f"},
    )
    by_type: dict[str, int] = {}
    for r in recs:
        by_type[r.change_type] = by_type.get(r.change_type, 0) + 1
    cell = [r.to_dict() for r in recs if r.change_type == "cell_mod"]
    rowadd = [r.to_dict() for r in recs if r.change_type == "row_add"]
    rowdrop = [r.to_dict() for r in recs if r.change_type == "row_drop"]
    schema = [r.to_dict() for r in recs if r.change_type in ("schema_add", "schema_drop")]
    return {
        "run_a": run_a, "run_b": run_b,
        "rows_a": int(len(da)), "rows_b": int(len(db)),
        "identity": ident,
        "n_changes": len(recs),
        "by_type": by_type,
        "cell_mods": cell[:limit],
        "row_adds": rowadd[:limit],
        "row_drops": rowdrop[:limit],
        "schema": schema,
    }


def fleet_summary(rows: list[dict]) -> dict:
    return {
        "n_runs": len(rows),
        "total_changes": sum(r["changes"] for r in rows),
        "total_unattributed": sum(r["unattributed"] for r in rows),
        "total_adjustment_warnings": sum(r["adjustment_warning_rows"] for r in rows),
        "breaks_total": sum(r["breaks_total"] for r in rows),
        "breaks_open": sum(r["breaks_open"] for r in rows),
        "sev_high": sum(r["sev_high"] for r in rows),
    }
