# Multi-Model Pricing Engine Rollout Plan

Urgency: `P1-high`

Status: `in_progress`

Source issues:

- `031-american-futures-option-pricing-engine-selection.md`
- `032-negative-futures-price-pricing-domain-guards.md`
- `030-risk-free-rate-sourcing-and-pit-join.md` (rate input dependency)
- `034-product-identity-resolver-and-option-on-futures-contract.md` (identity
  and adapter-routing dependency)

## Summary

Janus should support multiple option pricing engines, but exactly one engine
must be canonical for a run. The user chooses it through config or CLI. Other
models can be run in comparison mode, but they must be labeled diagnostic and
must not silently replace canonical IV, Greeks, readiness, or export values.

The plan is to introduce a pricing model registry first, then add engines in
small validated steps:

1. Registry and metadata.
2. Lognormal domain guards.
3. Bachelier/normal for negative and near-zero underlyings.
4. Shifted Black-76.
5. American approximations.
6. Tree/reference engines.
7. Optional PDE/high-trust reference.

## Implementation Status

Updated: `2026-07-07`

Implemented prerequisites:

- Issue `030` introduced `core/rates.py` and moved pricing/Greek rate
  resolution to a single stamped `df["r"]` contract.
- `resolve_greek_inputs()` now treats missing `r` as `missing_rate` instead of
  inventing a fallback.
- `run_greeks.py --input`, adapter prep, and option-chain export now consume
  the same row-level `r` contract.

Implemented pricing-model foundation:

- `core/pricing_models.py` now provides a shared registry and metadata contract.
- `black76_european` is implemented as an explicit runtime alias of current
  `black76`; existing `black76`, `bs`, and `bsm` behavior remains unchanged.
- CLI choices, export schema allowed values, scalar pricing, Greeks, and PCP
  parity mode now resolve through the registry.
- Scalar `core.pricing.price()`, `core.pricing.solve_iv()`,
  `core.greeks.single_leg_greeks()`, and `core.greeks.bump_greeks()` now fail
  closed for invalid lognormal domains without NumPy runtime warnings.
- `resolve_greek_inputs()` now splits `missing_underlying` from
  `nonpositive_underlying`.
- Export manifests now include model family, dynamics, exercise style,
  approximation, and parity-check mode.

Still open:

- `bachelier`/`normal`, `black76_shifted`, `black76_baw`, and reference engines
  are registered as planned metadata but are not runtime pricing/Greek engines.
- Row-level `pricing_domain_valid` / `pricing_domain_reason` columns are not yet
  stamped across prepared option artifacts.
- American no-arbitrage bounds are not implemented; non-European PCP equality is
  disabled by registry parity mode for now.

Recommended next step:

- Choose the next runtime engine based on validation priority: Phase 3
  Bachelier/normal for negative-price stress coverage, or Phase 5 BAW for
  American exercise residuals.

## Target Option Behavior Matrix

The future behavior should be explicit by instrument family and exercise style.
Product identity chooses the family and contract terms; pricing model selection
chooses the valuation engine. A model must never infer or overwrite product
identity.

| Input contract type | Identity output | Adapter | Canonical model target | Expected behavior |
|---|---|---|---|---|
| WTI futures support row | `instrument_type=future`, `product_family=futures_options` | `futures_options` support row | none | Preserve the futures row and use it as the PIT underlying map for matching option rows. |
| European option on futures | `instrument_type=option`, `option_underlying_type=future`, `exercise_style=european` | `futures_options` | `black76_european` | Price/solve IV/Greeks with Black-76, run European futures PCP equality, export model metadata. |
| American option on futures | `instrument_type=option`, `option_underlying_type=future`, `exercise_style=american` | `futures_options` | `black76_baw` first, tree reference later | Price with an American approximation once implemented, disable European PCP equality, stamp numerical Greek method until analytic Greeks are validated. |
| European equity/index option | `instrument_type=option`, `option_underlying_type=spot`, `product_family=equity_options`, `exercise_style=european` | `equity_options` | `bsm` / `bs` | Use spot `S`, dividend yield `q`, BSM IV/Greeks, and equity-style PCP equality. |
| American equity option | `instrument_type=option`, `option_underlying_type=spot`, `product_family=equity_options`, `exercise_style=american` | `equity_options` | `bsm_baw` or tree reference | Do not label plain BSM Greeks as American Greeks; route to planned American equity engine or mark unsupported. |
| Negative or zero futures underlying | Same identity as the contract, plus domain diagnostics | family-specific adapter | `bachelier` or explicit shifted model | Preserve real data, mark lognormal models unsupported, price only under a user-selected negative-domain-capable model. |
| Unknown, mixed, or inconsistent product | `product_identity_status=unknown/conflict` | none for official runs | none | Fail closed or quarantine; no canonical IV/Greeks/export until identity is resolved. |

