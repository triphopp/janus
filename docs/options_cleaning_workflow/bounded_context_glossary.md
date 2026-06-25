# Options Cleaning Workflow Bounded Context Glossary

เอกสารนี้ใช้เป็นสะพานกลางระหว่าง Domain Expert และ Technical Expert ก่อนนำ
workflow ในชุด `options_cleaning_*` ไปทำ slide หรือ paper. เป้าหมายคือให้คำใน
diagram, คำใน code, และคำในภาษา domain สื่อความหมายเดียวกัน

## Folder Map

ชุด workflow ทั้งหมดอยู่ใต้ `docs/options_cleaning_workflow/`

- `options_cleaning_01_intake_bronze/`: input, fixed-version guard, bronze contract,
  quarantine, coverage, and family schema handoff
- `options_cleaning_02_silver_adapter/`: option-chain standardization, equity/futures
  option adapters, IV/Greek/PCP/VRP processing, and option-universe filtering
- `options_cleaning_03_validation_observability/`: validators, data-quality scorecard,
  CDC, break ledger, manifest, and final prepared-data artifacts

## Shared Vocabulary Rules

- **Quarantine** means a row failed a bronze contract rule and is diverted before the
  silver adapter. Do not use this word for research-universe filters.
- **Filter / exclude / drop** means a row is removed from the current research universe,
  usually after adapter logic. This is not automatically bad data.
- **Flag** means a row stays in the dataset but receives a reason column for review.
- **Cap** means a numeric value is modified by policy and must be attributable in CDC.
- **Report** means the condition is summarized at run level but may not change rows.
- **Validated prepared dataset** is the current final output wording. Do not call it
  Gold unless a real Gold serving layer is implemented.

## Layer 01: Intake and Bronze

| Diagram term | Domain meaning | Technical meaning | Row fate / output |
| --- | --- | --- | --- |
| `Instrument YAML` | Business configuration for the instrument and experiment | Config loaded then normalized by `normalize_config()` | Produces normalized config |
| `family` | Asset family, e.g. equity options vs futures options | `cfg["family"]`, used by provider, contract, and adapter selection | Routes workflow |
| `Fixed-input guard` | Backtest must use a stable raw input, not a moving live feed | `data_file_sha256` or immutable `data_version`; enforced unless explicitly opted out | Fail fast on mutable/unpinned input |
| `Provider/cache read` | Source data extraction | yfinance/vendor/settlement provider or `VersionedCache.read()` | Produces raw provider frame |
| `Raw provider frame` | Data exactly as supplied after source read and basic loader standardization | DataFrame before bronze contract | Input to contract validation |
| `Bronze contract` | First data agreement between source and pipeline | Versioned YAML contract such as `equity_options.v1` or `settlement_options.v1` | Splits passed vs quarantined rows |
| `Structural checks` | Does the file have required fields in usable types? | Required columns, dtype coercion, key rounding | Bad rows get `_quarantine_reason` |
| `Semantic checks` | Are values economically possible? | Price, strike, right, expiry, and related row rules | Bad rows get `_quarantine_reason` |
| `PIT check` | Was this data knowable at the time we claim? | `available_at` must not be before `as_of_date` | Bad rows get `_quarantine_reason` |
| `Symbology / orphan` | Can the product identity be mapped unambiguously? | Product map validation for fields like `product_id`, `contract_root`, `hub` | Orphan rows are quarantined when enforced |
| `Distributional frame checks` | Run-level sanity checks on the batch | Null-rate checks; PSI is documented but waits for reference vintage | Frame-level report/break, not always row quarantine |
| `Coverage SLA` | Did we receive enough dates for the requested window? | Expected trading days, coverage ratio, and max date gap | Run-level pass/warn/fail and possible break |
| `Family schema guard` | Did we accidentally feed non-option data into an option adapter? | Requires option-chain columns such as `expiry`, `right`, `strike`, `price` | Fail fast before adapter math |

## Layer 02: Silver Adapter

