# Product Identity Resolver and Option-on-Futures Contract

Urgency: `P1-high`

Status: `draft`

Source references:

- `docs/reference/product_identity/wti/README.md`
- `docs/reference/product_identity/wti/MANIFEST.yaml`
- `docs/reference/product_identity/wti/evidence/ice-mft-data-dictionary-key-fields.csv`
- `docs/reference/product_identity/wti/evidence/local-wti-product-id-profile.csv`

## Summary

Janus needs an explicit product-identity layer between raw settlement ingestion
and pricing/export logic. The current WTI settlement feed shows the issue
clearly: `CONTRACT=T` identifies the ICE WTI futures product family, while
`CONTRACT TYPE=C/P/F` identifies whether the row itself is an option or a
future. `T + C/P` means option on futures; `T + F` means a futures row.

Today this is mostly inferred inside the settlement loader and then downstream
export relies on config-level `export.option_root: LO`. That is too fragile for
mixed feeds, cross-exchange labels, European-vs-American WTI products, and
future product additions.

## Why It Matters

- `PRODUCT_ID` identifies a product family within a provider/feed namespace, not
  the row's instrument type.
- `CONTRACT` identifies the source contract/product symbol, not necessarily the
  row's instrument type.
- `CONTRACT TYPE` identifies the row instrument type and right for the ICE
  settlement feed.
- Pricing models must not be used to infer product identity. A model such as
  `black76` or `black76_baw` answers how to price a row, not whether the source
  product is ICE `T`, CME `LO`, CME `LC`, ICE `WUL`, or ICE `TDE`.
- The current global `option_root` config can mislabel a row if a future feed
  mixes WTI American options, European WTI products, weekly options, or another
  source-native convention.

## Definitions

Use these concepts separately:

| Concept | Example | Meaning |
|---|---|---|
| `source_product_id` | `425` | Provider/feed-specific product id. |
| `source_contract` | `T` | Source product/contract symbol for the family or underlying. |
| `contract_type` | `C`, `P`, `F` | Row instrument type/right from the feed. |
| `instrument_type` | `option`, `future`, `index`, `cash` | Canonical row type in Janus. |
| `underlying_root` | `T` or `CL` | Contract/root used as the underlying for analytics/export. |
| `source_option_root` | `T` for ICE WTI American-style options | Source-native option identity when the feed uses the underlying root. |
| `equivalent_option_root` | `LO` | Optional crosswalk label for another venue/convention. |
| `product_family` | `futures_options`, `equity_options` | Adapter family selected from evidence/config, not inferred from pricing model. |
| `option_underlying_type` | `future`, `spot` | Whether the option exercises into/depends on futures or spot equity/index. |
| `exercise_style` | `american`, `european` | Contract term, not a model choice. |
| `pricing_model` | `black76`, `black76_baw` | Valuation method, not product identity. |

## Current State

Implemented behavior:

- `SettlementLoader` maps raw settlement columns to Janus columns.
- `SettlementLoader` classifies `C/P + strike` as `instrument_type=option`.
- Everything else currently falls through to `instrument_type=future`.
- `core.option_chain_export.export_config()` uses config-level
  `export.option_root` and `export.underlying_root`.

Known weakness:

- `I`, `CASH`, `CS`, unexpected contract types, missing strikes, or conflicting
  option-only fields can be misclassified instead of fail-closed.
- Product identity is not row-level.
- Source-native identity and CME-equivalent labels are not represented
  separately.
- Export identity is not backed by a product-master evidence record.

## Target Policy

Classify row type and product identity in two explicit steps.

### Step 1: Instrument Type

For ICE-style settlement rows:

```text
CONTRACT TYPE C/P + strike present      -> option
CONTRACT TYPE F/M/D + strike missing    -> future
CONTRACT TYPE I                         -> index
CONTRACT TYPE CASH/CS                   -> cash
anything inconsistent                   -> unknown / quarantine
```

Do not silently coerce unknown contract types to futures.

### Step 2: Product Identity

Resolve product identity from a provider-aware product master using the full
tuple:

```text
provider + source_product_id + hub + source_product_name + source_contract
```