Current implementation status against this matrix:

- European futures options and European equity options are implemented through
  `black76`/`black76_european`, `bs`, and `bsm`.
- American futures and American equity engines are registered but not runtime
  implementations.
- Negative-domain engines are registered as planned metadata only.
- Product-family and exercise-style routing depends on issue `034`; today the
  settlement loader still infers option/future shape before any evidence-backed
  product identity resolver exists.

Compatibility rules:

- `product_family=futures_options` may use `black76`, `black76_european`,
  `black76_baw`, `black76_shifted`, `black76_shifted_baw`, `bachelier`, or
  reference tree/PDE engines when explicitly selected.
- `product_family=equity_options` may use `bs`, `bsm`, `bsm_baw`, `bachelier`,
  or reference tree/PDE engines when explicitly selected.
- If `exercise_style=american` and the selected model is European-only, the run
  must either mark `pricing_model_contract_mismatch` or run in an explicitly
  declared approximation mode.
- `pricing_model=auto` resolves a target model from product identity and
  contract style. If that target is not implemented, official runs must block;
  diagnostic runs may use a configured temporary fallback only when
  approximation is explicitly allowed.
- If a model's domain rejects a row, the row should receive
  `pricing_domain_valid=false` and a reason; the system must not silently switch
  to another model.

## Design Principles

- User-selected model, not implicit model switching.
- One canonical `pricing.model` per run.
- Optional `pricing.compare_models` for diagnostics.
- Every output records model identity and assumptions.
- Product identity and exercise style come from the identity resolver or
  instrument config, not from the model name.
- Adapter routing must fail closed when row identity and configured family
  disagree.
- Invalid model domain is a data-quality state, not a crash.
- Pricing engines consume row-level `r` resolved by `core/rates.py`; they do
  not own rate sourcing or fallback policy.
- Negative futures prices can be real data; they are not automatically vendor
  errors.
- American approximation models must not reuse European put-call parity equality
  as a hard gate.
- Fast closed-form models remain the default path for large chains; slow
  numerical engines are opt-in or reference-only.

## Target Config

```yaml
pricing:
  model: black76_baw
  compare_models: [black76, bachelier]
  model_domain_policy: fail_closed
  greek_method: auto

  shifted_black:
    shift: null
    shift_source: configured

  baw:
    boundary_solver: brent
    boundary_tol: 1.0e-8
    max_iter: 100

  tree:
    steps: 500
    exercise: american

  reference:
    enabled: false
    model: trinomial
    sample_size: 1000
    cache: true
    execution: parallel
    max_workers: auto
    timeout_seconds: 600
    fail_on_timeout: false
```

CLI:

```bash
python run_greeks.py --input wti_options.csv --model bachelier --output outputs/greeks/wti.parquet
python run_greeks.py --input wti_options.csv --model black76_baw --compare-model black76 --compare-model bachelier --output outputs/greeks/wti.parquet
```

## Model Registry Contract

Create a registry record per engine:

```text
PricingModelSpec
  name
  aliases
  family
  exercise_style
  price_dynamics
  supports_negative_underlying
  requires_shift
  supports_closed_form_price
  supports_closed_form_greeks
  default_greek_method
  parity_check_mode
  speed_tier
  maturity
```

