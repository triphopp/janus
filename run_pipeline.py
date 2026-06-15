#!/usr/bin/env python
"""Quant Pipeline Framework — entry point.

Usage:
    python run_pipeline.py --instrument bz --start 2024-01-01 --end 2024-12-31
    python run_pipeline.py --instrument spx --start 2023-01-01 --end 2024-06-30

Flow: ingestion → adapter → core[validators→stability→splitter→metrics/overfitting] → outputs
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

# ── Ingestion ──
from ingestion.cache import get_cache
from ingestion.settlement_loader import SettlementLoader
from ingestion.equity_loader_a import EquityLoaderA
from ingestion.symbology import Symbology

# ── Adapters ──
from adapters.equity_adapter import EquityAdapter
from adapters.futures_adapter import FuturesAdapter
from adapters.equity_options_adapter import EquityOptionsAdapter
from adapters.futures_options_adapter import FuturesOptionsAdapter

# ── Core ──
from core import validators, stability as stab, splitter as spl
from core import metrics, overfitting as ovf, regime, audit as aud
from core import attribution as attr
from core import reporting


def load_config(instrument_name: str) -> dict:
    """Load instrument config and merge with family defaults."""
    inst_path = Path(f"configs/instruments/{instrument_name}.yaml")
    if not inst_path.exists():
        raise FileNotFoundError(f"Instrument config not found: {inst_path}")

    with open(inst_path) as f:
        cfg = yaml.safe_load(f)

    # Merge family defaults
    family = cfg.get("family", "equity")
    family_path = Path(f"configs/{family}.yaml")
    if family_path.exists():
        with open(family_path) as f:
            defaults = yaml.safe_load(f)
        # Shallow merge — instrument overrides family
        for k, v in defaults.items():
            if k not in cfg:
                cfg[k] = v

    return cfg


def get_provider(cfg: dict):
    """Select data provider based on config."""
    provider_name = cfg.get("provider", "settlement")
    family = cfg.get("family", "equity")

    if provider_name == "settlement":
        return SettlementLoader(Symbology())
    elif provider_name == "yfinance":
        return EquityLoaderA()
    else:
        raise ValueError(f"Unknown provider: {provider_name}")


def get_adapter(cfg: dict):
    """Select adapter based on instrument family."""
    family = cfg.get("family", "equity")
    if family == "equity":
        return EquityAdapter(cfg)
    elif family == "futures":
        return FuturesAdapter(cfg)
    elif family == "equity_options":
        return EquityOptionsAdapter(cfg)
    elif family == "futures_options":
        return FuturesOptionsAdapter(cfg)
    else:
        raise ValueError(f"Unknown family: {family}")


def run_pipeline(cfg: dict, start: str, end: str, run_id: str = None):
    """Execute full pipeline: ingestion → adapter → core stages → outputs."""
    if run_id is None:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    symbol = cfg.get("symbol", {}).get("ticker") or str(cfg.get("symbol", {}).get("product_id", "unknown"))

    # ── Phase 1: Ingestion ──
    print(f"[{run_id}] Loading {symbol} from {start} to {end}...")
    provider = get_provider(cfg)
    raw_df = provider.fetch(symbol, start, end)
    print(f"  Ingestion: {len(raw_df)} rows loaded")

    # Audit: after ingestion
    snap_ingest = aud.snapshot(raw_df, "ingestion", cfg, run_id)

    # ── Phase 2: Adapter ──
    adapter = get_adapter(cfg)
    df, core_cfg = adapter.prepare(raw_df)
    print(f"  Adapter ({cfg['family']}): {len(df)} rows prepared")

    # Audit: after adapter
    snap_adapter = aud.snapshot(df, "adapter", cfg, run_id)

    # ── Stage 1: Validators ──
    df = validators.logical_bounds_check(df, core_cfg)
    df = validators.missing_completeness(df, core_cfg)
    df = validators.outlier_cap(df, core_cfg)
    print(f"  Stage 1 (validators): {df['_bound_flag'].sum()} bound flags, "
          f"{df.get('_outlier_flag', pd.Series()).sum()} outliers capped")

    # Audit: after stage 1
    snap_v1 = aud.snapshot(df, "validators", cfg, run_id)

    # ── Stage 2: Stability ──
    return_col = core_cfg.get("return_col", "return_std")
    if return_col in df.columns:
        r = df[return_col].dropna()
        adf_result = stab.adf_kpss_check(r)
        arch_result = stab.arch_lm_test(r)
        jb_result = stab.jarque_bera(r)
        hurst = stab.hurst_exponent(r)
        print(f"  Stage 2 (stability): ADF p={adf_result.get('adf_pval', 'N/A'):.4f}, "
              f"Hurst={hurst:.3f}, ARCH={'yes' if arch_result.get('has_arch_effects') else 'no'}")

    # ── Stage 3: Splitter ──
    folds = spl.walk_forward_split(df, core_cfg)
    folds = spl.purge_embargo(folds, df, core_cfg)

    # Regime labels
    regime_labels = regime.assign_regime_labels(df, core_cfg)
    diversity = spl.regime_diversity_gate(folds, regime_labels, core_cfg)
    passed_folds = diversity["pass"].sum()
    print(f"  Stage 3 (splitter): {len(folds)} folds, {passed_folds} passed diversity gate")

    # Audit: after stage 3
    snap_v3 = aud.snapshot(df, "splitter", cfg, run_id)

    # ── Stage 4: Metrics + Overfitting ──
    fold_returns = {}
    for i, (tr, va) in enumerate(folds):
        if i not in diversity[diversity["pass"]].index:
            continue
        r = df[return_col].iloc[va]
        if r.dropna().empty:
            continue
        fold_returns[i] = r

    per_fold = metrics.per_fold_breakdown(fold_returns, regime_labels)
    per_regime = metrics.per_regime_breakdown(df[return_col], regime_labels)
    stab_score = metrics.stability_score(per_fold)

    # Overfitting checks
    if return_col in df.columns:
        all_r = df[return_col].dropna()
        sr = metrics.risk_adjusted(all_r)["sharpe"] or 0.0
        n_trials = core_cfg.get("n_trials", 40)
        dsr_result = ovf.deflated_sharpe_ratio(sr, n_trials, len(all_r))
        print(f"  Stage 4 (metrics): Sharpe={sr:.3f}, DSR={dsr_result.get('dsr', 0):.3f}, "
              f"p={dsr_result.get('p_value', 1):.4f}")

    # Audit: after stage 4
    snap_v4 = aud.snapshot(df, "metrics", cfg, run_id)

    # ── Write outputs ──
    outputs_dir = Path("outputs")
    for subdir in ["perf_report", "fold_manifest", "attribution"]:
        (outputs_dir / subdir).mkdir(parents=True, exist_ok=True)

    attribution_summary = None
    if any(col in df.columns for col in ["pnl_gross", "gross_pnl", "pnl"]):
        wf = attr.waterfall(df, {**cfg, **core_cfg})
        attr.to_frame(wf).to_csv(outputs_dir / "attribution" / f"{run_id}_waterfall.csv", index=False)
        attribution_summary = wf.as_dict()

    per_fold.to_csv(outputs_dir / "perf_report" / f"{run_id}_per_fold.csv", index=False)
    per_regime.to_csv(outputs_dir / "perf_report" / f"{run_id}_per_regime.csv", index=False)
    diversity.to_csv(outputs_dir / "fold_manifest" / f"{run_id}_diversity.csv", index=False)

    # Summary
    summary = {
        "run_id": run_id,
        "instrument": symbol,
        "family": cfg["family"],
        "date_range": [start, end],
        "n_rows_raw": len(raw_df),
        "n_rows_prepared": len(df),
        "n_folds": len(folds),
        "n_folds_passed": int(passed_folds),
        "stability_score": stab_score,
        "attribution": attribution_summary,
        "audit_snapshots": [snap_ingest, snap_adapter, snap_v1, snap_v3, snap_v4],
    }

    summary["summary_report"] = reporting.write_summary_report(summary, per_fold, per_regime, diversity, outputs_dir)

    with open(outputs_dir / f"{run_id}_summary.json", "w") as f:
        json.dump({k: str(v) if isinstance(v, (pd.Timestamp, datetime)) else v
                   for k, v in summary.items()}, f, indent=2, default=str)

    print(f"\nDone. Run ID: {run_id}")
    print(f"  Folds: {len(folds)} total, {passed_folds} passed")
    print(f"  Sharpe stability: mean={stab_score.get('sharpe_mean', 0):.3f}, "
          f"min={stab_score.get('sharpe_min', 0):.3f}")
    print(f"  Profitable folds: {stab_score.get('pct_profitable_folds', 0):.0%}")
    print("  Outputs: outputs/")

    return summary


def main():
    parser = argparse.ArgumentParser(description="Quant Pipeline Framework v1.3")
    parser.add_argument("--instrument", "-i", required=True,
                        help="Instrument name (e.g. bz, spx, aapl)")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--run-id", default=None, help="Custom run ID")
    args = parser.parse_args()

    cfg = load_config(args.instrument)
    summary = run_pipeline(cfg, args.start, args.end, args.run_id)

    return 0


if __name__ == "__main__":
    sys.exit(main())