For the current WTI feed, the target identity is:

```yaml
provider: ice_settlement_file
source_product_id: 425
hub: WTI
source_product_name: WTI Crude Futures
source_contract: T
instrument_type:
  F: future
  C: option_call
  P: option_put
underlying_root: T
source_option_root: T
product_family: futures_options
option_underlying_type: future
exercise_style: american
source_product_identity: ICE WTI American-Style Options
equivalent_option_roots:
  cme: LO
identity_confidence: high_for_current_feed_tuple
evidence:
  - docs/reference/product_identity/wti/MANIFEST.yaml
```

Important evidence boundary:

- ICE public documentation confirms the meaning of `Product ID` and
  `Contract Type`.
- The local/subscribed feed confirms the observed value `425` for WTI rows.
- No public product-master row explicitly listing
  `PRODUCT_ID=425 -> WTI Crude Futures` has been found yet.

## Adapter Routing Policy

The resolver must emit enough identity for the pipeline to choose the correct
adapter and to reject data that entered the wrong route.

Target routing:

| Identity fields | Correct adapter | Required pricing behavior |
|---|---|---|
| `product_family=futures_options`, `option_underlying_type=future` | `FuturesOptionsAdapter` | Map matching futures rows as `F`; use futures-option engines such as `black76_european` or `black76_baw`. |
| `product_family=equity_options`, `option_underlying_type=spot` | `EquityOptionsAdapter` | Require PIT spot/underlying price `S`; use equity engines such as `bsm` or planned `bsm_baw`. |
| `product_identity_status=unknown/conflict` | none for official runs | Block official pricing/export or quarantine in a research-only run. |

Compatibility requirements:

- A row with equity-option identity must not pass through the settlement
  futures-options path merely because it has `C/P + strike`.
- A futures-option row must not pass through the equity path merely because an
  `underlying_price` column exists.
- `exercise_style` is a contract attribute. A pricing model can be incompatible
  with it, but cannot change it.
- Adapter selection and model selection should produce clear diagnostics before
  IV solving or Greek computation when identity and config disagree.

Suggested diagnostics:

```text
product_family
option_underlying_type
adapter_family_selected
adapter_family_match
adapter_family_reason
contract_exercise_style
```

## Implementation Plan

### Phase 1: Product Master

Add a provider-aware product identity config, suggested path:

```text
configs/symbology/product_identity.yaml
```

Initial schema:

```yaml
products:
  - provider: ice_settlement_file
    source_product_id: 425
    hub: WTI
    source_product_name: WTI Crude Futures
    source_contract: T
    product_family: futures_options
    underlying_root: T
    source_option_root: T
    option_underlying_type: future
    exercise_style: american
    settlement_type: exercise_into_futures
    equivalent_option_roots:
      cme: LO
    evidence_ref: docs/reference/product_identity/wti/MANIFEST.yaml
```

Acceptance for this phase:

- Product master loads without code-level WTI constants.
- Duplicate/conflicting mappings are rejected.
- Missing evidence references are reported.

### Phase 2: Resolver API

Add a small resolver module, suggested path:

```text
ingestion/product_identity.py
```

Responsibilities:

- Normalize raw source fields.
- Classify `instrument_type` from `CONTRACT TYPE` and option-only fields.
- Resolve row-level product identity from the product master.
- Emit auditable fields:

```text
instrument_type
option_right
source_product_id
source_product_name
source_contract
source_contract_type
source_option_root
underlying_root
product_family
option_underlying_type
exercise_style
settlement_type
product_identity_status
product_identity_confidence
product_identity_reason
product_identity_evidence_ref
equivalent_option_root_cme
```

Acceptance for this phase:

- `T + F` resolves to a futures row.
- `T + C/P + strike` resolves to an option-on-futures row.
- WTI option rows stamp `product_family=futures_options` and
  `option_underlying_type=future`.
- `C/P` without strike is `unknown` or quarantined.
- `F/M/D` with strike is `conflict`, not silently accepted.
- `I`, `CASH`, and `CS` are not mislabelled as futures.

### Phase 3: Ingestion Wiring

