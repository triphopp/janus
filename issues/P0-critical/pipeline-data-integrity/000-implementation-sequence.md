# Pipeline Data Integrity Implementation Sequence

Urgency: `P0-critical`

Status: `draft`

Primary output contract:

- `023-downstream-option-chain-greeks-export.md`
- `024-option-chain-greeks-data-dictionary.md`

Related issues:

- `001-wti-incident-regression.md`
- `002-unit-registry-iv-scaling.md`
- `003-option-market-checks-run-status.md`
- `004-pit-reproducibility-official-runs.md`
- `012-split-date-and-contract-grain.md`
- `013-exchange-calendar-coverage.md`
- `014-versioned-cache-wiring.md`
- `015-event-calendar-pit-normalization.md`
- `022-settlement-availability-anchor.md`
- `023-downstream-option-chain-greeks-export.md`
- `024-option-chain-greeks-data-dictionary.md`

## Agent Read Order

Agents implementing or reviewing this release train should read these files in
this order:

1. `000-implementation-sequence.md` - overall dependency order, guardrails, and
   required test matrix.
2. `022-settlement-availability-anchor.md` - authoritative settlement timing and
   point-in-time availability policy.
3. `023-downstream-option-chain-greeks-export.md` - downstream CSV, manifest, and
   artifact contract.
4. `024-option-chain-greeks-data-dictionary.md` - human/domain data dictionary
   contract, including timing definitions that must be written beside the CSV.

For implementation, `000` is the entry point. For output-format questions, use
`023` first and then `024`. For any time-zone, settlement, `available_at`, or
tradable-time question, use `022` as the policy source and mirror the relevant
human-readable explanation into the generated `data_dictionary.md`.

## Summary

Implement the P0 pipeline data-integrity work as one coordinated release train,
using the downstream option chain Greeks export in `023` as the target output
contract.

The work can ship as one logical set, but it should be implemented in ordered
steps so the existing dashboard/report artifacts keep working while a new clean
downstream dataset is added.

Target shape:

```text
raw vendor data
  -> versioned source + unit assumptions + availability policy
  -> grain-safe option/futures canonical frames
  -> option market checks + review artifacts
  -> release gate
  -> data/option_chain_greeks/option_chain_greeks.csv
  -> data/option_chain_greeks/manifest.json
```

Important separation:

```text
Dashboard/review artifacts show problems and reasons.
Downstream CSV contains only clean market facts plus full Greeks.
Manifest/importer carries tradable-time policy.
```

## Can This Be Done As One Set?

Yes, with one constraint: `023` should be the final export contract, not the
first implementation step.

The blockers for a trustworthy downstream export are:

- source identity and reproducibility
- IV unit normalization
- settlement availability policy
- exchange calendar policy
- date-grain vs contract-grain separation
- option-domain checks that can block export

Once those are in place, the downstream export is mostly an additive writer.

## Real WTI Smoke Evidence

Smoke test on the real WTI file for `2024-09-25` validated the release train
direction:

- Settlement availability anchored to `14:30 America/New_York`, producing
  `2024-09-25T18:30:00Z` instead of the old midnight-derived `03:00Z` leak.
- Raw IV is preserved while canonical IV is normalized from percent to decimal
  (`58.26%` -> `0.5826`).
- Option-market readiness blocked the run because IV mismatch was `20.9%`, above
  the `20%` block threshold.
- The downstream export path handled the real-window scale quickly; row iteration
  is not the immediate bottleneck at the observed smoke-test size.

This is expected P0 behavior: the pipeline should refuse to publish a downstream
market dataset when WTI option checks show a material mismatch.

## Dependency Order

### Phase 0 - Public-Safe Regression Fixture

Issues:

- `001`

Purpose:

- Create a minimized WTI-style fixture with both futures rows and option rows.
- Prove the old failure mode exists without publishing licensed vendor rows.
- Use this fixture across later phases.

Exit criteria:

- Fixture contains source futures and option rows.
- Row reconciliation uses domain keys, not row index.
- The fixture can demonstrate IV mismatch, PCP mismatch, and grain separation.

### Phase 1 - Source, Units, Cache, and Timing Foundation

Issues:

- `002`
- `004`
- `014`
- `022`
- `013`

