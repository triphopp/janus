#!/usr/bin/env python
"""Quant Pipeline Framework — entry point.

Usage:
    python run_pipeline.py --instrument bz --start 2024-01-01 --end 2024-12-31
    python run_pipeline.py --instrument spx --start 2023-01-01 --end 2024-06-30

Flow: ingestion → adapter → core[validators→stability→splitter→metrics/overfitting] → outputs
"""

import argparse
import copy
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

# ── Ingestion ──
from ingestion.settlement_loader import SettlementLoader
from ingestion.equity_loader_a import EquityLoaderA
from ingestion.symbology import Symbology
from ingestion.versioned_cache import get_versioned_cache

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
from core import contracts as contracts_mod
from core import manifest as manifest_mod
from core import cdc
from core import breaks as breaks_mod
from core import coverage as coverage_mod
from core import lineage
from core import diff_report
from core.quarantine import write_quarantine
from core.causal import validate_pit_timing
from core.config import normalize_config


def load_config(instrument_name: str) -> dict:
    """Load instrument config and merge with family defaults.

    If no YAML exists for the name, treat it as an equity ticker and synthesize a default
    equity config from configs/equity.yaml — so `-i NVDA` works directly without a hand-
    written instrument file (no more "borrow aapl.yaml + --ticker NVDA" awkwardness).
    """
    inst_path = Path(f"configs/instruments/{instrument_name}.yaml")
    if inst_path.exists():
        with open(inst_path) as f:
            cfg = yaml.safe_load(f)
    else:
        cfg = {
            "family": "equity",
            "provider": "yfinance",
            "symbol": {"ticker": instrument_name.upper()},
            "_synthesized": True,
        }

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

    return normalize_config(cfg)


def apply_runtime_overrides(cfg: dict, ticker: str | None = None) -> dict:
    """Apply CLI overrides that should not require a dedicated YAML file."""
    out = copy.deepcopy(cfg)
    if ticker:
        family = out.get("family", "equity")
        if family != "equity":
            raise ValueError("--ticker override is only supported for equity instruments")
        out.setdefault("symbol", {})["ticker"] = ticker.upper()
        out.setdefault("runtime_overrides", {})["ticker"] = ticker.upper()
    return out


def get_provider(cfg: dict):
    """Select data provider by (provider, family).

    The provider must emit the shape the family's adapter expects: bar providers feed
    equity/futures; chain providers feed *_options. Mismatches are rejected here with a
    clear message instead of crashing later inside the adapter.
    """
    provider_name = cfg.get("provider", "settlement")
    family = cfg.get("family", "equity")
    options_family = family.endswith("_options")

    if provider_name == "settlement":
        # settlement files carry both futures + option rows
        return SettlementLoader(Symbology())
    elif provider_name == "yfinance":
        if options_family:
            from ingestion.equity_options_loader_yf import EquityOptionsLoaderYF
            return EquityOptionsLoaderYF(max_expiries=cfg.get("max_expiries"))
        return EquityLoaderA()
    else:
        raise ValueError(
            f"Unknown provider: {provider_name!r} (known: settlement, yfinance)"
        )


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


def _stability_series(df: pd.DataFrame, value_col: str, date_col: str = "as_of_date", agg: str = "mean") -> pd.Series:
    """Build a date-grain stability series without arbitrary first-row dedupe."""
    if value_col not in df.columns:
        return pd.Series(dtype=float)

    values = pd.to_numeric(df[value_col], errors="coerce")
    if date_col not in df.columns:
        return values.dropna()

    tmp = pd.DataFrame({
        date_col: pd.to_datetime(df[date_col]),
        value_col: values,
    }).dropna()
    if tmp.empty:
        return pd.Series(dtype=float)

    grouped = tmp.groupby(date_col)[value_col]
    if agg == "median":
        return grouped.median().sort_index()
    return grouped.mean().sort_index()


def _fmt_float(value, digits: int = 4) -> str:
    if value is None:
        return "N/A"
    try:
        if pd.isna(value):
            return "N/A"
    except TypeError:
        pass
    return f"{float(value):.{digits}f}"


def _safe_run_id(run_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(run_id)).strip("._")
    if not safe:
        raise ValueError("run_id must contain at least one safe filename character")
    return safe