| Diagram term | Domain meaning | Technical meaning | Row fate / output |
| --- | --- | --- | --- |
| `Normalize option columns` | Standardize chain fields before option math | Dates, numeric fields, `right = C/P`, option mask | DataFrame columns coerced |
| `Option mask` | Which rows are option contracts? | Inferred from `instrument_type == option` or valid `right + strike` | Used by filters and diagnostics |
| `EquityOptionsAdapter` | Equity/index option preparation path | Builds BS-Merton inputs from spot/underlying fields | Produces `S`, `F`, `option_price`, `price_std` |
| `FuturesOptionsAdapter` | Futures option preparation path | Builds continuous futures, term structure, scheduled event flags, and underlying future map | Produces Black-76-ready rows |
| `Underlying future map` | Which futures price prices this option? | Joins option rows to support future rows by date and contract identity | Missing maps may be dropped or raise in strict mode |
| `price_std` | Standardized underlying price used downstream | For options, generally the underlying/forward price, not the option premium | Feeds returns, validators, and metrics |
| `option_price` | Option premium | Copied from raw `price` for option rows | Feeds IV solving and pricing checks |
| `DTE / T` | Time from observation date to expiry | `core.dte.compute_dte_series()` produces `T`; `dte_days` is calendar-day helper | Rows after expiry get unusable/NaN time |
| `Universe filters before pricing` | Research choice to narrow the option universe before expensive math | `min_dte_days`, `max_dte_days`, `min_option_price`, `max_relative_spread` | Drops option rows; counted in `option_quality.universe.drop_by_reason` |
| `IV selection/solve` | Decide the IV used for each option row | `build_iv_surface()` sets canonical `iv` from `iv_provided` or from `solve_iv()` | Adds `iv`, `iv_source_used`, optional `iv_solved`, `iv_diff`, `iv_flag` |
| `Provided IV` | IV supplied by exchange/vendor | `iv_source: provided`, uses `iv_provided`; can validate against self-solved IV | Usually retained unless max-IV filter removes it |
| `Solved IV` | IV implied by observed premium under project pricing model | `iv_source: solve`, Brent/bisection root solve using price, underlying, strike, T, rate, right | Unsolvable IV becomes missing and can be filtered |
| `Max-IV filter` | Remove implausible or out-of-scope volatility rows | `option_universe.max_iv`; old `iv_cap` is alias only | Drops `iv_above_cap` or `iv_missing_or_unsolved` option rows |
| `Greeks` | Sensitivities used for option diagnostics/strategy features | `delta`, `gamma`, `vega`, `theta`, `rho` from `core.greeks.batch_greeks()` | Adds columns; can feed delta-band filter |
| `Delta-band filter` | Keep options within target moneyness/liquidity region | `option_universe.delta_band` using provided or computed delta | Drops `delta_below_min` / `delta_above_max` |
| `PCP` | Put-call parity sanity check | Pair call/put by date, expiry, strike, and contract identity | Flags `_pcp_flag`, `pcp_pair_missing`, `pcp_duplicate_pair` |
| `VRP sign` | Whether option IV is rich/cheap relative to realized vol | `vrp = iv - vol_std`, then sign bucket | Adds `vrp`, `vrp_sign` |
| `skew_25d` | Intended 25-delta skew diagnostic | Current implementation is a placeholder returning `0.0` | Do not present as full skew surface or active regime axis |
| `Silver quality flags` | Keep suspicious but not necessarily invalid option rows visible | IV, delta-sign/range, and premium-below-intrinsic checks | Adds `_iv_quality_flag`, `_delta_quality_flag`, `_premium_quality_flag` |
| `Prepared DataFrame + core_cfg` | Adapter output contract for later stages | Cleaned DataFrame plus config fields like `identity_cols`, `price_col`, `return_col` | Main input to validators and reporting |

Important wording: the code currently uses the function name `build_iv_surface()`, but
the implemented behavior is IV selection/validation/solving per row. Until real surface
interpolation or smoothing exists, prefer **IV selection/solve** in paper and slides.

## Layer 03: Validation and Observability