Purpose:

- Pin inputs for official runs.
- Preserve raw IV plus unit assumption before canonical IV is computed.
- Fix settlement availability so it is not anchored to midnight.
- Use exchange/provider calendars where available.
- Record source hash, config hash, code version, calendar id, and availability
  policy in manifests.

Exit criteria:

- Official run without fixed source version is blocked or explicitly exploratory.
- Unknown IV unit blocks official export.
- WTI settlement is not available before local session close or configured
  settlement release time.
- Calendar fallback is visible as `generic`, not silently treated as exchange truth.

### Phase 2 - Grain Separation and Domain Checks

Issues:

- `012`
- `003`

Purpose:

- Keep market price/date-grain work separate from option-contract-grain work.
- Compute option-domain checks as review artifacts.
- Use option checks to decide export eligibility.

Exit criteria:

- Rolling/date-level features cannot run on a mixed option-chain frame.
- Futures/support rows are not mixed with option contract rows in the downstream export.
- IV mismatch, PCP mismatch, premium sanity, delta sanity, and missing underlying
  match can block or review the run.

### Phase 3 - Downstream Export and Data Dictionary Additive Artifacts

Issues:

- `023`
- `024`

Purpose:

- Add `data/option_chain_greeks/option_chain_greeks.csv`.
- Add `data/option_chain_greeks/manifest.json`.
- Add a human-readable data dictionary for domain and technical users.
- Add a machine-readable schema or metadata artifact for validation.
- Keep `prepared.csv`, `summary.json`, HTML report, and dashboard outputs intact.

Exit criteria:

- Downstream CSV uses finance-friendly headers.
- Downstream CSV contains only clean market facts plus Greeks.
- Downstream CSV has no quarantine, held-back, excluded, run-health, raw vendor, or
  dashboard/debug columns.
- Downstream manifest declares product, exchange, precision, IV unit, pricing model,
  exchange calendar, and tradable-time policy.
- Data dictionary covers every exported downstream CSV column with technical definition,
  domain label, source mapping, unit, precision, and display guidance.

### Phase 4 - Event Calendar PIT, If Event Features Are Exported

Issues:

- `015`

Purpose:

- Normalize event calendars with `available_at` before any event feature can be
  exported or used in a decision.

Exit criteria:

- Event-derived fields are absent from the downstream CSV unless their availability policy
  passes PIT checks.
- Missing event availability is review/block in dashboard artifacts, not silent
  pass.

## Resolved Design Decisions

### Downstream CSV Is Date-Only

The downstream CSV should not repeat timestamps when the source is daily settlement
data.

Use:

```text
trade_date = market session date
```

Do not treat `trade_date` as tradable consumer time.

The manifest/importer must derive tradable time:

```text
tradable_time = next_trading_session_after_trade_date(trade_date, exchange_calendar)
```

### `decision_time` Is Internal Policy, Not a Downstream CSV Column

Some older issues mention `available_at <= decision_time`. That remains valid as
an internal PIT invariant for pipeline checks.

For the downstream export:

- `decision_time` does not need to appear in the CSV.
- The importer/backtest protocol derives tradable time from manifest policy.
- Tests must prove `trade_date` is not used as if it were immediately tradable.

### Review Artifacts Are Not Training Data

The following belong in dashboard/review artifacts or manifests, not in the downstream
CSV:

- run health
- quarantine rows
- held-back rows
- excluded-from-study rows
- detailed flags
- raw provider payload
- source line number

If a row is not clean enough for release, it should not appear in
`data/option_chain_greeks/option_chain_greeks.csv`.

## Target Downstream CSV Columns

The `023` format is the primary contract:

```csv
trade_date,product,underlying_symbol,option_symbol,contract_month,expiration_date,option_type,strike_price,option_settlement_price,underlying_settlement_price,implied_volatility,delta,gamma,vega,theta,rho,dte_days,pricing_model
```

## Target Format Rules