Initial registry:

| Model | Dynamics | Exercise | Negative underlying | Role |
|---|---|---|---|---|
| `black76` | lognormal | European | No | Current default, backward compatibility. |
| `black76_european` | lognormal | European | No | Explicit alias for current futures option model. |
| `bachelier` / `normal` | normal | European | Yes | Primary stress model for zero/negative futures. |
| `black76_shifted` | shifted lognormal | European | Yes if shifted positive | Alternative stress model with explicit displacement. |
| `black76_baw` | lognormal | American approximation | No | Fast American futures approximation. |
| `black76_shifted_baw` | shifted lognormal | American approximation | Yes if shifted positive | Later American stress candidate. |
| `bsm` | lognormal | European | No | Current equity/index model. |
| `bsm_baw` | lognormal | American approximation | No | Equity American approximation candidate. |
| `bjerksund_stensland` | lognormal | American approximation | No | Alternative American approximation for comparison. |
| `ju_zhong` | lognormal | American approximation | No | Higher-order approximation candidate. |
| `crr_binomial` | tree | European/American | Config dependent | Slow validation/reference engine. |
| `trinomial` | tree | European/American | Config dependent | More stable reference engine. |
| `finite_difference` | PDE | European/American | Config dependent | High-trust reference for boundary-sensitive cases. |

Out of initial scope:

- Heston, SABR, local volatility, and stochastic volatility calibration. These
  are surface/calibration models, not simple per-row pricing engines for the
  current pipeline.

## Phase 0 - Characterization

- Add tests pinning current `black76`, `bs`, and `bsm` behavior.
- Add tests showing current scalar `price()` and `single_leg_greeks()` emit
  invalid-log warnings for `F <= 0`.
- Add tests showing batch Greeks already return `NaN` for invalid domains.
- Add a WTI negative-price fixture with `F = -37.63`, positive option premium,
  positive strike, positive IV, and valid expiry.

Exit criteria:

- Current behavior is documented by tests before refactor.
- No production behavior changes yet.

## Phase 1 - Registry and Selector

- Completed `2026-07-07`: create `core/pricing_models.py`.
- Completed `2026-07-07`: move supported implemented model names, aliases,
  metadata, and allowed config values into
  the registry.
- Completed `2026-07-07`: update `core/pricing.py`, `core/greeks.py`, `run_greeks.py`,
  `adapters/options_base.py`, and `core/option_chain_export.py` to resolve
  models through the registry.
- Completed `2026-07-07`: preserve `black76`, `bs`, and `bsm` outputs exactly.
- Completed `2026-07-07`: make unknown model errors list supported model names.

Exit criteria:

- `pricing.model` uses registry validation in core pricing, Greeks, CLI,
  adapter PCP, and export metadata.
- CLI choices are generated from the implemented registry subset.
- Existing golden tests pass unchanged.

## Phase 2 - Domain Guards

- Completed `2026-07-07`: add a shared domain validator for active/planned
  dynamics families:
  - lognormal: `S_or_F > 0`, `K > 0`, `T > 0`, `sigma > 0`
  - normal: finite `S_or_F`, `K`, `T > 0`, `sigma > 0`
  - shifted lognormal: `S_or_F + shift > 0`, `K + shift > 0`
- Completed `2026-07-07`: make `price()`, `solve_iv()`, `single_leg_greeks()`, and `bump_greeks()`
  fail closed without NumPy runtime warnings.
- Add row-level diagnostics:
  `pricing_domain_valid`, `pricing_domain_reason`,
  `pricing_model_supported`.
- Completed `2026-07-07`: split `missing_underlying` from `nonpositive_underlying`.

Exit criteria:

- Invalid lognormal domains return `NaN` or clear domain errors by policy.
- Negative WTI rows are preserved but marked unsupported for `black76`.

## Phase 2B - Family and Exercise-Style Compatibility Gates

