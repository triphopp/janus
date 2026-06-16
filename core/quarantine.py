"""Quarantine — holding pen for contract-failing rows (P0).

Contract-failing rows are diverted here instead of flowing downstream into a
backtest. Nothing re-enters silently; re-admission is an explicit analyst action.
See: Memory/plans/data_ops_architecture.md §1 (medallion + quarantine), §13.2
(quarantine must be visible to avoid silent sample bias).

Layout:
    quarantine/<run_id>/<tier>.parquet   # the diverted rows + _quarantine_reason
    quarantine/<run_id>/<tier>.summary.json
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from core.contracts import QUARANTINE_REASON_COL

DEFAULT_QUARANTINE_DIR = Path("quarantine")


def _reason_counts(df: pd.DataFrame) -> dict:
    if QUARANTINE_REASON_COL not in df.columns:
        return {}
    counts: dict = {}
    for raw in df[QUARANTINE_REASON_COL]:
        for r in str(raw).split(";"):
            if r:
                counts[r] = counts.get(r, 0) + 1
    return counts


def _breakdown(df: pd.DataFrame, col: str, limit: int = 50) -> dict:
    """Quarantine count by a dimension (date/symbol/regime) — §13.2 visibility."""
    if col not in df.columns:
        return {}
    series = df[col]
    if pd.api.types.is_datetime64_any_dtype(series):
        series = series.dt.date.astype(str)
    vc = series.astype(str).value_counts().head(limit)
    return {str(k): int(v) for k, v in vc.items()}


def write_quarantine(
    quarantined: pd.DataFrame,
    run_id: str,
    tier: str,
    rows_in: int,
    out_dir: Path | str = DEFAULT_QUARANTINE_DIR,
    breakdown_cols: Optional[list[str]] = None,
) -> dict:
    """Persist diverted rows + a summary. Returns the summary dict.

    Always returns a summary (with quarantine_rate) even when nothing was diverted,
    so every run can surface its quarantine rate (§13.12 acceptance criterion).
    """
    rows_q = int(len(quarantined))
    summary = {
        "run_id": run_id,
        "tier": tier,
        "written_at": datetime.now(timezone.utc).isoformat(),
        "rows_in": int(rows_in),
        "rows_quarantined": rows_q,
        "quarantine_rate": (rows_q / rows_in) if rows_in else 0.0,
        "by_reason": _reason_counts(quarantined),
        "parquet": None,
        "csv": None,
    }

    if rows_q:
        cols = breakdown_cols or ["as_of_date", "product_id", "symbol", "vol_regime"]
        summary["by_dimension"] = {
            c: _breakdown(quarantined, c) for c in cols if c in quarantined.columns
        }

        run_dir = Path(out_dir) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = run_dir / f"{tier}.parquet"
        csv_path = run_dir / f"{tier}.csv"
        try:
            quarantined.to_parquet(parquet_path, index=False)
            summary["parquet"] = str(parquet_path)
        except Exception:
            summary["parquet"] = None
        quarantined.to_csv(csv_path, index=False)
        summary["csv"] = str(csv_path)

        with open(run_dir / f"{tier}.summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, default=str)

    return summary
