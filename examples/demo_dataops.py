"""Demo: exercise the P0-P5 data-ops stack on synthetic data (no provider file needed).

Run:  python examples/demo_dataops.py
Opens nothing; prints a summary and writes outputs/diff/demo_diff.html — open that in a browser.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on path

import numpy as np
import pandas as pd

from core import cdc, breaks as bk, contracts, lineage, manifest as mf
from core.diff_report import write_diff_html
from core.quarantine import write_quarantine
from ingestion.symbology import Symbology


def make_raw(n=40, seed=1):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    df = pd.DataFrame({
        "as_of_date":      dates,
        "available_at":    pd.to_datetime(dates, utc=True) + pd.Timedelta(hours=3),
        "ingested_at":     pd.Timestamp("2024-06-01T00:00:00Z"),
        "timestamp":       [None] * n,
        "product_id":      254,
        "contract_root":   "B",
        "hub":             "North Sea",
        "instrument_type": "future",
        "right":           [None] * n,
        "strike":          [np.nan] * n,
        "delivery_month":  dates + pd.DateOffset(months=1),
        "expiry":          dates + pd.DateOffset(months=1),
        "price":           80 + np.cumsum(rng.normal(0, 0.5, n)),
        "net_change":      rng.normal(0, 0.3, n),
        "iv_provided":     [np.nan] * n,
        "delta_provided":  [np.nan] * n,
        "provider":        "demo",
    })
    # inject 2 bad rows: a negative price + an orphan product_id
    df.loc[5, "price"] = -1.0
    df.loc[7, "product_id"] = 999999
    return df


def main():
    raw = make_raw()
    cfg = {"family": "futures_options"}

    # ── P0: contract gate + quarantine ──
    res = contracts.validate_for_cfg(raw, cfg, symbology=Symbology())
    qsum = write_quarantine(res.quarantined, "demo", "bronze", res.report["rows_in"])
    print(f"[P0] contract gate: {res.report['rows_quarantined']} quarantined "
          f"({res.report['quarantine_rate']:.1%}) reasons={res.report['quarantine_by_reason']}")
    clean = res.passed

    # ── P2: simulate a cleaning stage (cap one outlier) then diff ──
    after = clean.copy()
    after["_outlier_flag"] = False
    idx = after.index[0]
    after.loc[idx, "price"] = after.loc[idx, "price"] - 3.0      # attributed cap
    after.loc[idx, "_outlier_flag"] = True
    if len(after) > 3:
        after.loc[after.index[3], "price"] = after.loc[after.index[3], "price"] + 5.0  # UNATTRIBUTED!

    rmap = {"adapter->validators": {"price": {"flag_col": "_outlier_flag", "reason": "outlier_cap"}}}
    recs = cdc.diff_run([("adapter", clean), ("validators", after)],
                        identity_cols=["as_of_date", "product_id"], reason_maps=rmap, run_id="demo")
    brks = bk.raise_breaks(recs, "demo")
    print(f"[P2] CDC: {len(recs)} changes, {bk.summarize(brks)} ")

    # ── P3: lineage impact + auto-purge window ──
    graph = lineage.load_lineage("futures_options")
    print(f"[P3] price contaminates: {lineage.impact_of(graph, 'price')}")
    print(f"[P3] auto-purge_bars = max_lookback = {lineage.max_lookback(graph)}")

    # ── P1: run manifest ──
    m = mf.build_manifest("demo", cfg, raw, after, symbol="254",
                          contract_report=res.report, n_trials=40, knowledge_cutoff_fallback="2024-12-31")
    print(f"[P1] manifest: code={m['code_version']} input_hash={list(m['input_data_hashes'].values())[0][:12]}…")

    # ── P5: HTML viewer ──
    html = write_diff_html(recs, brks, "demo")
    print(f"[P5] open this in a browser -> {html}")


if __name__ == "__main__":
    main()
