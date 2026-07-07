# American Futures Option Pricing Engine Selection

Urgency: `P1-high`

Status: `draft`

Source signal:

- WTI settlement `OPTION_VOLATILITY` appears more consistent with an
  American futures-option model such as Black-76 plus Barone-Adesi-Whaley
  early-exercise approximation than with plain European Black-76.

## Summary

The current futures-option stack treats WTI options as European Black-76:
`pricing.model: black76`. That is too narrow if the exchange-provided WTI IV
was produced from an American-style futures-option model. We need a pricing
engine layer that keeps the shared Black-76 base but lets the user explicitly
choose the exercise/approximation model, for example:

- `black76` / `black76_european` — current European futures-option model.
- `black76_baw` — American futures-option approximation using
  Barone-Adesi-Whaley: European base value plus an early-exercise premium.
- `bsm` — existing equity/index model.

The important design rule: `black76_baw` must not silently replace `black76`.
Model choice is a run input and must be visible in config, outputs, summaries,
and exported option-chain metadata.

## Why It Matters

- If WTI exchange IV was generated under an American approximation and we invert
  the same premiums with European Black-76, near-money IV self-tests can report
  false provider/model disagreement.
- Greeks can be biased around exercise boundaries because the missing
  early-exercise premium is not constant across strike, expiry, volatility, or
  rates.
- European put-call parity equality is not a valid hard gate for American
  options. Reusing the current equality check under `black76_baw` would create
  false quality flags.
- The issue is a model-contract problem, not only a formula problem: users need
  to know which pricing engine was used for each run.

## Current State

Verified code paths:

| Location | Current behavior |
|---|---|
| `core/pricing.py` | `price()` supports `black76`, `bs`, `bsm`; no American futures-option approximation. |
| `core/pricing.py` | `solve_iv()` inverts with the selected model string, so adding `black76_baw` here automatically affects IV solving/self-tests. |
| `core/greeks.py` | Closed-form Greeks exist for European Black-76 and BSM only; vectorized backends branch directly on model string. |
| `adapters/options_base.py` | `build_iv_surface()`, `compute_greeks()`, and `check_pcp()` all consume `pricing_model`. |
| `run_greeks.py` | CLI `--model` choices are hardcoded to `black76`, `bs`, `bsm`. |
| `core/option_chain_export.py` | Data dictionary allowed values are hardcoded to `black76`, `bsm`; descriptions say Black-76. |

The existing selector is good enough for a first rollout, but the direct
`if model == ...` branches should move toward a small model registry so adding
one engine does not scatter new string cases everywhere.

## Quant Approach

Barone-Adesi-Whaley is the right shape for this request because it preserves
the existing Black-76/Black-Scholes family and adds an early-exercise premium:

```text
American value = European base value + early exercise premium
```

For futures options, use the general cost-of-carry formulation with:

```text
b = 0
S = F
European base = Black-76
```

This is not a pure closed-form formula in the same sense as European Black-76.
The price expression is analytic once the critical exercise boundary is known,
but that critical boundary is found by iteration. The implementation should
therefore be described as an analytic approximation with a numerical boundary
solve.

High-level call form:

```text
if F >= F_star:
    C_american = F - K
else:
    C_american = C_black76 + A2 * (F / F_star) ** q2
```

High-level put form:

```text
if F <= F_star:
    P_american = K - F
else:
    P_american = P_black76 + A1 * (F / F_star) ** q1
```

`F_star`, `q1`, `q2`, `A1`, and `A2` follow the BAW quadratic approximation
with cost of carry `b=0`. Boundary solving must be robust, bounded, and
well-tested; a failed boundary solve should return `NaN` for IV solving or
fall back only under an explicit config policy, never silently.

## Target Architecture

Introduce a pricing-engine contract:

```text
PricingEngine
  name
  family: futures_options | equity_options
  exercise_style: european | american
  price_dynamics: lognormal | normal | shifted_lognormal | tree | finite_difference
  approximation: none | barone_adesi_whaley | crr_tree | trinomial_tree | pde
  supports_negative_underlying
  price(...)
  supports_closed_form_greeks
  parity_check_mode: equality | american_bounds | disabled
```

Initial engine names should be broad enough that the user can select one
pricing model per run without code edits:

| Engine | Family | Exercise | Negative `F/S`? | Use |
|---|---|---|---|---|
| `black76` | futures options | European | No | Backward-compatible alias for current behavior. |
| `black76_european` | futures options | European | No | Explicit European futures-option pricing. |
| `black76_baw` | futures options | American approximation | No | WTI/NYMEX self-test candidate after validation. |
| `bachelier` / `normal` | futures/equity options | European | Yes | Stress regimes where underlying can be near zero or negative. |
| `black76_shifted` | futures options | European shifted lognormal | Yes, if shifted domain is positive | Negative/near-zero futures with explicit displacement. |
| `black76_shifted_baw` | futures options | American shifted approximation | Yes, if shifted domain is positive | Later candidate for American futures in negative-price regimes. |
| `bsm` | equity/index options | European | No | Existing Black-Scholes-Merton model. |
| `bsm_baw` | equity/index options | American approximation | No | American equity-style options where early exercise matters. |
| `crr_binomial` | generic options | American/European | Depends on implementation | Slow reference engine and validation baseline. |
| `trinomial` | generic options | American/European | Depends on implementation | More stable tree reference for American options. |
| `finite_difference` | generic options | American/European | Depends on PDE setup | Slow, high-trust reference for boundary-sensitive cases. |

Non-goal for this issue: local-vol, stochastic-vol, or full surface dynamics
such as SABR/Heston. Those are calibration/surface models, not simple drop-in
per-row pricing engines for the current pipeline. They can become later
extensions once the engine registry and validation contracts are in place.

Selection policy:

- A run has exactly one active `pricing.model` for canonical IV/Greeks/export.
- Optional comparison mode may run additional models for diagnostics only:
  `pricing.compare_models: [black76, black76_baw, bachelier]`.
- No automatic model switching unless the config explicitly asks for it. If a
  row is outside the selected model domain, mark it unsupported instead of
  silently falling back to another model.
- Every output must record `pricing_model`, `price_dynamics`,
  `exercise_style`, and any model-specific parameter such as
  `pricing_shift`.

Config example:

```yaml
pricing:
  model: black76_baw
  compare_models: [black76, bachelier]
  exercise_style: american
  approximation: barone_adesi_whaley
  baw_boundary_solver: brent
  baw_boundary_tol: 1.0e-8
```

CLI example:

```bash
python run_greeks.py --input wti_options.csv --model black76_baw --output outputs/greeks/wti.parquet
```

## Refactor Plan

**Phase 1 — Pricing engine selection**

- Add `black76_european` as an alias for existing `black76`.
- Add `black76_baw` to the model allow-list in config/CLI/output metadata.
- Add registry metadata for model family, exercise style, dynamics,
  negative-underlying support, parity-check mode, and Greek method.
- Allow exactly one active `pricing.model`; add optional `compare_models` for
  diagnostics without changing canonical outputs.
- Centralize model metadata in `core/pricing.py` or a new
  `core/pricing_models.py`.
- Preserve `black76` behavior exactly for backward compatibility.

**Phase 1B — Negative-domain-capable European engines**

- Add `bachelier`/`normal` pricing and IV solving for European options as the
  first engine that supports non-positive underlyings.
- Add `black76_shifted` only with explicit `pricing_shift`; stamp the shift in
  summaries and exports because shifted vol is not plain Black vol.
- Keep `black76_shifted_baw` deferred until plain `black76_baw` and shifted
  European pricing are both validated.

**Phase 2 — BAW price and IV solver**

- Implement BAW price for American futures calls and puts using Black-76 as the
  European base and `b=0`.
- Reuse `solve_iv()` with `model="black76_baw"` so IV inversion and provider
  self-tests can compare European vs American assumptions.
