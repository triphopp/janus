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
        "date_range": None,
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
        "metrics_input": None,
        "strategy_metrics_available": None,
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
        "has_report": False,
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
    # Three layouts supported:
    #   legacy:   outputs/<rid>_summary.json
    #   old flat: outputs/runs/<rid>__.../summary.json
    #   new:      outputs/runs/<SYMBOL>/<rid>/summary.json
    summary_paths = (
        list(OUTPUTS.glob("*_summary.json"))
        + list(OUTPUTS.glob("runs/*/summary.json"))       # old flat: runs/{rid}__*/
        + list(OUTPUTS.glob("runs/*/*/summary.json"))     # new:      runs/{symbol}/{rid}/
    )
    for sp in summary_paths:
        s = _read_json(sp)
        if not s:
            continue
        rid = s.get("run_id") or sp.parent.name.split("__")[0]
        r = runs.setdefault(rid, _blank(rid))
        r["instrument"] = s.get("instrument")
        r["family"] = s.get("family")
        r["date_range"] = s.get("date_range")
        r["n_rows"] = s.get("n_rows_prepared") or s.get("n_rows_raw")
        r["n_folds"] = s.get("n_folds")
        r["n_folds_passed"] = s.get("n_folds_passed")
        r["metrics_input"] = s.get("metrics_input")
        r["strategy_metrics_available"] = s.get("strategy_metrics_available")
        ss = s.get("stability_score") or {}
        r["sharpe_mean"] = ss.get("sharpe_mean")
        _apply_price_adjustments(r, s.get("price_adjustments"))
        if r.get("created_at") is None:
            r["created_at"] = s.get("created_at")

    # Older runs do not have price_adjustments in summary.json. Read only the
    # needed prepared.csv columns so the dashboard still exposes adjustment drift.
    # Also mark runs that have a final_report.html.
    for rid, r in runs.items():
        if r.get("price_adjustments") is None:
            _apply_price_adjustments(r, _load_price_adjustments_from_prepared(rid))
        d = find_run_dir(rid)
        if d is not None:
            r["has_report"] = (d / "report" / "final_report.html").exists()

    rows = list(runs.values())
    rows.sort(key=lambda x: (x.get("created_at") or "", x["run_id"]), reverse=True)
    return rows


def run_detail(run_id: str) -> Optional[dict]:
    for r in scan_runs():
        if r["run_id"] == run_id:
            r = dict(r)
            r["breaks"] = load_breaks(run_id)
            r["changes_sample"] = _changes_sample(run_id)
            r["stage_hops"] = _stage_hops(run_id)
            tagged = load_tagged_return_outliers(run_id)
            r["tagged_return_outliers"] = tagged["rows"]
            r["tagged_return_outlier_summary"] = tagged["summary"]
            return r
    return None


def _stage_hops(run_id: str) -> list[dict]:
    """Per stage-transition rollup over the FULL ledger — feeds the dashboard stage strip.

    One entry per consecutive hop (ingestion→adapter, adapter→validators, ...) in first-
    seen order, so the UI can show which step each change came from.
    """
    order: list[str] = []
    by_hop: dict[str, dict] = {}
    for d in _iter_jsonl(DIFF_DIR / f"{run_id}_changes.jsonl"):
        hop = f"{d.get('stage_from')}->{d.get('stage_to')}"
        slot = by_hop.get(hop)
        if slot is None:
            slot = {"stage_from": d.get("stage_from"), "stage_to": d.get("stage_to"),
                    "changes": 0, "cell_mod": 0, "schema_add": 0, "schema_drop": 0,
                    "row_add": 0, "row_drop": 0, "unattributed": 0}
            by_hop[hop] = slot
            order.append(hop)
        slot["changes"] += 1
        ct = d.get("change_type")
        if ct in slot:
            slot[ct] += 1
        if d.get("reason") == _cdc.UNATTRIBUTED:
            slot["unattributed"] += 1
    return [by_hop[h] for h in order]


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
    exact = RUNS_DIR / run_id
    if exact.is_dir():
        return exact

    # New layout: outputs/runs/{SYMBOL}/{run_id}
    hits = sorted(path for path in RUNS_DIR.glob(f"*/{run_id}") if path.is_dir())
    if hits:
        return hits[0]

    # Legacy flat layout: outputs/runs/{run_id}__{SYMBOL}__...
    hits = sorted(path for path in RUNS_DIR.glob(f"{run_id}__*") if path.is_dir())
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