def _strategy_return_col(df: pd.DataFrame, cfg: dict) -> str | None:
    candidates = cfg.get("strategy_return_cols", ["return_net", "strategy_return", "pnl_return"])
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _pit_guard_status(df: pd.DataFrame) -> dict:
    required = {"as_of_date", "available_at", "decision_time"}
    if not required.issubset(df.columns):
        return {
            "status": "not_checked",
            "reason": "missing decision_time or availability columns",
            "required_columns": sorted(required),
        }

    try:
        validate_pit_timing(
            df,
            execution_col="execution_time" if "execution_time" in df.columns else None,
            label_end_col="label_end_time" if "label_end_time" in df.columns else None,
        )
    except ValueError as exc:
        return {"status": "fail", "reason": str(exc)}
    return {"status": "pass"}


def _cache_guard_status(cfg: dict, cache_mode: str) -> dict:
    if cache_mode != "versioned_cache":
        return {
            "status": "fail",
            "reason": "pipeline read directly from provider/source instead of VersionedCache",
        }
    version = cfg.get("data_version", "latest")
    if version == "latest":
        return {
            "status": "fail",
            "reason": "VersionedCache is enabled but data_version is mutable latest",
        }
    return {"status": "pass", "data_version": str(version)}


def _enforce_cache_guard(cache_guard: dict, cfg: dict) -> None:
    """Require pinned raw inputs unless the run explicitly opts out."""
    if not bool(cfg.get("require_fixed_data_version", True)):
        return
    if cache_guard.get("status") == "fail":
        raise ValueError(
            "Fixed versioned raw data is required for backtest-grade runs: "
            f"{cache_guard.get('reason')}. "
            "Set require_fixed_data_version: false or pass --allow-unversioned-data "
            "only for exploratory provider-fetch diagnostics."
        )