- Add boundary-solver diagnostics:
  `baw_boundary_converged`, `baw_boundary_iterations`,
  `baw_boundary_solver_status`.
- Add tests against published examples or an independent reference engine
  across calls/puts, ITM/ATM/OTM, short/long DTE, and low/high rates.

**Phase 3 — Greeks policy**

- Do not pretend the existing closed-form Black-76 Greeks are BAW Greeks.
- First safe rollout: compute `black76_baw` Greeks with finite-difference bump
  off the BAW price and stamp `greek_method: numerical_baw_bump`.
- Later optimization: derive/test analytic Greeks for the BAW premium component
  or vectorize/cache boundary solves after price accuracy is proven.
- Keep CUDA/vectorized fast paths for `black76`/`bsm`; route `black76_baw`
  through loop or cached vectorized CPU until validated.

**Phase 4 — Market checks**

- For `black76`/`black76_european`, keep European futures put-call parity:
  `C - P = exp(-rT) * (F - K)`.
- For `black76_baw`, replace equality with American no-arbitrage bounds or
  mark PCP equality as `disabled_due_to_american_exercise`.
- Readiness and dashboard copy must say whether IV mismatch is being tested
  under European or American assumptions.

**Phase 5 — WTI validation**

- Run the same WTI sample under both `black76` and `black76_baw`.
- Compare near-money IV mismatch rate, median absolute IV diff, and p95 IV diff.
- Promote `black76_baw` for WTI only if it materially reduces model-vs-provider
  residuals without creating worse premium/parity/generic quality failures.
- Record the decision in the run summary and instrument config.

**Phase 6 — Reference engines**

- Add `crr_binomial` or `trinomial` as a slow reference engine for American
  options and BAW validation.
- Keep tree/PDE engines out of the default fast path unless the user explicitly
  selects them.
- Use reference engines in tests and model-comparison artifacts to catch BAW
  approximation failures around early-exercise boundaries.

## Acceptance Criteria

- [ ] `pricing.model` accepts the registered engine set; unknown models still
      fail closed with a clear error listing supported models.
- [ ] A run records exactly one active canonical model; optional
      `compare_models` outputs are marked diagnostic and never replace canonical
      IV/Greeks silently.
- [ ] `black76` output is unchanged versus current golden tests.
- [ ] `black76_baw` price is implemented as Black-76 European base plus BAW
      early-exercise premium for futures options (`b=0`).
- [ ] At least one negative-underlying-capable model (`bachelier`/`normal` or
      explicit `black76_shifted`) is registered separately from Black-76.
- [ ] BAW boundary solve is bounded, deterministic, tested, and emits
      convergence diagnostics.
- [ ] `solve_iv("black76_baw", ...)` round-trips prices generated by
      `price("black76_baw", ...)`.
- [ ] CLI `--model black76_baw` works and summary/output metadata records the
      selected model.
- [ ] Export schema/data dictionary allow and describe `black76_baw`.
- [ ] American models do not use European PCP equality as a hard quality gate.
- [ ] BAW Greeks are either correctly derived and tested, or explicitly stamped
      as numerical bump Greeks; no output labels them as plain Black-76 Greeks.
- [ ] A WTI comparison artifact shows European Black-76 vs BAW residuals before
      changing the WTI default.

## Evidence Required

- Unit tests for BAW call/put prices and boundary behavior.
- IV round-trip tests for `black76_baw`.
- Regression proving `black76` golden fixtures remain unchanged.
- CLI test for `run_greeks.py --model black76_baw`.
- Option-quality test proving American model selection disables/replaces
  European PCP equality.
- WTI model-comparison summary: `black76` vs `black76_baw`.

## References

- Barone-Adesi, G. and Whaley, R. E. (1987), "Efficient Analytic Approximation
  of American Option Values."
- Black-76 remains the European base for futures options; BAW supplies the
  early-exercise premium and boundary approximation.