| Area | Rule |
| --- | --- |
| Header | `lower_snake_case` |
| Dates | ISO 8601 date, `YYYY-MM-DD` |
| Contract month | ISO 8601 date, `YYYY-MM-01`, preserving raw `STRIP` month-date semantics |
| Timestamp | not repeated in daily downstream CSV |
| Time zone | manifest/importer policy |
| Enum values | lowercase, e.g. `call`, `put`, `black76` |
| Market codes | uppercase where domain-standard, e.g. `WTI`, `CL` |
| Currency | ISO 4217 in manifest, e.g. `USD` |
| Price unit | manifest, e.g. `USD_per_barrel` |
| Price decimals | 2 for WTI settlement/strike export |
| IV decimals | 6, decimal unit |
| Greek decimals | 8 for all Greeks |

## Expected Artifact Set

Existing artifacts must remain:

- `prepared.csv`
- `prepared.parquet`
- `summary.json`
- HTML report
- dashboard/report tables

New artifacts:

- `data/option_chain_greeks/option_chain_greeks.csv`
- `data/option_chain_greeks/manifest.json`
- `data/option_chain_greeks/data_dictionary.md`
- `data/option_chain_greeks/schema.json`

`summary.json` should include:

```json
{
  "artifacts": {
    "option_chain_greeks_csv": "...",
    "option_chain_greeks_manifest": "...",
    "option_chain_greeks_data_dictionary": "...",
    "option_chain_greeks_schema": "..."
  }
}
```

## Implementation Guardrails

- Do not remove existing prepared/dashboard/report outputs in the first pass.
- Do not rename existing prepared columns as part of this export.
- Do not export review/debug columns to the downstream dataset.
- Do not put instrument-specific defaults such as symbols, exchange names, units,
  contract units, or calendars in `core/` export code. Those values must come
  from instrument configs.
- Do not use raw row index for reconciliation.
- Do not infer settlement availability from midnight.
- Do not allow unknown IV unit assumptions into official downstream export.
- Do not treat `not_checked` as pass.
- Do not ship the downstream export as complete without a data dictionary covering every
  exported column.

## Agent Test Matrix

Agents implementing this release train should add or update tests in small,
traceable groups. Test names below are suggested names; exact file placement may
follow the repo's existing `tests/` layout.

### Fixture and Regression Tests

Issue coverage:

- `001`

Required tests:

- `test_wti_fixture_contains_futures_and_option_rows`
- `test_row_reconciliation_rejects_row_index_join`
- `test_wti_incident_sets_run_readiness_review_or_blocked`

Required evidence:

- public-safe WTI-style fixture
- row reconciliation artifact
- run summary readiness status

### Unit and IV Scaling Tests

Issue coverage:

- `002`

Required tests:

- `test_iv_percent_raw_converts_to_decimal`
- `test_unknown_iv_unit_blocks_official_export`
- `test_percent_iv_treated_as_decimal_blocks`
- `test_decimal_iv_divided_twice_blocks`
- `test_unit_assumptions_written_to_manifest`

Required evidence:

- `unit_assumptions.json` or manifest unit section
- failing examples for 100x and 0.01x mistakes

### Source, Cache, and Reproducibility Tests

Issue coverage:

- `004`
- `014`

Required tests:

- `test_official_run_requires_fixed_input_version`
- `test_provider_direct_mode_is_exploration_only`
- `test_manifest_records_source_config_code_and_output_hashes`
- `test_rerun_output_hash_mismatch_blocks_unless_explained`
- `test_pipeline_reads_committed_fixture_from_versioned_cache`

Required evidence:

- run manifest
- versioned cache fixture run
- rerun hash comparison

### Settlement Availability and Calendar Tests

Issue coverage:

- `013`
- `022`

Required tests:

- `test_settlement_available_at_not_anchored_to_midnight`
- `test_us_settlement_not_available_before_local_session_close`
- `test_settlement_utc_boundary_conversion`
- `test_missing_settlement_availability_policy_blocks_official_run`
- `test_coverage_records_calendar_id`
- `test_exchange_calendar_differs_from_generic_weekdays`

Required evidence:

- PIT boundary fixture
- availability inference artifact
- coverage report with `calendar_id`

### Grain and Option Market Check Tests

Issue coverage:

- `003`
- `012`

Required tests:

- `test_market_price_rows_and_option_contract_rows_are_separate_grains`
- `test_rolling_operation_rejects_mixed_grain_option_chain`
- `test_same_date_shuffle_does_not_change_date_level_features`
- `test_future_truncation_does_not_change_past_features`
- `test_iv_mismatch_rate_can_block_export`
- `test_pcp_mismatch_rate_can_block_export`
- `test_missing_underlying_match_can_block_export`
- `test_not_checked_is_not_pass`
- `test_disabled_option_check_is_visible_not_clean`

Required evidence:

- grain contract tests
- market checks summary
- run summary option-market status

### Downstream Export Contract Tests

Issue coverage:

- `023`

Required tests:

- `test_option_chain_greeks_csv_written_additively`
- `test_option_chain_greeks_summary_artifact_paths_written`
- `test_option_chain_greeks_has_expected_columns_in_order`
- `test_option_chain_greeks_has_no_review_or_raw_vendor_columns`
- `test_option_chain_greeks_exports_full_greeks`
- `test_option_chain_greeks_uses_black76_for_wti`
- `test_option_chain_greeks_formats_dates_as_iso`
- `test_option_chain_greeks_formats_contract_month_as_yyyy_mm_01`
- `test_option_chain_greeks_formats_wti_prices_to_2_decimals`
- `test_option_chain_greeks_formats_iv_to_6_decimals`
- `test_option_chain_greeks_formats_greeks_to_8_decimals`
- `test_option_chain_greeks_requires_instrument_policy_from_config`
- `test_option_chain_greeks_schema_units_come_from_config`
- `test_trade_date_is_not_treated_as_tradable_time`
- `test_rows_failing_release_gate_are_absent_from_downstream_csv`
- `test_existing_prepared_summary_dashboard_artifacts_remain_available`

Required evidence:

- downstream CSV golden snapshot
- manifest golden snapshot
- dashboard/report compatibility snapshot

### Data Dictionary and Schema Tests

Issue coverage:

- `024`

Required tests:

- `test_data_dictionary_exists_for_option_chain_greeks`
- `test_schema_json_exists_for_option_chain_greeks`
- `test_dictionary_covers_every_exported_column`
- `test_schema_covers_every_exported_column`
- `test_dictionary_documents_raw_source_mapping`
- `test_dictionary_documents_domain_labels`
- `test_dictionary_documents_units_precision_and_allowed_values`
- `test_dictionary_documents_trade_date_timing_semantics`
- `test_dictionary_documents_settlement_timing_policy`
- `test_dictionary_references_settlement_timing_sources`
- `test_dictionary_distinguishes_ice_brent_from_platts_dated_brent`
- `test_dictionary_documents_contract_month_raw_to_canonical_display`

Required evidence:

- `data/option_chain_greeks/data_dictionary.md`
- `data/option_chain_greeks/schema.json`
- dictionary/schema validation result

### Event PIT Tests

Issue coverage:

- `015`

Required tests:

- `test_event_csv_ingestion_produces_available_at`
- `test_event_feature_join_requires_available_at_before_tradable_time`
- `test_missing_event_availability_is_not_checked_or_blocked`
- `test_event_released_after_decision_is_rejected`

Required evidence:

- event fixture
- PIT join test artifact
- run summary event-check status

## Acceptance Criteria

- [ ] The P0 issues can be executed as one release train with `023` as the final
      output contract.
- [ ] `001` fixture is reused by unit, grain, timing, checks, and downstream export tests.
- [ ] Internal PIT checks support derived tradable time even though the downstream CSV is
      date-only.
- [ ] Existing dashboard/report artifacts remain backward compatible.
- [ ] New downstream artifacts are additive and linked from `summary.json`.
- [ ] Data dictionary and schema artifacts are additive and linked from
      `summary.json`.
- [ ] Rows failing release gates remain visible in review artifacts but absent from
      `data/option_chain_greeks/option_chain_greeks.csv`.
- [ ] The downstream manifest declares all policies required to avoid one-day settlement
      leakage.
- [ ] The data dictionary documents raw, canonical, and display meanings for
      all exported fields.

## Evidence Required

- dependency-ordered implementation checklist
- public-safe WTI-style fixture
- Downstream CSV golden snapshot
- Downstream manifest golden snapshot
- Data dictionary snapshot
- Schema snapshot
- dashboard/report compatibility snapshot
- PIT/leakage test proving `trade_date` is not tradable time
