# Downstream Option Chain Greeks Export

Urgency: `P0-critical`

Status: `draft`

Source plan:

- `docs/design/csv_storage_bounded_context_redesign.md`
- `docs/design/data_structure_reading_map.md`
- `docs/design/data_test_measurement_criteria.md`
- `issues/completed/P0-critical/pipeline-data-integrity/012-split-date-and-contract-grain.md`
- `issues/completed/P0-critical/pipeline-data-integrity/022-settlement-availability-anchor.md`
- CME WTI contract policy reference:
  `https://www.cmegroup.com/markets/energy/crude-oil/light-sweet-crude.contractSpecs.html`

## Summary

Add an additive downstream-ready option chain export for futures options,
starting with WTI, that contains clean daily market facts plus full Black-76
Greeks. This export is for downstream use by Lean, Python, research notebooks,
or other consumers, and must not replace the existing dashboard or review
artifacts during the first migration phase.

The export should be a market-facing dataset, not a data-quality report. It should
include only rows that passed the release gate. Quarantine, held-back rows,
excluded-from-study rows, detailed check flags, and dashboard readiness language
belong in review artifacts and manifests, not in the downstream CSV.

## Why It Matters

The current prepared output is too wide and mixes raw vendor fields, domain checks,
analytics, dashboard flags, and option-chain rows in one table. In the observed WTI
sample:

- raw provider file: `1,851,596` rows, `13` columns
- current prepared output: `1,831,218` rows, `55` columns
- rough clean option rows after major flags: `206,471` rows, about `11.15%` of raw

Downstream consumers do not need the full review/debug shape. They need a clean
option chain with market prices, implied volatility, and Greeks. Keeping this
export separate reduces accidental column misuse while preserving the existing
dashboard/report workflow.

This issue also prevents the previous one-day settlement timing failure from being
reintroduced. The CSV may stay date-only, but the manifest/importer must define how
`trade_date` becomes tradable consumer time.

## Scope

In scope:

- Add a new additive dataset folder, e.g. `data/option_chain_greeks/`.
- Add a reusable downstream CSV, e.g. `data/option_chain_greeks/option_chain_greeks.csv`.
- Add companion metadata files in the same folder: `manifest.json`,
  `data_dictionary.md`, and `schema.json`.
- Keep existing `prepared.csv`, `summary.json`, HTML report, and dashboard artifacts
  working unchanged during the first phase.
- Export only clean rows that passed the release gate.
- Use finance-friendly column names in the downstream CSV.
- Use date-only market facts in the CSV and move availability/tradable-time policy
  into the manifest/importer.
- Support WTI futures options using `pricing_model=black76`.
- Record market conventions in instrument config and copy them into the manifest
  or product policy. The core writer must not provide instrument-specific defaults.
- Add `summary.json` artifact paths for the downstream CSV, manifest, data
  dictionary, and schema.

Out of scope:

- Replacing the dashboard data source.
- Removing or renaming existing prepared output columns.
- Exporting quarantine/review/debug fields into the downstream CSV.
- Implementing every downstream custom data reader in this issue.
- Adding second-order Greeks such as vanna, vomma, charm, speed, or color.

## Downstream CSV Contract

Purpose: clean daily option chain with Greeks for downstream consumers.

Grain: one option contract per `trade_date`.

Headers use `lower_snake_case`. Domain codes such as product/root values may remain
uppercase in row values.

Recommended columns:

| Column | Meaning |
| --- | --- |
| `trade_date` | Market session date described by the settlement row |
| `product` | Domain product label, e.g. `WTI` |
| `underlying_symbol` | Underlying futures symbol/root-month label |
| `option_symbol` | Option contract label for downstream use |
| `contract_month` | Underlying contract month normalized from raw `STRIP` date |
| `expiration_date` | Option expiration date |
| `option_type` | `call` or `put` |
| `strike_price` | Option strike |
| `option_settlement_price` | Option settlement premium |
| `underlying_settlement_price` | Underlying futures settlement used for Greeks |
| `implied_volatility` | Canonical decimal IV |
| `delta` | Black-76 delta |
| `gamma` | Black-76 gamma |
| `vega` | Black-76 vega |
| `theta` | Black-76 theta |
| `rho` | Black-76 rho |
| `dte_days` | Calendar or configured DTE basis, in days |
| `pricing_model` | `black76` for WTI futures options |

Columns that must not appear in the downstream CSV:

- `run_health`
- `quarantine`
- `quarantine_reason`
- `held_back`
- `excluded_from_study`
- `_bound_flag`
- `_missing_flag`
- `_pcp_flag`
- `iv_flag`
- provider payload JSON
- source line number
- raw vendor column names
- dashboard-only labels

## Format and Precision

Canonical CSV formatting:

| Area | Rule |
| --- | --- |
| Header | `lower_snake_case` |
| Dates | ISO 8601 date, `YYYY-MM-DD` |
| Contract month | ISO 8601 date, `YYYY-MM-01`, preserving raw `STRIP` month-date semantics |
| Timestamp | Not repeated in daily downstream CSV unless required by importer |
| Time zone | Manifest/importer policy, not row-level daily CSV |
| Enum values | lowercase, e.g. `call`, `put`, `black76` |
| Product/root values | market codes may remain uppercase, e.g. `WTI`, `CL` |
| Currency | ISO 4217 in manifest, e.g. `USD` |
| Price unit | manifest, e.g. `USD_per_barrel` |
| Price tick | product policy, WTI currently `0.01` per barrel |
| Price decimals | `2` for WTI settlement/strike export |
| IV decimals | `6`, decimal unit not percent |
| Greek decimals | `8` for all exported Greeks |

