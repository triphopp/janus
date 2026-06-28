# Option Chain Greeks Data Dictionary for Technical and Domain Users

Urgency: `P0-critical`

Status: `draft`

Source plan:

- `issues/P0-critical/pipeline-data-integrity/022-settlement-availability-anchor.md`
- `issues/P0-critical/pipeline-data-integrity/023-downstream-option-chain-greeks-export.md`
- `issues/P0-critical/pipeline-data-integrity/000-implementation-sequence.md`
- `docs/design/csv_storage_bounded_context_redesign.md`
- `docs/design/data_structure_reading_map.md`

## Summary

Create a data dictionary for the downstream option chain Greeks export that can
be used by both technical implementers and domain experts. The dictionary must explain raw
source mapping, canonical CSV fields, domain display labels, units, precision,
allowed values, and timing policy.

The goal is to make the downstream export self-explanatory without requiring
users to read pipeline code or infer meaning from raw vendor headers.

## Why It Matters

The same field can mean different things to different users:

- `STRIP` is raw vendor data but represents contract month.
- `contract_month` is the canonical CSV field and should use ISO `YYYY-MM-01`.
- Domain review may display the same value as `Nov 2024` or `2024-11`.
- `trade_date` is the market session date, not tradable consumer time.
- `OPTION_VOLATILITY` is raw provider IV, while `implied_volatility` is canonical
  decimal IV.

Without a data dictionary, downstream consumers, quants, and domain reviewers can
interpret the same file differently and reintroduce the data-integrity problems
this P0 work is meant to remove.

## Scope

In scope:

- Add a human-readable data dictionary artifact for the downstream CSV.
- Add a machine-readable schema or metadata artifact for validation.
- Separate technical field definitions from domain display labels.
- Document raw source mapping for every exported field where applicable.
- Document units, decimal precision, allowed values, nullability, and examples.
- Read product units from the run manifest or instrument config, not from
  hardcoded defaults in core code.
- Document timing semantics: `trade_date` is market session date, not
  tradable time.
- Document product timing policy in the data dictionary so domain users can see
  when settlement data becomes eligible for downstream use.
- Document timing source references for WTI and ICE Brent policies.
- Document WTI-specific market conventions used by the export.

Out of scope:

- Building a full enterprise data catalog.
- Replacing the dashboard UI glossary.
- Adding every possible future product family in the first version.
- Documenting raw licensed vendor rows verbatim.

## Dictionary Shape

Use one source of truth with two views:

```text
Technical view:
  column name, dtype, format, unit, precision, nullable, source mapping,
  validation rule, internal notes

Domain view:
  display label, business meaning, market convention, example display value,
  how a domain expert should read it
```

Recommended artifacts:

- `data/option_chain_greeks/data_dictionary.md`
- `data/option_chain_greeks/schema.json`

The generated `data_dictionary.md` must be the human-readable file an agent or
domain reviewer can open beside the CSV to understand:

- what each column means
- how raw fields map to canonical values
- which display labels are finance-friendly
- which time policy prevents one-day settlement leakage

## Required Fields

Each exported CSV column should have at least:

| Metadata | Meaning |
| --- | --- |
| `column_name` | canonical CSV header |
| `domain_label` | finance-friendly display label |
| `description` | domain meaning |
| `source_field` | raw field or derived source |
| `source_transform` | normalization or calculation rule |
| `dtype` | expected technical type |
| `format` | date, decimal, enum, string, integer |
| `unit` | market unit where applicable |
| `precision` | decimal places where applicable |
| `nullable` | whether blank values are allowed |
| `allowed_values` | enum values where applicable |
| `example_canonical` | canonical CSV example |
| `example_display` | domain display example |

## Initial WTI Dictionary Entries

The first version must cover the `023` downstream CSV contract:

| Column | Domain label | Source mapping | Canonical rule |
| --- | --- | --- | --- |
| `trade_date` | Trade Date | raw `TRADE DATE` | ISO `YYYY-MM-DD`; market session date |
| `product` | Product | config/product policy | uppercase market label, e.g. `WTI` |
| `underlying_symbol` | Underlying Futures | derived from contract root/month | market code, e.g. `CLX24` |
| `option_symbol` | Option Contract | derived contract identity | stable option label for downstream use |
| `contract_month` | Contract Month | raw `STRIP` | ISO `YYYY-MM-01`; display may be `YYYY-MM` |
| `expiration_date` | Expiration Date | raw `EXPIRATION DATE` | ISO `YYYY-MM-DD` |
| `option_type` | Option Type | raw `CONTRACT TYPE` | `C -> call`, `P -> put` |
| `strike_price` | Strike Price | raw `STRIKE` | 2 decimals for WTI |
| `option_settlement_price` | Option Settlement Price | raw `SETTLEMENT PRICE` on option rows | 2 decimals, `USD_per_barrel` |
| `underlying_settlement_price` | Underlying Settlement Price | matched futures settlement | 2 decimals, `USD_per_barrel` |
| `implied_volatility` | Implied Volatility | raw `OPTION_VOLATILITY` plus unit registry | decimal unit, 6 decimals |
| `delta` | Delta | Black-76 computed Greek | 8 decimals |
| `gamma` | Gamma | Black-76 computed Greek | 8 decimals |
| `vega` | Vega | Black-76 computed Greek | 8 decimals |
| `theta` | Theta | Black-76 computed Greek | 8 decimals |
| `rho` | Rho | Black-76 computed Greek | 8 decimals |
| `dte_days` | Days To Expiration | `expiration_date - trade_date` using configured DTE basis | integer |
| `pricing_model` | Pricing Model | product policy | `black76` for WTI futures options |

