# Multi-Model Pricing Engine Rollout Plan

Urgency: `P1-high`

Status: `draft`

Source issues:

- `031-american-futures-option-pricing-engine-selection.md`
- `032-negative-futures-price-pricing-domain-guards.md`

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

## Design Principles

- User-selected model, not implicit model switching.
- One canonical `pricing.model` per run.
- Optional `pricing.compare_models` for diagnostics.
- Every output records model identity and assumptions.
- Invalid model domain is a data-quality state, not a crash.
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

- Create `core/pricing_models.py`.
- Move supported model names, aliases, metadata, and allowed config values into
  the registry.
- Update `core/pricing.py`, `core/greeks.py`, `run_greeks.py`,
  `adapters/options_base.py`, and `core/option_chain_export.py` to resolve
  models through the registry.
- Preserve `black76`, `bs`, and `bsm` outputs exactly.
- Make unknown model errors list supported model names.

Exit criteria:

- `pricing.model` uses registry validation everywhere.
- CLI choices are generated from or checked against the registry.
- Existing golden tests pass unchanged.

## Phase 2 - Domain Guards

- Add a shared domain validator for each dynamics family:
  - lognormal: `S_or_F > 0`, `K > 0`, `T > 0`, `sigma > 0`
  - normal: finite `S_or_F`, `K`, `T > 0`, `sigma > 0`
  - shifted lognormal: `S_or_F + shift > 0`, `K + shift > 0`
- Make `price()`, `solve_iv()`, `single_leg_greeks()`, and `bump_greeks()`
  fail closed without NumPy runtime warnings.
- Add row-level diagnostics:
  `pricing_domain_valid`, `pricing_domain_reason`,
  `pricing_model_supported`.
- Split `missing_underlying` from `nonpositive_underlying`.

Exit criteria:

- Invalid lognormal domains return `NaN` or clear domain errors by policy.
- Negative WTI rows are preserved but marked unsupported for `black76`.

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