Example header only:

```csv
trade_date,product,underlying_symbol,option_symbol,contract_month,expiration_date,option_type,strike_price,option_settlement_price,underlying_settlement_price,implied_volatility,delta,gamma,vega,theta,rho,dte_days,pricing_model
```

## Manifest Contract

The downstream export manifest carries policies that should not be repeated on
every row:

```json
{
  "product": "WTI Crude Oil Options",
  "exchange": "NYMEX",
  "underlying_root": "CL",
  "option_root": "LO",
  "currency": "USD",
  "price_unit": "USD_per_barrel",
  "contract_unit": "1000_barrels",
  "price_tick": 0.01,
  "pricing_model": "black76",
  "data_frequency": "daily",
  "date_format": "ISO_8601_YYYY_MM_DD",
  "contract_month_format": "ISO_8601_YYYY_MM_01",
  "contract_month_source_field": "STRIP",
  "contract_month_display": "YYYY-MM",
  "trade_date_meaning": "market_session_date",
  "availability_policy": "available_next_trading_session_after_settlement",
  "tradable_time_policy": "next_trading_session_after_trade_date",
  "exchange_calendar": "NYMEX",
  "timezone": "America/New_York",
  "settlement_timing": {
    "time_kind": "settlement_period_end",
    "local_time": "14:30:00",
    "timezone": "America/New_York",
    "same_day_file_availability_assumption": "not_assumed",
    "source_reference": "https://www.cmegroup.com/trading/energy/files/NYMEX_Energy_Futures_Daily_Settlement_Procedure.pdf"
  },
  "iv_unit": "decimal",
  "iv_decimal_places": 6,
  "greek_decimal_places": 8,
  "quality_gate": "passed"
}
```

Important invariant:

```text
trade_date is not tradable consumer time.
Tradable consumer time is derived by importer policy from trade_date and exchange calendar.
```

For example, a settlement row for `trade_date=2024-09-25` should not be visible
to a downstream backtest as if it was known at the start of `2024-09-25`. The
importer must apply the manifest policy, such as next tradable session after
settlement.

## Migration Plan

1. Keep the existing prepared/dashboard/report artifacts unchanged.
2. Add a downstream export writer behind a config flag or family-specific export path.
3. Write `option_chain_greeks.csv`, `manifest.json`, `data_dictionary.md`, and
   `schema.json` under `data/option_chain_greeks/` in the run output directory.
4. Add paths to `summary.json` under `artifacts`.
5. Ensure dashboard/report continue reading existing review artifacts.
6. Add tests that verify the downstream CSV contains no review/debug columns.
7. Add tests that verify the manifest contains the tradable-time policy and product
   precision policy.
8. Add a small public-safe WTI-style fixture that proves dirty rows are excluded
   from the downstream export but remain visible in review artifacts.

## Acceptance Criteria

- [ ] `data/option_chain_greeks/option_chain_greeks.csv` is written as an additive artifact for WTI-style
      futures options runs.
- [ ] `data/option_chain_greeks/manifest.json` is written beside the CSV.
- [ ] `data/option_chain_greeks/data_dictionary.md` is written beside the CSV.
- [ ] `data/option_chain_greeks/schema.json` is written beside the CSV.
- [ ] Existing `prepared.csv`, `summary.json`, HTML report, and dashboard artifacts
      remain available.
- [ ] `summary.json` includes artifact paths for the downstream CSV and metadata files.
- [ ] Downstream CSV uses finance-friendly headers and no raw vendor column names.
- [ ] Downstream CSV does not include run health, quarantine, held-back, excluded, or
      detailed check flag columns.
- [ ] Downstream CSV includes full Greeks: `delta`, `gamma`, `vega`, `theta`, `rho`.
- [ ] WTI futures options use `pricing_model=black76`.
- [ ] Dates are ISO 8601 date-only fields.
- [ ] WTI prices and strikes export with 2 decimal places.
- [ ] IV exports as decimal unit with 6 decimal places.
- [ ] All Greeks export with 8 decimal places.
- [ ] Manifest records `trade_date_meaning`, `availability_policy`, and
      `tradable_time_policy`.
- [ ] Manifest records product settlement timing policy, including local time,
      timezone, time kind, and source reference.
- [ ] Core export code does not hardcode product symbols, exchange names, price
      units, contract units, or exchange calendars as defaults.
- [ ] Export fails with a clear configuration error if required instrument policy
      is missing.
- [ ] Tests prove `trade_date` is not treated as tradable consumer time.
- [ ] Tests prove rows failing release gates do not appear in the downstream CSV.

## Evidence Required

- `data/option_chain_greeks/option_chain_greeks.csv`
- `data/option_chain_greeks/manifest.json`
- `data/option_chain_greeks/data_dictionary.md`
- `data/option_chain_greeks/schema.json`
- `summary.json` artifact path snapshot
- fixture-based export test
- no-review-columns assertion
- tradable-time policy/importer test or manifest validation test

## Related Checks

- Gate: `G2 Schema + Grain`
- Gate: `G4 Unit Assumptions`
- Gate: `G5 Domain Market Checks`
- Gate: `G6 PIT + Reproducibility`
- Gate: `G7 Dashboard Status`
- Metric: `downstream_export_clean_row_rate`
- Metric: `downstream_export_no_review_columns`
- Metric: `greek_export_completeness_rate`
- Metric: `tradable_time_policy_declared`
- Expected status: downstream export blocked unless source data, IV units, option checks,
  and settlement availability policy pass.
