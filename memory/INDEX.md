---
name: quant-pipeline-index
description: Complete file-by-file index of quant_pipeline/ for agent navigation
metadata:
  type: reference
---

# Quant Pipeline — File Index

## Root

| File | Purpose | Key Contents |
|------|---------|-------------|
| `run_pipeline.py` | Entry point | argparse CLI, load_config(), get_provider(), get_adapter(), run_pipeline() |

## ingestion/ — Data Loading Layer

| File | Purpose | Key Classes/Functions |
|------|---------|----------------------|
| `base.py` | Provider contract | `RAW_SCHEMA`, `EQUITY_RAW_SCHEMA`, `ProviderBase` (ABC), `validate_schema()` |
| `settlement_loader.py` | Pipe-delimited EOD parser | `SettlementLoader`, `SETTLEMENT_COLUMN_MAP`, `parse_pipe_row()` |
| `equity_loader_a.py` | POC equity provider (Yahoo) | `EquityLoaderA` — handles raw_close/adj_factor, delisting |
| `equity_loader_b.py` | Cross-check equity provider | `EquityLoaderB` — cross_check() adj convention |
| `symbology.py` | PRODUCT_ID → InternalSymbol | `Symbology`, `InternalSymbol`, resolve(), reverse(), validate_uniqueness() |
| `cache.py` | Parquet cache + incremental | `Cache`, `get_cache()`, is_complete(), missing_ranges() |
| `versioned_cache.py` | v1.4 immutable raw cache + PIT joins | `VersionedCache`, `infer_available_at()`, `add_availability_columns()`, `pit_join()` |

## core/ — Asset-Agnostic Processing (IRON RULE: NO instrument names)

| File | Stage | Purpose | Key Functions |
|------|-------|---------|--------------|
| `validators.py` | 1 | Logical bounds, completeness, outliers | `logical_bounds_check()`, `missing_completeness()`, `outlier_cap()` |
| `stability.py` | 2 | Stationarity, distribution shift, feature quality | `adf_kpss_check()`, `arch_lm_test()`, `variance_ratio_test()`, `ljung_box()`, `jarque_bera()`, `hurst_exponent()`, `distribution_shift()`, `information_coefficient()`, `vif_condition_number()`, `sign_consistency()` |
| `splitter.py` | 3 | Walk-forward, purge/embargo, diversity gate | `walk_forward_split()`, `purge_embargo()`, `regime_diversity_gate()` (KL+JS), `combinatorial_purged_cv()` |
| `metrics.py` | 4 | Full metric set + per-fold/regime | `return_metrics()`, `risk_adjusted()`, `drawdown_metrics()`, `distribution_metrics()`, `tail_metrics()`, `hit_metrics()`, `per_fold_breakdown()`, `per_regime_breakdown()`, `stability_score()` |
| `overfitting.py` | 4 | False discovery prevention | `deflated_sharpe_ratio()`, `prob_backtest_overfitting()`, `min_track_record_length()` |
| `regime.py` | all | Rule-based labels + HMM/GMM validator | `assign_regime_labels()`, `compute_transition_matrix()`, `validate_labels_hmm()`, `diversity_check_gmm()` |
| `pricing.py` | — | Option pricing + IV solver | `price()` (Black-76/BS-Merton), `solve_iv()` (Brent), `validate_provided_iv()` |
| `greeks.py` | — | Closed-form Greeks + net Greeks | `single_leg_greeks()`, `net_greeks()`, `bump_greeks()` (test only), `Leg` dataclass |
| `dte.py` | — | DTE single source of truth | `compute_dte()`, `compute_dte_series()` — calendar/trading basis |
| `audit.py` | — | Lightweight snapshot + diff | `snapshot()`, `diff_stages()`, `hash_schema()`, `hash_subset()` |
| `txcost.py` | 4 | v1.4 transaction cost model | `cost_per_trade()`, `total()`, `financing_cost()`, `CostBreakdown` |
| `attribution.py` | 4 | v1.4 waterfall attribution | `waterfall()`, `to_frame()`, `Layer`, `WaterfallResult` |

## adapters/ — Asset-Aware Preparation

| File | Purpose | Key Classes/Methods |
|------|---------|---------------------|
| `base.py` | Adapter contract | `AdapterBase` (ABC) — `prepare(raw_df) → (df, cfg)` |
| `equity_adapter.py` | Equity pipeline | `EquityAdapter` — PIT-adjusted price, corp actions, survivorship |
| `futures_adapter.py` | Futures pipeline | `FuturesAdapter` — continuous futures, term structure, scheduled events |
| `options_base.py` | Shared options (~65%) | `OptionsBase` — IV surface, Greeks, PCP, VRP, skew, validate_provided_iv |
| `equity_options_adapter.py` | Equity options | `EquityOptionsAdapter` — BS-Merton, strike-adjust, NYSE close |
| `futures_options_adapter.py` | Futures options | `FuturesOptionsAdapter` — Black-76, roll, term structure, event regimes |

## configs/ — All Instrument-Specific Configuration

| File | Purpose |
|------|---------|
| `equity.yaml` | Equity family defaults |
| `futures.yaml` | Futures family defaults |
| `instruments/bz.yaml` | Brent futures options — full spec (model, DTE, events, audit) |
| `instruments/spx.yaml` | SPX index options spec |
| `instruments/aapl.yaml` | AAPL equity spec |
| `symbology/product_map.yaml` | product_id → contract_root + hub mapping |
| `events/eia.csv` | EIA report schedule |
| `events/opec.csv` | OPEC meeting schedule |
| `events/earnings.csv` | Earnings dates (placeholder) |

## tests/ — Hard Requirements (must pass before merge)

| Path | Covers |
|------|--------|
| `test_ingestion/test_symbology.py` | Uniqueness + round-trip + no-orphan + real-row parsing |
| `test_dte/test_calendar.py` | Calendar/trading convention, edge cases (T=0, T<0, leap year) |
| `test_core/test_pricing.py` | Black-76, BS-Merton, PCP, IV solver round-trip, arb detection |
| `test_core/test_greeks.py` | Closed-form, bump-vs-analytic, net Greeks, calendar spread vega |
| `test_core/test_splitter.py` | No look-ahead, purge gap, diversity gate (unseen regime fails) |
| `test_core/test_metrics.py` | Numeric stability, per-fold breakdown, hit metrics |
| `test_core/test_txcost.py` | v1.4 fixed/scaled/impact transaction costs |
| `test_core/test_attribution.py` | v1.4 options/equity attribution waterfall |
| `test_ingestion/test_versioned_cache.py` | v1.4 immutable cache + available_at PIT join |
| `golden/black76_reference.csv` | Reference values cross-checked vs QuantLib |
| `fixtures/` | Small synthetic+real-row samples (commit-safe) |
| `conftest.py` | Shared fixtures: brent_row, sample_raw_df, sample_option_df, sample_returns, sample_regime_labels, symbology, bz_config |