Wire the resolver into `SettlementLoader` after raw date filtering and before
schema validation.

Acceptance for this phase:

- Existing WTI ingestion still produces the expected option and futures rows.
- Unknown product tuples fail closed with a clear error or quarantine status,
  depending on config.
- The loader no longer has a broad "everything else is future" fallback.
- The selected adapter family must match resolved product identity before any
  IV solving or Greek computation.

### Phase 4: Export Wiring

Update option-chain export to prefer row-level identity fields:

```text
source_option_root / equivalent_option_root_cme / configured fallback
underlying_root from row / configured fallback
```

Policy:

- Source-native export should use source-native identity.
- CME-style export may use `equivalent_option_root_cme`.
- Config-level `export.option_root` is allowed only for explicitly declared
  single-product fallback runs.

Acceptance for this phase:

- Export manifests record whether symbol roots came from row identity,
  product-master crosswalk, or config fallback.
- Mixed roots cannot collide silently.
- Pricing model aliases do not change product roots.

### Phase 5: Manifest and Readiness

Add product identity metadata to run summaries/readiness:

```text
product_identity.status
product_identity.unknown_rows
product_identity.conflict_rows
product_identity.mapping_version_or_hash
product_identity.evidence_refs
```

Acceptance for this phase:

- Runs with unknown identity are marked blocked for official exports.
- Research/debug runs can opt into quarantine mode, but the manifest must show
  the unresolved rows.

## Unit Tests

Add focused tests before enabling broad behavior:

- `tests/test_ingestion/test_product_identity.py`
  - `T + F` resolves to `future`.
  - `T + C + strike` resolves to call option on futures.
  - `T + P + strike` resolves to put option on futures.
  - Resolved WTI options include `product_family=futures_options` and
    `option_underlying_type=future`.
  - `C/P` without strike is rejected or marked `unknown`.
  - `F/M/D` with strike is rejected or marked `conflict`.
  - `I`, `CASH`, `CS` do not become `future`.
  - Unknown `(provider, product_id, hub, product, contract)` fails closed.
  - Duplicate/conflicting product-master rows raise.

- `tests/test_ingestion/test_settlement_loader_product_identity.py`
  - Settlement loader stamps row-level identity fields.
  - Existing WTI-style fixture produces both futures and option rows.
  - Option-only fields are null on futures rows after classification.
  - Equity-option-like rows do not pass through settlement/futures-options
    ingestion as official rows without an explicit identity mapping.

- `tests/test_core/test_option_chain_export_product_identity.py`
  - Export uses row-level source root when requested.
  - CME-equivalent export uses `equivalent_option_root_cme`.
  - `pricing_model=black76_european` does not change `option_root`.
  - Mixed `LO`/`LC` or `T`/`WUL` style rows cannot collide silently.

## Acceptance Criteria

- [ ] Product identity resolution is provider-aware and evidence-backed.
- [ ] `CONTRACT` and `CONTRACT TYPE` are modeled separately.
- [ ] Option-on-futures rows are explicitly labelled as options on an
      underlying futures contract.
- [ ] Adapter routing uses `product_family` and `option_underlying_type` and
      rejects family mismatches.
- [ ] Unknown or inconsistent rows fail closed.
- [ ] Export roots come from row-level identity or an audited fallback.
- [ ] Run manifests expose identity status, confidence, and evidence refs.
- [ ] Unit tests cover option, future, cash/index, unknown, and conflict cases.
- [ ] No pricing model setting can infer or override product identity.

## Evidence Required

- Passing unit tests listed above.
- A small public-safe fixture with mixed `C`, `P`, and `F` rows.
- A run manifest showing product identity status and evidence refs.
- Updated docs pointing to `docs/reference/product_identity/wti/`.

## Related Issues

- `issues/P1-high/pricing-models/031-american-futures-option-pricing-engine-selection.md`
- `issues/P1-high/pricing-models/033-multi-model-pricing-engine-rollout-plan.md`
- `issues/P1-high/output-artifacts/028-run-output-artifact-simplification.md`
- `issues/P1-high/storage-contracts/005-csv-bundle-storage-redesign.md`