def load_raw_row(run_id: str, symbol: str, as_of_date: str) -> Optional[dict]:
    """Return raw-source fields for one (symbol, date) row from prepared data.

    Prefer parquet (faster) over CSV. Date matching uses date-part only so
    timezone offsets in the CDC key (e.g. 2022-05-18T00:00:00-04:00) don't
    cause a miss.
    """
    import math

    d = find_run_dir(run_id)
    if d is None:
        return None
    pq = d / "data" / "prepared.parquet"
    csv = d / "data" / "prepared.csv"
    if pq.exists():
        df = pd.read_parquet(pq)
    elif csv.exists():
        df = pd.read_csv(csv)
    else:
        return None

    if "as_of_date" not in df.columns or "symbol" not in df.columns:
        return None

    date_part = str(as_of_date)[:10]
    df["_date_str"] = pd.to_datetime(df["as_of_date"], utc=True).dt.date.astype(str)
    mask = (df["symbol"].astype(str) == symbol) & (df["_date_str"] == date_part)
    rows = df[mask]
    if rows.empty:
        return None

    raw = rows.iloc[0]
    wanted = [
        "raw_close", "raw_close_unadj", "adj_factor", "adj_factor_source",
        "adj_factor_is_pit", "provider", "price_std", "adjusted_price_provider",
        "price_adjustment_warning", "return_raw", "return_std", "return_winsorized",
        "_return_outlier_flag", "_return_outlier_reason", "_return_outlier_policy",
        "_return_outlier_evidence", "_return_clip_lower", "_return_clip_upper",
        "_return_validation_status",
        "_bound_flag", "_bound_reason", "_outlier_flag",
    ]
    out: dict = {}
    for f in wanted:
        if f not in raw.index:
            continue
        v = raw[f]
        if hasattr(v, "item"):
            v = v.item()
        try:
            if math.isnan(v) or math.isinf(v):
                v = None
        except (TypeError, ValueError):
            pass
        if v is pd.NA or (isinstance(v, float) and v != v):
            v = None
        out[f] = v
    return out


def load_tagged_return_outliers(run_id: str, limit: int = 200) -> dict:
    """Return rows with return outlier tags for reviewer follow-up."""
    import math

    d = find_run_dir(run_id)
    if d is None:
        return {"summary": {"total": 0, "shown": 0}, "rows": []}
    pq = d / "data" / "prepared.parquet"
    csv = d / "data" / "prepared.csv"
    if pq.exists():
        try:
            df = pd.read_parquet(pq)
        except Exception:
            return {"summary": {"total": 0, "shown": 0}, "rows": []}
    elif csv.exists():
        wanted = {
            "as_of_date", "symbol", "raw_close", "price_std",
            "return_raw", "return_std", "return_winsorized",
            "_return_outlier_flag", "_return_outlier_reason", "_return_outlier_policy",
            "_return_outlier_evidence", "_return_clip_lower", "_return_clip_upper",
            "_return_validation_status",
        }
        try:
            df = pd.read_csv(csv, usecols=lambda c: c in wanted)
        except Exception:
            return {"summary": {"total": 0, "shown": 0}, "rows": []}
    else:
        return {"summary": {"total": 0, "shown": 0}, "rows": []}

    if "_return_outlier_flag" not in df.columns:
        return {"summary": {"total": 0, "shown": 0}, "rows": []}

    mask = _bool_series(df["_return_outlier_flag"])
    tagged = df[mask].copy()
    total = int(len(tagged))
    if total == 0:
        return {"summary": {"total": 0, "shown": 0}, "rows": []}

    if "return_raw" in tagged.columns:
        tagged["_abs_return_sort"] = pd.to_numeric(tagged["return_raw"], errors="coerce").abs()
        tagged = tagged.sort_values("_abs_return_sort", ascending=False, na_position="last")

    def _counts(col: str) -> dict:
        if col not in tagged.columns:
            return {}
        return {
            str(k): int(v)
            for k, v in tagged[col].fillna("").astype(str).value_counts().items()
            if str(k)
        }

    fields = [
        "as_of_date", "symbol", "raw_close", "price_std",
        "return_raw", "return_std", "return_winsorized",
        "_return_outlier_reason", "_return_outlier_policy", "_return_outlier_evidence",
        "_return_clip_lower", "_return_clip_upper", "_return_validation_status",
    ]
    rows = []
    for _, raw in tagged.head(limit).iterrows():
        out = {}
        for f in fields:
            if f not in raw.index:
                continue
            v = raw[f]
            if hasattr(v, "item"):
                v = v.item()
            if f == "as_of_date" and v is not None:
                v = str(v)
            try:
                if math.isnan(v) or math.isinf(v):
                    v = None
            except (TypeError, ValueError):
                pass
            if v is pd.NA or (isinstance(v, float) and v != v):
                v = None
            out[f] = v
        rows.append(out)

    return {
        "summary": {
            "total": total,
            "shown": len(rows),
            "by_policy": _counts("_return_outlier_policy"),
            "by_status": _counts("_return_validation_status"),
            "by_reason": _counts("_return_outlier_reason"),
        },
        "rows": rows,
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
