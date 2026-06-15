"""Data audit — lightweight before/after snapshot per stage.

v1.3: Snapshot row_count, schema_hash, data_hash, key_stats, na_pattern.
For debugging where values drift. NOT a full MLOps observability stack.
Cost must stay < 5% of pipeline runtime.
"""

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

try:
    import xxhash  # xxh64 - fast, deterministic
except ImportError:  # pragma: no cover - environment fallback
    xxhash = None


def _digest(value: str) -> str:
    """Stable content digest. Prefer xxh64, fallback when dependency is absent."""
    if xxhash is not None:
        return xxhash.xxh64(value).hexdigest()
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_schema(df: pd.DataFrame) -> str:
    """Hash of (col_name, dtype) pairs — catches schema drift."""
    cols = sorted([(c, str(df[c].dtype)) for c in df.columns])
    return _digest(json.dumps(cols, sort_keys=True))


def hash_subset(df: pd.DataFrame) -> str:
    """xxh64 of snapshot columns — deterministic content check."""
    # Hash row-wise concatenation of stringified values
    data_str = df.to_csv(index=False, header=True)
    return _digest(data_str)


def _stats(series: pd.Series) -> dict:
    """Quick stats for a column: min, max, mean, null_count."""
    s = series.dropna()
    stats = {"null_count": int(series.isna().sum())}

    if len(s) == 0:
        stats.update({"min": None, "max": None, "mean": None})
    elif pd.api.types.is_numeric_dtype(series):
        stats.update({"min": float(s.min()), "max": float(s.max()), "mean": float(s.mean())})
    elif pd.api.types.is_datetime64_any_dtype(series):
        stats.update({"min": s.min().isoformat(), "max": s.max().isoformat(), "mean": None})
    else:
        stats.update({"min": str(s.min()), "max": str(s.max()), "mean": None})

    return stats


def _is_numeric_value(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def snapshot(df: pd.DataFrame, stage: str, cfg: dict, run_id: Optional[str] = None) -> dict:
    """Take a lightweight snapshot at input or output of a pipeline stage.

    Call at both input and output of each stage.
    Writes JSON line to outputs/audit/<run_id>.jsonl.

    Args:
        df: DataFrame to snapshot
        stage: stage name (ingestion/adapter/validators/splitter/metrics)
        cfg: pipeline config with 'audit' block:
            - snapshot_cols: list of columns to track
            - hash_algo: 'xxh64' (only one supported)
        run_id: unique run identifier

    Returns:
        dict with snapshot data
    """
    audit_cfg = cfg.get("audit", {})
    cols = audit_cfg.get("snapshot_cols", list(df.columns[:5]))
    cols = [c for c in cols if c in df.columns]

    snap = {
        "stage": stage,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "row_count": len(df),
        "schema_hash": hash_schema(df),
        "data_hash": hash_subset(df[cols]) if cols else "",
        "key_stats": {c: _stats(df[c]) for c in cols if c in df.columns},
        "na_pattern": df.isna().sum().to_dict(),
    }

    # Write to audit file
    if audit_cfg.get("enabled", True):
        rid = run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        audit_dir = Path("outputs/audit")
        audit_dir.mkdir(parents=True, exist_ok=True)
        path = audit_dir / f"{rid}.jsonl"
        with open(path, "a") as f:
            f.write(json.dumps(snap) + "\n")

    return snap


def diff_stages(before: dict, after: dict) -> dict:
    """Compare two snapshots — find what changed between stages.

    Used via CLI: quant_audit diff --before ingestion --after adapter

    Args:
        before: snapshot dict from earlier stage
        after: snapshot dict from later stage

    Returns:
        dict with: row_delta, schema_changed, new_nans, key_stat_deltas
    """
    diff = {
        "stage_from": before["stage"],
        "stage_to": after["stage"],
        "row_delta": after["row_count"] - before["row_count"],
        "schema_changed": after["schema_hash"] != before["schema_hash"],
    }

    # New NaN that appeared in this stage
    new_nans = {}
    for col, count in after["na_pattern"].items():
        before_count = before["na_pattern"].get(col, 0)
        if count > before_count:
            new_nans[col] = count - before_count
    diff["new_nans"] = new_nans

    # Key stat changes
    stat_deltas = {}
    for col in after.get("key_stats", {}):
        if col in before.get("key_stats", {}):
            bs = before["key_stats"][col]
            at = after["key_stats"][col]
            deltas = {}
            for metric in ["mean", "min", "max", "null_count"]:
                before_value = bs.get(metric)
                after_value = at.get(metric)
                if _is_numeric_value(before_value) and _is_numeric_value(after_value):
                    deltas[metric] = after_value - before_value
            if any(v != 0 for v in deltas.values()):
                stat_deltas[col] = deltas
    diff["key_stat_deltas"] = stat_deltas

    return diff