- Consume row-level identity fields from issue `034` when available:
  `product_family`, `option_underlying_type`, `exercise_style`,
  `settlement_type`, and `product_identity_status`.
- Validate the selected adapter against identity:
  - futures options require `option_underlying_type=future` for option rows and
    matching futures support rows;
  - equity options require `option_underlying_type=spot` and a PIT underlying
    spot price;
  - unknown/conflict rows cannot enter official pricing/export.
- Validate the selected model against identity:
  - European-only models on American contracts are allowed only when
    `pricing.exercise_approximation_policy` explicitly permits approximation;
  - American models cannot reuse European PCP equality;
  - model family mismatches fail before IV solving or Greek computation.
- Stamp diagnostics:

```text
pricing_model_contract_match
pricing_model_contract_reason
selected_model_exercise_style
contract_exercise_style
option_underlying_type
```

Exit criteria:

- Equity option data cannot be accidentally treated as futures options just
  because it has `C/P + strike`.
- WTI settlement rows cannot be exported under a CME-style root unless the
  product identity crosswalk explicitly provides that root.
- American contracts priced with a European approximation are visibly labelled
  as approximations in summaries and exports.

## Phase 3 - Bachelier / Normal Model

- Implement European Bachelier price for calls and puts.
- Implement Bachelier IV solver.
- Implement Greeks, at least scalar and numpy batch.
- Add model metadata:
  `price_dynamics=normal`, `supports_negative_underlying=true`.
- Add tests for negative, zero, and positive underlyings.
- Add comparison artifact for WTI stress rows:
  `black76` unsupported vs `bachelier` priced.

Exit criteria:

- `pricing.model: bachelier` works in core pricing, IV solving, Greek-only CLI,
  adapter prep, and export metadata.
- Negative-underlying rows can produce model-supported prices/Greeks under
  Bachelier.

## Phase 4 - Shifted Black-76

- Implement shifted Black-76 European price:
  `F_shifted = F + alpha`, `K_shifted = K + alpha`.
- Require explicit `pricing.shifted_black.shift` or a documented shift resolver.
- Stamp `pricing_shift`, `shifted_underlying`, and `shifted_strike` in
  diagnostics where relevant.
- Implement shifted IV solver.
- Do not treat shifted vol as interchangeable with plain Black vol.

Exit criteria:

- `pricing.model: black76_shifted` fails if shift is missing.
- Shifted model supports rows only where shifted domain is positive.
- Outputs make the shift visible.

## Phase 5 - American Approximation Engines

- Implement `black76_baw` for American futures options.
- First Greek policy: numerical bump Greeks from the selected price engine,
  stamped as `greek_method=numerical_bump`.
- Add optional `bsm_baw` only after futures BAW is stable.
- Add an explicit compatibility policy for American equity options: until
  `bsm_baw` or a reference tree engine is implemented, plain `bsm` may be used
  only as a declared European approximation and must be stamped as such.
- Evaluate alternative approximations:
  - `bjerksund_stensland`
  - `ju_zhong`
- American models use `american_bounds` or disabled parity checks, not European
  PCP equality.

Exit criteria:

- `black76_baw` IV round-trips its own prices.
- BAW price is checked against reference examples and tree engines.
- Readiness and dashboard output say the IV self-test used American
  approximation assumptions.

## Phase 6 - Tree Reference Engines

- Implement `crr_binomial` as a slow reference engine.
- Add `trinomial` if CRR stability is insufficient near boundary cases.
- Support American and European exercise modes.
- Use high step counts in tests/reference artifacts, not default production.
- Execute reference pricing through a cache-first parallel runner:
  - look up cached row results by deterministic input hash;
  - compute only cache misses;
  - split misses into chunks;
  - run chunks with bounded process workers;
  - preserve deterministic output ordering after merge.
- Keep the canonical production pricing path independent from reference jobs.
  Reference jobs may run synchronously for audits or asynchronously for
  diagnostics.

Exit criteria:

