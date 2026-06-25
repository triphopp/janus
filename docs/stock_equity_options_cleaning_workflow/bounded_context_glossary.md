# Stock and Equity Options Cleaning Workflow Glossary

This glossary aligns domain and technical language for the stock (`equity`) and
equity-option (`equity_options`) cleaning workflows. It intentionally excludes
futures-option concerns such as contract roots, hubs, delivery months, and Black-76
underlying future maps.

## Folder Map

- `stock_equity_01_intake_bronze/`: source read, fixed-input guard, bronze contracts,
  quarantine, coverage, and family-schema handoff
- `stock_equity_02_silver_preparation/`: stock price preparation and equity-option
  preparation
- `stock_equity_03_validation_observability/`: validators, data-quality scorecard,
  CDC, break ledger, manifest, and prepared-data artifacts

## Shared Vocabulary Rules

- **Stock** means `family: equity`, daily equity price bars prepared by `EquityAdapter`.
- **Equity option** means `family: equity_options`, option-chain rows prepared by
  `EquityOptionsAdapter`.
- **Quarantine** means a row failed a bronze contract and is diverted before silver
  preparation.
- **Universe filter** means a research selection after option preparation; it is not
  the same thing as quarantine.
- **Flag** means the row stays in the dataset with a review reason.
- **Validated prepared dataset** is the current final output. Do not call it Gold.

## Layer 01: Intake and Bronze

| Term | Domain meaning | Technical meaning | Row fate / output |
| --- | --- | --- | --- |
| `equity` | Stock price-bar workflow | `cfg["family"] == "equity"` | Uses `EquityLoaderA` and `EquityAdapter` |
| `equity_options` | Equity option-chain workflow | `cfg["family"] == "equity_options"` | Uses `EquityOptionsLoaderYF` or vendor feed and `EquityOptionsAdapter` |
| `Fixed-input guard` | Backtest must use stable raw input | `data_file_sha256` or immutable `data_version` | Fail fast on mutable/unpinned input |
| `Equity price source` | Stock daily bars | yfinance price loader or versioned cache | Raw stock frame |
| `Equity option source` | Option chain snapshot or vendor historical chain | yfinance option-chain loader or vendor feed | Raw option-chain frame |
| `equity_price.v1` | Bronze contract for stock bars | Requires `as_of_date`, `symbol`, `raw_close`, `adj_factor`, `volume`, `is_delisted` | Bad rows quarantine |
| `equity_options.v1` | Bronze contract for equity options | Requires `as_of_date`, `symbol`, `expiry`, `right`, `strike`, `price`, `underlying_price` | Bad rows quarantine |
| `PIT availability` | Data must be knowable before decisions use it | `available_at` and `decision_time` | Bad timing can fail PIT guards |
| `Coverage SLA` | Enough dates for the requested window | coverage ratio and max date gap | Run-level pass/warn/fail |
| `yfinance option snapshot` | Current option chain only, not historical option chains | One `as_of_date` snapshot | Coverage/min-sample gates should flag it as not backtest-grade history |

## Layer 02: Silver Preparation

| Term | Domain meaning | Technical meaning | Row fate / output |
| --- | --- | --- | --- |
| `raw_close` | Provider close used for stock return calculations | yfinance `Close`; split-adjusted in modern yfinance | Feeds `price_std` unless policy changes |
| `raw_close_unadj` | Reconstructed true traded historical price level | `raw_close * split_factor` | Diagnostic/level-strategy support |
| `adj_factor` | Provider adjustment factor | Adj Close / Close; dividend-only for yfinance >= 1.x | Kept for diagnostics |
| `split_factor` | Retroactive split adjustment baked into provider close | Future split ratio product after date `t` | Warning for price-level leakage risk |
| `dividend` | Cash dividend on ex-date | Added into total return in `EquityAdapter` | Feeds `return_raw` |
| `price_std` | Standardized stock price | For stock: prepared close; for options: underlying price | Feeds returns, validators, metrics |
| `return_raw` | Total return used by stock pipeline | `(price_std + dividend) / previous_price - 1` | Canonical stock return |
| `return_price` | Price-only diagnostic return | `price_std / previous_price - 1` | Diagnostic |
| `vol_std` | Realized volatility estimate | Rolling std of `return_std` | Feature/diagnostic |
| `survivor_flag` | Delisting/survivorship visibility | From `is_delisted` when present | Row stays visible |
| `option_price` | Equity-option premium | Copied from option-chain `price` | Feeds IV solving and pricing diagnostics |
| `BS-Merton inputs` | Pricing inputs for equity options | `S`, `F` compatibility, `T`, `r`, `iv`, `q` when configured | Feeds Greeks and PCP |
| `IV selection/solve` | Choose IV per option row | `iv_provided` or solved IV via `solve_iv()` | Adds canonical `iv` |
| `Option universe filter` | Research selection for option rows | DTE, premium, spread, max IV, delta band | Drops option rows; not quarantine |
| `PCP` | Put-call parity check | Pair call/put by date, expiry, strike, symbol | Adds PCP flags |
| `skew_25d` | Intended skew diagnostic | Current implementation is placeholder `0.0` | Do not present as full skew surface |

## Layer 03: Validation and Observability

| Term | Domain meaning | Technical meaning | Row fate / output |
| --- | --- | --- | --- |
| `PIT-MAD return policy` | Detect extreme stock returns without future data | `EquityAdapter.apply_return_clip()` as separate CDC stage | Default tag-only |
| `cross-provider validation` | Check whether a large stock return is genuine | Optional Stooq/AlphaVantage comparison | Updates return-outlier reason/status |
| `logical_bounds_check()` | Logical value checks | price, premium, intrinsic, volume, bid/ask, IV, strike | Adds `_bound_flag` |
| `missing_completeness()` | Missing dates, duplicate grain, liquidity floor | duplicate identity-date, `date_gap`, OI/volume floor | Adds `_missing_flag` |
| `outlier_cap()` | PIT outlier treatment on applicable price series | Expanding MAD cap for configured `price_col` where applicable | Adds `_outlier_flag`, may mutate `price_col` |
| `Data-quality scorecard` | Run-level health summary | return, price, bounds, missing, quarantine, coverage dimensions | Pass/warn/fail |
| `CDC` | Stage-to-stage change audit | ingestion -> adapter -> return_clip -> validators | Change ledger JSONL |
| `Break ledger` | Review queue for unexplained changes | UNATTRIBUTED cell changes, unexpected rows, coverage breaks | Break JSONL |
| `Manifest` | Reproducibility receipt | config hash, code version, input/output hashes | Manifest JSON |

## Recommended Labels

| Technical label | Slide/paper label |
| --- | --- |
| `EquityAdapter` | Stock price preparation |
| `EquityOptionsAdapter` | Equity-option preparation |
| `raw_close_unadj` | True traded price reconstruction |
| `price_adjustment_warning` | Provider adjustment warning |
| `apply_return_clip()` | PIT return outlier policy |
| `build_iv_surface()` | IV selection/solve |
| `logical_bounds_check()` | Logical value checks |
| `missing_completeness()` | Completeness checks |
| `outlier_cap()` | PIT outlier treatment |

## Review Checklist

- Do not mix futures-option terms into stock/equity-option figures.
- Do not call yfinance equity-option data historical option-chain data.
- Do not call the final prepared output Gold.
- Do not present `skew_25d` as a completed surface/regime feature.
- Separate stock return outlier policy from option universe filtering.