| Diagram term | Domain meaning | Technical meaning | Row fate / output |
| --- | --- | --- | --- |
| `Return clip hook` | Optional policy for extreme return observations | Adapter method such as `apply_return_clip()` if present | Usually flag-only by default |
| `PIT-MAD return policy` | Detect return outliers without using the future | Point-in-time MAD policy; optional derived `return_winsorized` | Tags returns; canonical returns should not be silently overwritten |
| `Stage 1 validators` | Final data sanity pass before experiment stages | Calls `logical_bounds_check()`, `missing_completeness()`, `outlier_cap()` | Adds flags and may cap selected price series |
| `logical_bounds_check()` | Are row values logically/economically valid? | Checks price > 0, option premium > 0, premium >= intrinsic, volume >= 0, bid <= ask, IV > 0, strike > 0 | Adds `_bound_flag`, `_bound_reason`; does not quarantine |
| `missing_completeness()` | Are there missing dates, duplicate grains, or thin liquidity? | Checks duplicate identity-date rows, `date_gap`, open-interest floor, optional volume floor | Adds `_missing_flag`, `_missing_reason` |
| `date_gap` | Missing observation gap in a time series | Gap between consecutive `as_of_date` values per identity; default basis is business/trading days | Flag reason like `date_gap>5bd`; does not mean strike-grid gap |
| `outlier_cap()` | Extreme price-series value treatment | Expanding MAD clip using only prior observations where applicable | Adds `_outlier_flag`; may modify configured `price_col` |
| `outlier_gap` | Not a current code term | Usually this means either `date_gap` or `outlier_cap`; clarify before using | Avoid in slides unless formally defined |
| `Data-quality scorecard` | Run-level health summary | Rates for return outliers, price outliers, bounds, missingness, quarantine, coverage | Pass/warn/fail; may enforce failure |
| `AQL / LTPD` | Acceptable vs unacceptable defect-rate thresholds | Scorecard budgets for each quality dimension | Controls pass/warn/fail status |
| `CDC stage chain` | What changed between pipeline stages? | Diffs ingestion -> adapter -> return_clip -> validators | Produces reason-coded change records |
| `Change ledger` | Audit log of schema, row, and cell changes | JSONL records with stage, key, column, before/after, reason | Written to `outputs/diff` |
| `Break ledger` | Issues that require review | Unattributed cell changes, unexpected row additions, unexplained row drops, coverage breaks | Written to `outputs/breaks` |
| `Manifest` | Reproducibility receipt | Code version, config hash, input/output hashes, contract versions, knowledge cutoff | Written to `outputs/manifest` and run output dir |
| `option_quality` | Option-specific quality summary | IV, delta, PCP, universe-drop, and silver-flag summaries | Stored in `summary.json` |
| `Validated prepared dataset` | Final dataset emitted by this workflow | Prepared CSV/parquet plus metadata artifacts after validation and observability | Current final output; not a Gold layer |

## Terms Requiring Domain Sign-Off

These values are policy choices, not purely technical facts:

- `coverage_min_ratio` and `coverage_max_gap_days`: how much missing history is tolerable
- `date_gap_days` / `max_gap_days`: maximum allowed observation gap by asset family
- `min_oi`, `futures_oi_floor`, `min_volume`: liquidity floors
- `min_dte_days`, `max_dte_days`: option horizon universe
- `min_option_price`: minimum premium to keep IV/Greek math stable
- `max_relative_spread`: liquidity/spread cutoff
- `option_universe.max_iv`: maximum allowed IV for the research universe
- `delta_band`: target moneyness/sensitivity range
- `iv_validate_threshold`: allowed difference between provided IV and self-solved IV
- Data-quality AQL/LTPD budgets for pass/warn/fail

## Recommended Plain-English Labels

Use these in slides when a function name is too technical:

| Technical label | Slide/Paper label |
| --- | --- |
| `logical_bounds_check()` | Logical value checks |
| `missing_completeness()` | Completeness and duplicate checks |
| `date_gap` | Missing observation gap |
| `outlier_cap()` | Point-in-time outlier treatment |
| `build_iv_surface()` | IV selection/solve |
| `Max-IV filter` | IV universe cap |
| `CDC` | Stage-to-stage change audit |
| `Break ledger` | Review ledger for unexplained changes |
| `core_cfg` | Prepared-data runtime contract |

## Review Checklist Before Changing Figures

- Every diagram label maps to exactly one meaning in this glossary.
- Any dropped row is classified as either quarantine or research-universe exclusion.
- Any value mutation is either avoided, derived into a new column, or CDC-attributed.
- Any run-level failure is separated from row-level failure.
- No figure claims a Gold layer, full IV surface interpolation, or active skew regime axis
  unless the implementation is added first.