- Tree engine can validate BAW on representative WTI cases.
- Approximation error is reported by moneyness, tenor, right, and volatility.
- Cached reference rows are reused across runs when model, inputs, solver
  settings, and code version match.
- A reference timeout does not silently change canonical pricing outputs.

## Phase 6B - Reference Cache and Parallel Runner

Create a reusable reference execution layer:

```text
ReferenceRequest
  reference_model
  rows
  solver_settings
  cache_policy
  max_workers
  timeout_seconds

ReferenceResult
  row_key
  reference_price
  reference_greeks
  cache_hit
  runtime_ms
  solver_status
```

Cache key must include:

```text
reference_model
F_or_S
K
T
r
sigma
right
exercise_style
shift
tree_steps
tolerance
solver_version
code_version
```

Execution modes:

| Mode | Behavior | Use |
|---|---|---|
| `off` | Do not run reference pricing. | Normal production runs. |
| `cache_only` | Load cached references; do not compute misses. | Fast CI/report refresh. |
| `parallel` | Compute cache misses with bounded workers. | Validation runs. |
| `async` | Start reference job and mark comparison pending. | Dashboard/audit workflows. |

Parallelism rules:

- Reference pricing is embarrassingly parallel by row or chunk.
- Use process workers for Python-heavy tree/PDE code.
- Cap workers and batch size to avoid starving the main pipeline.
- Record worker count, cache-hit rate, timeout, and failures in summary.
- Never let a failed diagnostic reference job mutate canonical IV/Greeks/export.

Exit criteria:

- Reference validation can price a sampled WTI grid in parallel.
- Re-running the same validation is mostly cache hits.
- Parallel and single-worker reference results are bitwise or tolerance
  equivalent.

## Phase 7 - Finite Difference Reference

- Add `finite_difference` only if tree/reference coverage is not enough.
- Use it for targeted validation around early-exercise boundaries and stress
  regimes.
- Keep it out of large-chain default runs.

Exit criteria:

- PDE reference produces stable prices for a small curated fixture.
- BAW/tree disagreements can be triaged with a higher-trust method.

## Phase 8 - Model Comparison Artifact

Add optional comparison output when `pricing.compare_models` is set:

```text
model_comparison.parquet
model_comparison_summary.json
```

Per-row fields:

```text
canonical_model
comparison_model
price_model
iv_solved_model
delta_model
pricing_domain_valid
pricing_domain_reason
abs_price_diff_vs_canonical
abs_iv_diff_vs_provider
```

Summary fields:

```text
rows_supported
rows_unsupported
median_abs_iv_diff
p95_abs_iv_diff
boundary_failures
runtime_seconds
```

Exit criteria:

- Users can compare `black76`, `black76_baw`, `bachelier`, and
  `black76_shifted` without changing canonical outputs.

## Acceptance Criteria

- [ ] A central pricing model registry exists and owns model names, aliases, and
      metadata.
- [ ] Unknown model selection fails closed with a clear supported-model list.
- [ ] Existing `black76`, `bs`, and `bsm` behavior remains unchanged.
- [ ] Lognormal invalid domains no longer emit NumPy runtime warnings.
- [ ] Negative WTI-style rows are preserved and marked unsupported under
      lognormal models.
- [ ] At least one negative-underlying-capable model is available.
- [ ] At least one American approximation model is available.
- [ ] At least one tree/reference model validates American approximations.
- [ ] `pricing.compare_models` produces diagnostic artifacts without replacing
      canonical outputs.
- [ ] Export manifests record model family, dynamics, exercise style, and any
      shift/approximation parameters.

## Suggested Delivery Order

1. Registry only, no math changes.
2. Domain guards.
3. Bachelier.
4. Shifted Black-76.
5. Black76 BAW.
6. CRR/trinomial reference.
7. Comparison artifacts.
8. Optional finite difference.

This order gives the fastest risk reduction first: the system stops hiding
invalid lognormal domains, then gains a real negative-price-capable model,
then adds American exercise support.