def _bool_series(values: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(values):
        return values.fillna(False)
    return values.map(
        lambda v: str(v).strip().lower() in {"1", "true", "t", "yes", "y"}
    ).fillna(False)


def _split_adjustment_summary(df: pd.DataFrame) -> dict:
    """Summarize provider SPLIT adjustment baked into raw_close.

    yfinance Close is split-adjusted, so adj_factor (dividend-only) is blind to splits.
    split_factor != 1.0 means the provider retroactively divided this row's price by a
    future split — a price-LEVEL look-ahead that the dividend-only guard cannot see.
    """
    if "split_factor" not in df.columns:
        return {"status": "not_checked", "reason": "split_factor column not present"}
    factor = pd.to_numeric(df["split_factor"], errors="coerce")
    affected = factor.notna() & ((factor - 1.0).abs() > 1e-9)
    split_events = 0
    if "split_ratio" in df.columns:
        ratio = pd.to_numeric(df["split_ratio"], errors="coerce")
        split_events = int((ratio.notna() & ((ratio - 1.0).abs() > 1e-9)).sum())
    n_affected = int(affected.sum())
    return {
        # warning: raw_close is retroactively split-adjusted for these rows
        "status": "warning" if n_affected else "not_applicable",
        "policy": "provider_split_back_adjusted" if n_affected else "no_provider_splits",
        "split_events": split_events,
        "rows_split_adjusted": n_affected,
        "split_factor_min": float(factor.min()) if factor.notna().any() else None,
        "split_factor_max": float(factor.max()) if factor.notna().any() else None,
        "note": (
            "raw_close is split-adjusted (returns-correct); use raw_close_unadj for "
            "price levels"
        ) if n_affected else None,
    }


def _price_adjustment_summary(df: pd.DataFrame, cfg: dict) -> dict:
    """Summarize provider adjustment factors and whether they affected price_std."""
    if "adj_factor" not in df.columns:
        return {"status": "not_applicable", "reason": "adj_factor column not present"}

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
    use_retro = bool(cfg.get("allow_retro_adjusted_prices", False))
    if warning_rows:
        status = "warning"
        policy = "retro_adjustment_blocked"
    elif factor_row_count:
        status = "pass"
        policy = "adjustments_applied" if (use_retro or int(pit_rows.sum())) else "provider_factor_observed"
    else:
        status = "not_applicable"
        policy = "no_provider_adjustments"

    return {
        "status": status,
        "policy": policy,
        "rows": int(len(df)),
        "factor_rows": factor_row_count,
        "warning_rows": warning_rows,
        "pit_factor_rows": int(pit_rows.sum()),
        "allow_retro_adjusted_prices": use_retro,
        "adj_factor_min": float(factors.min()) if factors.notna().any() else None,
        "adj_factor_max": float(factors.max()) if factors.notna().any() else None,
        "max_abs_price_std_vs_provider_adjusted": float(diff.max()) if not diff.empty else None,
        "mean_abs_price_std_vs_provider_adjusted": float(diff.mean()) if not diff.empty else None,
    }


def _assert_validation_folds(folds: list, df: pd.DataFrame, cfg: dict) -> None:
    """Fail closed when purge/embargo leaves no validation data."""
    if folds:
        return

    date_col = cfg.get("date_col", "as_of_date")
    unique_dates = None
    if date_col in df.columns:
        unique_dates = int(pd.to_datetime(df[date_col], errors="coerce").nunique())
    purge_bars = cfg.get("purge_bars", 5)
    if purge_bars == "max_dte":
        purge_bars = cfg.get("_max_dte", "max_dte")
    embargo_bars = cfg.get("event_embargo_bars", 0)
    raise ValueError(
        "No validation folds after walk-forward purge/embargo; "
        f"unique_dates={unique_dates}, purge_bars={purge_bars}, "
        f"event_embargo_bars={embargo_bars}. "
        "Reduce purge/embargo, shorten option horizon, or provide a longer history."
    )


def run_pipeline(cfg: dict, start: str, end: str, run_id: str = None):
    """Execute full pipeline: ingestion → adapter → core stages → outputs."""
    if run_id is None:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_id = _safe_run_id(run_id)

    symbol = cfg.get("symbol", {}).get("ticker") or str(cfg.get("symbol", {}).get("product_id", "unknown"))

    # ── Phase 1: Ingestion ──
    print(f"[{run_id}] Loading {symbol} from {start} to {end}...")
    provider = get_provider(cfg)

    # SettlementLoader takes a file path; equity providers take a ticker symbol.
    provider_name = cfg.get("provider", "settlement")
    if provider_name == "settlement":
        data_source = cfg.get("data_file") or symbol
    else:
        data_source = cfg.get("symbol", {}).get("ticker", symbol)

    cache_cfg = cfg.get("versioned_cache", {}) or {}
    read_versioned_cache = bool(cache_cfg.get("read", False) or cfg.get("read_versioned_cache", False))
    write_versioned_cache = bool(cache_cfg.get("write", False) or cfg.get("write_versioned_cache", False))
    cache_mode = "versioned_cache" if read_versioned_cache else "provider_fetch"
    cache_guard = _cache_guard_status(cfg, cache_mode)
    _enforce_cache_guard(cache_guard, cfg)

    if read_versioned_cache:
        raw_df = get_versioned_cache().read(symbol, {**cfg, "backtest_start": start})
        if "as_of_date" in raw_df.columns:
            dates = pd.to_datetime(raw_df["as_of_date"], errors="coerce")
            raw_df = raw_df[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
    else:
        raw_df = provider.fetch(data_source, start, end)
        if write_versioned_cache:
            get_versioned_cache().write(
                symbol,
                raw_df,
                run_id=run_id,
                storage_format=cfg.get("data_storage_format", "parquet"),
            )
    print(f"  Ingestion: {len(raw_df)} rows loaded")

    # Audit: after ingestion
    snap_ingest = aud.snapshot(raw_df, "ingestion", cfg, run_id)
    n_rows_ingested = len(raw_df)
    raw_df_ingested = raw_df  # pre-gate provider input — pinned in the run manifest (I6)

    # ── Bronze gate: Data Contract + quarantine (P0) ──
    # Validate raw against its versioned contract; divert failing rows to
    # quarantine instead of letting them poison the backtest. Subsumes H2/H3.
    contract_gate = {"status": "not_checked", "reason": "no contract resolved"}
    quarantine_summary = None
    contract_report = None
    try:
        symbology_for_gate = Symbology()
    except Exception:
        symbology_for_gate = None
    contract_result = contracts_mod.validate_for_cfg(raw_df, cfg, symbology=symbology_for_gate)
    if contract_result is not None:
        rep = contract_result.report
        contract_report = rep
        quarantine_summary = write_quarantine(
            contract_result.quarantined, run_id, rep.get("tier", "bronze"), rep["rows_in"]
        )
        raw_df = contract_result.passed
        q_rate = rep.get("quarantine_rate", 0.0)
        contract_gate = {
            "status": "warning" if rep.get("rows_quarantined") else "pass",
            "contract_id": rep.get("contract_id"),
            "version": rep.get("version"),
            "enforcement": rep.get("enforcement"),
            "rows_quarantined": rep.get("rows_quarantined"),
            "quarantine_rate": q_rate,
            "by_reason": rep.get("quarantine_by_reason"),
            "frame_breaks": rep.get("frame_breaks"),
        }
        print(f"  Bronze gate ({rep.get('contract_id')} v{rep.get('version')}): "
              f"{rep.get('rows_quarantined')} rows quarantined "
              f"({q_rate:.1%}), {rep.get('rows_passed')} passed")

    # ── Coverage / freshness SLA gate (bronze) ──
    # Row-shape contracts can't see "we got 28 rows for a 20-month window". Compare the
    # trading days actually present vs the requested calendar; a shortfall becomes a
    # lifecycle-tracked break (high=fail / medium=warn) instead of silently passing.
    pipeline_breaks: list = []
    coverage_gate = {"status": "not_checked"}
    try:
        cov_min_ratio = float(cfg.get("coverage_min_ratio", coverage_mod.DEFAULT_MIN_RATIO))
        cov_max_gap = int(cfg.get("coverage_max_gap_days", coverage_mod.DEFAULT_MAX_GAP_DAYS))
        coverage_gate = coverage_mod.assess_coverage(
            raw_df, start, end, min_ratio=cov_min_ratio, max_gap_days=cov_max_gap
        )
        pipeline_breaks.extend(coverage_mod.coverage_breaks(coverage_gate, run_id, start, end))
        _cov_icon = {"pass": "ok", "warn": "WARN", "fail": "FAIL"}.get(coverage_gate["status"], "?")
        print(f"  Coverage SLA: {_cov_icon} - {coverage_gate['present_trading_days']}/"
              f"{coverage_gate['expected_trading_days']} trading days "
              f"({coverage_gate['coverage_ratio']:.1%})"
              + ("; " + "; ".join(coverage_gate["reasons"]) if coverage_gate["reasons"] else ""))
    except Exception as exc:  # SLA gate is observability — never break the run
        coverage_gate = {"status": "error", "error": str(exc)}

    # ── Phase 2: Adapter ──
    adapter = get_adapter(cfg)
    df, core_cfg = adapter.prepare(raw_df)
    print(f"  Adapter ({cfg['family']}): {len(df)} rows prepared")

    # Audit: after adapter
    snap_adapter = aud.snapshot(df, "adapter", cfg, run_id)
    frame_adapter = df.copy()  # CDC before-image (P2)

    # ── Stage 1: Validators ──
    df = validators.logical_bounds_check(df, core_cfg)
    df = validators.missing_completeness(df, core_cfg)
    df = validators.outlier_cap(df, core_cfg)
    print(f"  Stage 1 (validators): {df['_bound_flag'].sum()} bound flags, "
          f"{df.get('_outlier_flag', pd.Series()).sum()} outliers capped")

    # Audit: after stage 1
    snap_v1 = aud.snapshot(df, "validators", cfg, run_id)

    # ── Change Data Capture + break ledger (P2, §6/§7) ──
    # Diff adapter→validators at cell level; attribute price caps via _outlier_flag;
    # anything unexplained becomes an UNATTRIBUTED high-severity break.
    cdc_summary = {"status": "not_run"}
    try:
        price_col = core_cfg.get("price_col", "price")
        reason_maps = {
            "adapter->validators": {
                price_col: {"flag_col": "_outlier_flag", "reason": "outlier_cap"},
                "_row_drop": {"reason": "validator_or_filter"},
            }
        }
        cdc_records = cdc.diff_run(
            [("adapter", frame_adapter), ("validators", df.copy())],
            identity_cols=core_cfg.get("identity_cols"),
            reason_maps=reason_maps,
            run_id=run_id,
        )
        ledger_path = cdc.write_ledger(cdc_records, run_id)
        cdc_breaks = breaks_mod.raise_breaks(cdc_records, run_id)
        # one ledger per run: coverage-SLA breaks join CDC breaks
        pipeline_breaks.extend(cdc_breaks)
        breaks_path = breaks_mod.write_breaks(pipeline_breaks, run_id)
        diff_html_path = diff_report.write_diff_html(cdc_records, cdc_breaks, run_id)
        cdc_summary = {
            "status": "ok",
            "changes": len(cdc_records),
            "rollup": cdc.rollup(cdc_records),
            "ledger": ledger_path,
            "breaks": breaks_mod.summarize(pipeline_breaks),
            "breaks_ledger": breaks_path,
            "diff_html": diff_html_path,
        }
        unattributed = sum(1 for r in cdc_records if r.reason == cdc.UNATTRIBUTED
                           and r.change_type == "cell_mod")
        print(f"  CDC (adapter->validators): {len(cdc_records)} changes, "
              f"{unattributed} UNATTRIBUTED, {cdc_summary['breaks']['total']} breaks")
    except Exception as exc:  # CDC is observability — never break the run
        cdc_summary = {"status": "error", "error": str(exc)}
        # still persist any coverage-SLA breaks even if the CDC diff failed
        if pipeline_breaks:
            try:
                breaks_mod.write_breaks(pipeline_breaks, run_id)
            except Exception:
                pass

    # ── Auto-purge from lineage (P3, leakage L5) — OPT-IN ──
    # A feature's lookback IS its correct purge window. When enabled, derive purge_bars
    # from the lineage graph instead of the cfg 'max_dte' guess. Default off so fold
    # behavior is unchanged unless explicitly opted in (cfg.use_lineage_purge: true).
    lineage_purge = {"status": "not_used"}
    if cfg.get("use_lineage_purge"):
        graph = lineage.load_lineage(cfg.get("family", ""))
        if graph:
            purge_bars = lineage.max_lookback(graph)
            core_cfg = {**core_cfg, "purge_bars": purge_bars}
            lineage_purge = {"status": "applied", "purge_bars": purge_bars,
                             "source": "lineage.max_lookback"}

    # Build date-grouped folds before stability so Stage 2 diagnostics use
    # the same validation windows that metrics will score.
    folds = spl.walk_forward_split(df, core_cfg)
    folds = spl.purge_embargo(folds, df, core_cfg)
    _assert_validation_folds(folds, df, core_cfg)

    # ── Stage 2: Stability ──
    return_col = core_cfg.get("return_col", "return_std")
    stability_results: dict = {}
    if return_col in df.columns:
        r = _stability_series(df, return_col, agg="mean")

        adf_result = stab.adf_kpss_check(r)
        arch_result = stab.arch_lm_test(r)
        jb_result = stab.jarque_bera(r)
        hurst_result = stab.hurst_exponent(r)
        hurst_value = hurst_result.get("hurst")
        vr_result = stab.variance_ratio_test(r, input_kind="return_series")
        lb_result = stab.ljung_box(r)

        # PSI: use the same folds as walk-forward CV.
        row_return = pd.to_numeric(df[return_col], errors="coerce")
        psi_returns = stab.fold_distribution_shift(row_return, folds, core_cfg) if folds else {}

        # PSI on IV if available
        iv_col = "iv_provided"
        psi_iv: dict = {}
        if iv_col in df.columns:
            daily_iv = _stability_series(df, iv_col, agg="median")
            if len(daily_iv) >= 20:
                split_iv = int(len(daily_iv) * 0.6)
                psi_iv = stab.distribution_shift(daily_iv.iloc[:split_iv], daily_iv.iloc[split_iv:], core_cfg)

        # IV summary stats
        iv_stats: dict = {}
        if iv_col in df.columns:
            iv_clean = df[iv_col].replace([float("inf"), float("-inf")], float("nan")).dropna()
            iv_stats = {
                "null_pct": round(df[iv_col].isna().mean() * 100, 2),
                "min": float(iv_clean.min()) if len(iv_clean) else None,
                "median": float(iv_clean.median()) if len(iv_clean) else None,
                "mean": float(iv_clean.mean()) if len(iv_clean) else None,
                "p95": float(iv_clean.quantile(0.95)) if len(iv_clean) else None,
                "max": float(iv_clean.max()) if len(iv_clean) else None,
                "deep_otm_count": int((iv_clean > 2.0).sum()),
                "delta_mean": float(df["delta"].mean()) if "delta" in df.columns else None,
            }

        # Return summary stats
        return_stats = {
            "mean": float(r.mean()),
            "std": float(r.std()),
            "skew": float(r.skew()),
            "kurtosis": float(r.kurtosis()),
            "n": int(len(r)),
            "max_gain": float(r.max()),
            "max_loss": float(r.min()),
        }

        stability_results = {
            "trading_days": int(len(r)),
            "adf": adf_result,
            "arch": arch_result,
            "jarque_bera": jb_result,
            "hurst": hurst_result,
            "variance_ratio": vr_result,
            "ljung_box": lb_result,
            "psi_returns": psi_returns,
            "psi_iv": psi_iv,
            "iv_stats": iv_stats,
            "return_stats": return_stats,
            "input_grain": "date_mean",
            "psi_threshold": core_cfg.get("psi_threshold", 0.25),
        }

        feature_cols = [col for col in core_cfg.get("feature_cols", []) if col in df.columns]
        target_col = core_cfg.get("forward_return_col") or core_cfg.get("target_col")
        if feature_cols:
            stability_results["feature_quality"] = {
                "vif": stab.vif_condition_number(df[feature_cols]),
                "sign_consistency": stab.sign_consistency(df, {**core_cfg, "feature_cols": feature_cols}),
            }
            if target_col in df.columns:
                stability_results["feature_quality"]["ic"] = {
                    col: stab.information_coefficient(df[col], df[target_col])
                    for col in feature_cols
                }

        arch_state = arch_result.get("has_arch_effects")
        arch_text = "unknown" if arch_state is None else ("yes" if arch_state else "no")
        print(f"  Stage 2 (stability): ADF p={_fmt_float(adf_result.get('adf_pval'))}, "
              f"Hurst={_fmt_float(hurst_value, 3)}, ARCH={arch_text}")

    # Regime labels
    regime_labels = regime.assign_regime_labels(df, core_cfg)
    diversity = spl.regime_diversity_gate(folds, regime_labels, core_cfg)
    passed_folds = int(diversity["pass"].sum()) if "pass" in diversity.columns else 0
    print(f"  Stage 3 (splitter): {len(folds)} folds, {passed_folds} passed diversity gate")

    # Audit: after stage 3
    snap_v3 = aud.snapshot(df, "splitter", cfg, run_id)

    # ── Stage 4: Metrics + Overfitting ──
    strategy_return_col = _strategy_return_col(df, core_cfg)
    strategy_metrics_available = strategy_return_col is not None
    metrics_return_col = strategy_return_col or return_col
    metrics_input = "strategy_pnl" if strategy_metrics_available else "market_diagnostic"

    fold_returns = {}
    if metrics_return_col in df.columns:
        for i, (tr, va) in enumerate(folds):
            if i not in diversity[diversity["pass"]].index:
                continue
            fold_df = df.iloc[va]
            r = _stability_series(fold_df, metrics_return_col, agg="mean")
            if r.dropna().empty:
                continue
            fold_returns[i] = r

    per_fold = metrics.per_fold_breakdown(fold_returns, regime_labels)
    if metrics_return_col in df.columns:
        per_regime = metrics.per_regime_breakdown(df[metrics_return_col], regime_labels)
    else:
        per_regime = pd.DataFrame()
    stab_score = metrics.stability_score(per_fold)

    # Min-sample floor: a Sharpe computed on a handful of points is noise, not signal.
    # Below the floor, refuse to report it instead of printing a misleading 3.5.
    MIN_METRIC_SAMPLES = int(core_cfg.get("min_metric_samples", 60))
    sample_warning = None
    if metrics_return_col in df.columns:
        all_r = _stability_series(df, metrics_return_col, agg="mean").dropna()
        n_obs = len(all_r)
        if 0 < n_obs < MIN_METRIC_SAMPLES:
            sample_warning = (
                f"insufficient_sample: {n_obs} observations < {MIN_METRIC_SAMPLES} floor - "
                "Sharpe/DSR skipped (would be noise)"
            )
            print(f"  Stage 4: SKIPPED - {sample_warning}")
        elif not all_r.empty:
            sr = metrics.risk_adjusted(all_r)["sharpe"] or 0.0
            if strategy_metrics_available:
                n_trials = core_cfg.get("n_trials", 40)
                dsr_result = ovf.deflated_sharpe_ratio(sr, n_trials, len(all_r))
                print(f"  Stage 4 (strategy metrics): Sharpe={sr:.3f}, "
                      f"DSR={dsr_result.get('dsr', 0):.3f}, p={dsr_result.get('p_value', 1):.4f}")
            else:
                print(f"  Stage 4 (market diagnostics): Sharpe={sr:.3f}; "
                      "strategy P&L not present, DSR skipped")

    # Audit: after stage 4
    snap_v4 = aud.snapshot(df, "metrics", cfg, run_id)
    price_adjustments = _price_adjustment_summary(df, cfg)
    split_adjustments = _split_adjustment_summary(df)

    # ── Write outputs ──
    outputs_root = Path("outputs")
    run_dir = reporting.run_output_dir(outputs_root, run_id, symbol, cfg["family"], start, end)
    for subdir in ["tables", "attribution", "data", "report"]:
        (run_dir / subdir).mkdir(parents=True, exist_ok=True)

    attribution_summary = None
    if any(col in df.columns for col in ["pnl_gross", "gross_pnl", "pnl"]):
        wf = attr.waterfall(df, {**cfg, **core_cfg})
        attr.to_frame(wf).to_csv(run_dir / "attribution" / "waterfall.csv", index=False)
        attribution_summary = wf.as_dict()

    per_fold.to_csv(run_dir / "tables" / "per_fold.csv", index=False)
    per_regime.to_csv(run_dir / "tables" / "per_regime.csv", index=False)
    diversity.to_csv(run_dir / "tables" / "diversity.csv", index=False)

    # ── Export prepared DataFrame ──
    # Parquet for large datasets; CSV as a human-readable companion.
    data_dir = run_dir / "data"
    parquet_path = data_dir / "prepared.parquet"
    csv_path = data_dir / "prepared.csv"
    try:
        df.to_parquet(parquet_path, index=False)
    except Exception:
        parquet_path = None
    df.to_csv(csv_path, index=False)
    print(f"  Data export: {csv_path}")

    # Summary
    summary = {
        "run_id": run_id,
        "output_dir": str(run_dir),
        "instrument": symbol,
        "family": cfg["family"],
        "date_range": [start, end],
        "n_rows_raw": n_rows_ingested,
        "n_rows_prepared": len(df),
        "n_folds": len(folds),
        "n_folds_passed": int(passed_folds),
        "stability_score": stab_score,
        "metrics_input": metrics_input,
        "strategy_metrics_available": bool(strategy_metrics_available),
        "strategy_return_col": strategy_return_col,
        "metric_warning": "; ".join(filter(None, [
            sample_warning,
            None if strategy_metrics_available else
            "Stage 4 used market return diagnostics because strategy P&L/return data is absent.",
        ])) or None,
        "sample_floor_breached": bool(sample_warning),
        "guard_status": {
            "pit_timing": _pit_guard_status(df),
            "strategy_pnl_present": "pass" if strategy_metrics_available else "fail",
            "cache_version_fixed": cache_guard,
            "price_adjustments": price_adjustments,
            "split_adjustments": split_adjustments,
            "contract_gate": contract_gate,
            "coverage_sla": coverage_gate.get("status"),
        },
        "price_adjustments": price_adjustments,
        "split_adjustments": split_adjustments,
        "contract_gate": contract_gate,
        "coverage_gate": coverage_gate,
        "quarantine": quarantine_summary,
        "cdc": cdc_summary,
        "lineage_purge": lineage_purge,
        "data_cache_mode": cache_mode,
        "attribution": attribution_summary,
        "audit_snapshots": [snap_ingest, snap_adapter, snap_v1, snap_v3, snap_v4],
        "data_export": {
            "csv": str(csv_path),
            "parquet": str(parquet_path) if parquet_path else None,
            "columns": list(df.columns),
            "n_rows": len(df),
        },
    }

    summary["artifacts"] = {
        "per_fold": str(run_dir / "tables" / "per_fold.csv"),
        "per_regime": str(run_dir / "tables" / "per_regime.csv"),
        "diversity": str(run_dir / "tables" / "diversity.csv"),
        "prepared_csv": str(csv_path),
        "prepared_parquet": str(parquet_path) if parquet_path else None,
    }

    # ── Run manifest: content-pinned reproducibility (P1, I6) ──
    n_trials_used = core_cfg.get("n_trials", cfg.get("n_trials", 40))
    run_manifest = manifest_mod.build_manifest(
        run_id, cfg, raw_df_ingested, df,
        symbol=symbol,
        contract_report=contract_report,
        n_trials=n_trials_used,
        n_trials_source="config",
        knowledge_cutoff_fallback=end,
    )
    manifest_path = manifest_mod.write_manifest(run_manifest)
    # also drop a copy beside the run's other artifacts
    manifest_mod.write_manifest(run_manifest, run_dir)
    summary["manifest"] = run_manifest
    summary["manifest_path"] = manifest_path

    summary["summary_report"] = reporting.write_summary_report(summary, per_fold, per_regime, diversity, run_dir)

    if stability_results:
        html_path = reporting.write_html_report(
            summary, stability_results, per_fold, per_regime, diversity, run_dir
        )
        summary["html_report"] = html_path

    summary_path = run_dir / "summary.json"
    with open(summary_path, "w") as f:
        json.dump({k: str(v) if isinstance(v, (pd.Timestamp, datetime)) else v
                   for k, v in summary.items()}, f, indent=2, default=str)

    print(f"\nDone. Run ID: {run_id}")
    print(f"  Folds: {len(folds)} total, {passed_folds} passed")
    print(f"  Sharpe stability: mean={stab_score.get('sharpe_mean', 0):.3f}, "
          f"min={stab_score.get('sharpe_min', 0):.3f}")
    print(f"  Profitable folds: {stab_score.get('pct_profitable_folds', 0):.0%}")
    print(f"  Outputs: {run_dir}")
    print(f"  Summary: {summary_path}")
    if summary.get("html_report"):
        print(f"  HTML report: {summary['html_report']}")

    return summary


def main():
    parser = argparse.ArgumentParser(description="Quant Pipeline Framework v1.3")
    parser.add_argument("--instrument", "-i", required=True,
                        help="Instrument name (e.g. bz, spx, aapl)")
    parser.add_argument("--ticker", default=None,
                        help="Override equity ticker without creating a new instrument YAML (e.g. MSFT)")
    parser.add_argument("--provider", default=None, choices=["settlement", "yfinance"],
                        help="Override data provider (config default used if omitted)")
    parser.add_argument("--data-file", default=None,
                        help="Import an external settlement file (pipe-delimited) — overrides "
                             "cfg.data_file; implies --provider settlement")
    parser.add_argument("--allow-unversioned-data", action="store_true",
                        help="Allow direct provider/source reads for exploratory diagnostics only")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--run-id", default=None, help="Custom run ID")
    args = parser.parse_args()

    cfg = load_config(args.instrument)
    cfg = apply_runtime_overrides(cfg, ticker=args.ticker)
    if args.provider:
        cfg["provider"] = args.provider
    if args.data_file:
        cfg["data_file"] = args.data_file
        cfg.setdefault("provider", "settlement")
        if cfg["provider"] != "settlement":
            cfg["provider"] = "settlement"
    if args.allow_unversioned_data:
        cfg["require_fixed_data_version"] = False
    summary = run_pipeline(cfg, args.start, args.end, args.run_id)

    return 0


if __name__ == "__main__":
    sys.exit(main())
