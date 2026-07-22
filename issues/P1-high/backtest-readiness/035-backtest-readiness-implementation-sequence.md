# Backtest Readiness Implementation Sequence

Urgency: `P1-high`

Status: `draft`

Source issues:

- `issues/P1-high/storage-contracts/034-product-identity-resolver-and-option-on-futures-contract.md`
- `issues/P1-high/pricing-models/033-multi-model-pricing-engine-rollout-plan.md`
- `issues/P1-high/pricing-models/031-american-futures-option-pricing-engine-selection.md`
- `issues/P1-high/pricing-models/032-negative-futures-price-pricing-domain-guards.md`
- `issues/P1-high/rates-discounting/030-risk-free-rate-sourcing-and-pit-join.md`
- `issues/P1-high/test-harness/016-end-to-end-fixture-run-and-golden-snapshots.md`
- `issues/P1-high/storage-contracts/005-csv-bundle-storage-redesign.md`
- `issues/P1-high/output-artifacts/028-run-output-artifact-simplification.md`

## Summary

This issue defines the minimum implementation sequence before Janus data should
be treated as backtest-ready. The current priority is not more strategy logic;
it is making sure every option row has a trustworthy identity, the selected
pricing model is compatible with the contract, rates are explicit and PIT, and
the exported artifact is clearly downstream-ready.

Backtests may run in diagnostic mode before every item is complete, but official
or trusted backtests must not hide unknown product identity, model/contract
mismatches, missing rates, or mixed-grain prepared data.

## Highest-Priority Sequence

### 1. Product identity and adapter routing

Primary issue:

- `034-product-identity-resolver-and-option-on-futures-contract.md`

Required before trusted backtest:

- Resolve row-level `instrument_type`, `product_family`,
  `option_underlying_type`, and `exercise_style`.
- Distinguish `CONTRACT` from `CONTRACT TYPE`.
- Label WTI option rows as options on futures, not generic options.
- Reject or quarantine unknown/conflicting rows.
- Prevent equity-option-shaped rows from entering the futures-options adapter
  merely because they have `C/P + strike`.

Backtest risk if skipped:

- The pipeline can price the right numbers with the wrong model family, wrong
  underlying, or wrong export identity.

### 2. Pricing model policy and compatibility gates

Primary issue:

- `033-multi-model-pricing-engine-rollout-plan.md`

Related issues:

- `031-american-futures-option-pricing-engine-selection.md`
- `032-negative-futures-price-pricing-domain-guards.md`

Required before trusted backtest:

- Add a pricing model selection policy:

```yaml
pricing_model_policy:
  futures_options:
    european:
      default: black76_european
    american:
      # Official target once implemented and validated.
      default: black76_baw
      # Diagnostic-only fallback; never automatic for official runs.
      temporary_fallback: black76_european
      fallback_label: european_approximation_for_american_contract
  equity_options:
    european:
      default: bsm
    american:
      # Official target once implemented and validated.
      default: bsm_baw
      # Diagnostic-only fallback; never automatic for official runs.
      temporary_fallback: bsm
      fallback_label: european_approximation_for_american_contract
```

Model-resolution contract:

1. Resolve product identity first. If identity is `unknown` or `conflict`, stop
   before model selection.
2. `pricing_model=auto` resolves the policy target from
   `product_family + option_underlying_type + exercise_style`.
3. If the policy target is not implemented, official runs fail with
   `pricing_model_not_implemented`.
4. Diagnostic runs may use `temporary_fallback` only when the user explicitly
   allows approximation.
5. `compare_models` are diagnostic artifacts only; they never replace the
   canonical model chosen for the run.

- Add compatibility diagnostics:

```text
pricing_model_target
pricing_model_source
pricing_model_runtime_status
pricing_model_contract_match
pricing_model_contract_reason
contract_exercise_style
selected_model_exercise_style
is_model_approximation
```

- Add CLI support for explicit model behavior:

```text
janus run WTI --pricing-model auto
janus run WTI --pricing-model black76_european --allow-model-approximation
janus run WTI --pricing-model black76_baw --compare-model black76_european
```

The `black76_baw` command shape is the intended interface after that engine is
implemented. Until then, WTI American-style contracts can only use
`black76_european` as an explicitly labelled diagnostic approximation.

- Keep advanced override compatibility:

```text
--override pricing.model=black76_european
--override pricing_model=black76_european
```

Backtest risk if skipped:

- American contracts may be priced with European models without visible
  approximation status.
- Unknown model defaults can become silent assumptions.
- Negative or zero futures domains may be mixed into IV/Greek failures without
  a clear `pricing_domain_reason`.

### 3. Rate sourcing and PIT discounting

Primary issue:

- `030-risk-free-rate-sourcing-and-pit-join.md`

Required before trusted backtest:

- Finish SOFR or public-safe sourced-rate fixture.
- Gate official runs on sourced-rate coverage.
- Keep constant rate fallback visible and non-green for official runs.
- Add PCP-implied-rate cross-check once option identity and clean pair
  selection are available.

Backtest risk if skipped:

- `theta`, `rho`, IV inversion, and discounting can be internally consistent
  but economically wrong.

### 4. End-to-end fixture and golden snapshots

Primary issue:

- `016-end-to-end-fixture-run-and-golden-snapshots.md`

Required before trusted backtest:

- Add a small public-safe fixture that runs the full pipeline.
- Include both option and futures support rows for futures-options tests.
- Assert identity fields, pricing model metadata, rate summary, export
  eligibility, and output artifacts.

Backtest risk if skipped:

- Unit tests can pass while the actual run path produces incomplete or
  misleading artifacts.

### 5. Canonical storage/export grain

Primary issue:

- `005-csv-bundle-storage-redesign.md`

Related issue:

- `028-run-output-artifact-simplification.md`

Required before trusted backtest:

- Separate futures support rows, option contracts, analytics, quality checks,
  and run health into explicit grains.
- Mark the old wide prepared artifact as debug or compatibility-only.
- Put downstream-ready files under `exports/`.
- Write `artifacts.json` so tools can find the canonical backtest input
  without guessing from filenames.

Backtest risk if skipped:

- A downstream backtest can accidentally consume mixed-grain debug data instead
  of the clean option-chain export.

## Diagnostic Backtest Policy

Diagnostic backtests are allowed before every production engine exists, but
they must be visibly labelled.

WTI example while `black76_baw` is not implemented:

```text
contract_exercise_style = american
pricing_model = black76_european
pricing_model_contract_match = false
pricing_model_contract_reason = european_approximation_for_american_contract
is_model_approximation = true
run_trust_level = diagnostic
```

Official backtests must fail or block when:

- product identity is unknown or conflicting;
- adapter family does not match row identity;
- selected pricing model is incompatible and approximation is not explicitly
  allowed;
- required rate source is configured but missing;
- canonical downstream export is unavailable or mixed-grain.

## Work That Should Not Block First Backtest-Ready Data

These issues are useful but should not be started ahead of the sequence above:

- `006-dashboard-domain-language.md` — wait for stable summary/artifact fields.
- `008-equity-price-trust-audit.md` — required before trusted equity backtests,
  not before WTI option-chain backtests.
- `019-equity-factor-attribution-pit-data.md` — factor attribution is outside
  the current option-pricing readiness path.
- `018-transaction-cost-calibration.md` — required before trusted net-PnL
  conclusions, but after data identity/pricing/export correctness.
- `029-second-order-greek-pnl-attribution.md` — wait until pricing engines and
  Greek metadata are stable.
- Large package moves in `027-janus-scope-and-package-refactor.md` — keep only
  fixture/safety harness work on the near-term path.

## Acceptance Criteria

- [ ] A run can report row-level product identity for futures rows and option
      rows.
- [ ] Adapter routing fails closed when identity and configured family disagree.
- [ ] `pricing_model=auto` selects from product identity and contract style.
- [ ] Official runs block unknown identity, unimplemented policy targets, and
      model/contract mismatches.
- [ ] Diagnostic runs can use a declared approximation and stamp that fact in
      summary/export metadata.
- [ ] Temporary fallback models are never applied automatically in official
      runs.
- [ ] Rate summary shows sourced coverage and fallback count.
- [ ] A public-safe fixture proves the end-to-end path.
- [ ] Downstream-ready backtest inputs are separated from debug/prepared data.
- [ ] The run artifact index identifies the canonical downstream export.

## Evidence Required

- Unit tests for product identity, adapter routing, pricing model policy, and
  override/CLI behavior.
- End-to-end fixture output with summary, rate summary, identity diagnostics,
  and downstream export artifact.
- Export schema/data dictionary showing identity, pricing model, and
  approximation fields.
- `git diff --check` and focused test output for each phase.