## Display Rules

Canonical CSV values are for machines. Display values are for humans.

Examples:

| Concept | Raw | Canonical CSV | Domain display |
| --- | --- | --- | --- |
| Contract month | `11/1/2024` | `2024-11-01` | `Nov 2024` or `2024-11` |
| Option type | `C` | `call` | `Call` |
| IV | `58.26110` percent | `0.582611` | `58.26%` |
| Price | `34.69000` | `34.69` | `$34.69/bbl` |

## Timing Definition

The dictionary must make this invariant explicit:

```text
trade_date is the market session date described by the data.
trade_date is not the time at which a downstream consumer is allowed to consume the row.
```

The tradable consumer time is derived from the manifest/importer policy:

```text
tradable_time = next_trading_session_after_trade_date(trade_date, exchange_calendar)
```

The downstream CSV remains date-only. Do not add `available_at`,
`decision_time`, or `tradable_time` as row-level CSV columns unless a downstream
consumer explicitly requires a timestamped feed. Those values belong in the
manifest/importer policy and review artifacts.

## Timing and Availability Section

The generated `data_dictionary.md` must include a section named `Timing and
Availability` with both domain and technical explanations.

It must include at least:

| Policy | Required dictionary explanation |
| --- | --- |
| `trade_date` | Market session date described by the row; not the time the row is tradable |
| `availability_policy` | Settlement rows are not available from midnight; availability is derived from product timing policy |
| `tradable_time_policy` | Downstream backtests/importers should consume the row no earlier than the next eligible trading session after the settlement policy allows it |
| `timezone` | Use the exchange/product timezone from the manifest, not the local machine timezone |
| `settlement_timing.source_reference` | Link or cite the product timing source used for the run |

Product timing examples to document:

| Product/source | Time kind | Local time | Timezone | Dictionary note |
| --- | --- | --- | --- | --- |
| NYMEX WTI (`CL`) | settlement period end | `14:30:00` | `America/New_York` | CME WTI daily settlement calculation uses a window ending at 14:30 ET; same-day public file availability is not assumed unless the run declares a real-time settlement feed policy |
| ICE Brent futures | settlement period end | `19:30:00` | `Europe/London` | ICE Brent futures daily settlement uses a two-minute window starting at 19:28 London time |
| Platts Dated Brent | assessment time | `16:30:00` | `Europe/London` | Different benchmark; do not use this timing for ICE Brent futures/options unless the data contract explicitly says the source is Platts Dated Brent |

Recommended source references:

- CME WTI settlement procedure:
  `https://www.cmegroup.com/trading/energy/files/NYMEX_Energy_Futures_Daily_Settlement_Procedure.pdf`
- ICE Brent futures product specification:
  `https://www.ice.com/products/219/brent-crude-futures`
- Platts Dated Brent assessment explanation:
  `https://www.spglobal.com/energy/en/pricing-benchmarks/assessments/crude-oil/dated-brent-price-explained`

The data dictionary may summarize these policies, but the canonical
machine-readable values must come from the generated `manifest.json`.

## Acceptance Criteria

- [ ] A human-readable data dictionary is generated or maintained for the downstream
      option Greeks CSV.
- [ ] A machine-readable schema or metadata artifact exists for validation.
- [ ] Every `023` CSV column has technical and domain definitions.
- [ ] Raw source mapping is documented for every field that comes from vendor data.
- [ ] Derived fields document their calculation or source policy.
- [ ] `contract_month` explicitly documents raw `STRIP` to ISO `YYYY-MM-01`.
- [ ] `trade_date` explicitly documents market-session semantics and tradable-time
      policy separation.
- [ ] The dictionary includes a `Timing and Availability` section.
- [ ] The dictionary documents WTI settlement-period end as
      `14:30:00 America/New_York` when the run is WTI/NYMEX CL.
- [ ] The dictionary documents ICE Brent futures settlement-period end as
      `19:30:00 Europe/London` when the run is ICE Brent futures/options.
- [ ] The dictionary distinguishes Platts Dated Brent `16:30:00 Europe/London`
      from ICE Brent futures/options timing.
- [ ] The dictionary states that `available_at`, `decision_time`, and
      `tradable_time` are not row-level CSV columns in the date-only export.
- [ ] Units and precision match `023`.
- [ ] Price units in `schema.json` and `data_dictionary.md` come from the
      manifest/instrument config for the run.
- [ ] Display labels are finance-friendly and do not require pipeline knowledge.
- [ ] The dictionary is linked from `summary.json` artifacts or the downstream manifest.

## Evidence Required

- `data/option_chain_greeks/data_dictionary.md`
- `data/option_chain_greeks/schema.json`
- dictionary validation test
- `summary.json` or manifest link to data dictionary
- domain review snapshot showing display labels

## Related Checks

- Gate: `G2 Schema + Grain`
- Gate: `G4 Unit Assumptions`
- Gate: `G6 PIT + Reproducibility`
- Metric: `option_chain_greeks_dictionary_column_coverage`
- Metric: `option_chain_greeks_dictionary_source_mapping_coverage`
- Metric: `option_chain_greeks_dictionary_domain_label_coverage`
- Expected status: downstream export cannot be considered complete unless its data
  dictionary covers every exported column.
