# Negative Futures Price Pricing-Domain Guards

Urgency: `P1-high`

Status: `in_progress`

Source signal:

- COVID-era WTI proved that exchange-valid futures settlement prices can be
  non-positive. A lognormal futures-option model such as Black-76 cannot price
  `F <= 0` because it computes `log(F / K)`.

## Summary

The pipeline currently has some row-level protections, but the core scalar
pricing functions are not fail-closed for non-positive underlyings. If a WTI
negative-price row reaches `core.pricing.price("black76", ...)`,
`core.pricing.solve_iv(...)`, or `core.greeks.single_leg_greeks(...)`, NumPy
emits an invalid-log warning and returns `NaN` implicitly. That is better than
an explosion, but it is not an auditable protection.

Fix by separating:

- raw data validity: negative futures prices can be real and must be preserved;
- pricing-domain validity: lognormal models require `F > 0`, `K > 0`,
  `sigma > 0`, `T > 0`, and valid right;
- model support: non-positive futures regimes require a different user-selected
  pricing model such as Bachelier/normal or shifted Black, not plain Black-76.

## Implementation Status

Updated: `2026-07-07`

Implemented:

- `core/pricing_models.py` now carries a shared scalar domain validator.
- Lognormal scalar pricing/IV/Greek paths fail closed for `S_or_F <= 0`,
  `K <= 0`, `sigma <= 0`, missing `r`, invalid `right`, and bad `T`.
- `core.pricing.price()`, `core.pricing.solve_iv()`,
  `core.greeks.single_leg_greeks()`, and `core.greeks.bump_greeks()` return
  `NaN`/clear runtime errors instead of emitting NumPy invalid-log warnings.
- `validate_provided_iv()` no longer invents `r=0.05`; rows without stamped
  rate are not invertible.
- `resolve_greek_inputs()` distinguishes `missing_underlying` from
  `nonpositive_underlying`.
- `black76_baw` is documented in the registry as still lognormal and therefore
  not valid for `F <= 0` unless a shifted implementation is explicitly added.

Still open:

- Prepared option artifacts do not yet stamp row-level
  `pricing_domain_valid`, `pricing_domain_reason`, or
  `pricing_model_supported`.
- Raw futures validation still needs an instrument-aware policy for preserving
  real negative settlements while keeping option premiums strictly positive.
- No Bachelier/normal or shifted-Black runtime engine is implemented yet.

## Why It Matters

- Treating a real negative futures settlement as bad data would erase exactly
  the stress regime the system must understand.
- Letting `np.log(F / K)` produce warnings and implicit `NaN` hides the reason
  behind failed IV/Greeks.
- Barone-Adesi-Whaley does not solve this by itself. BAW adds an
  early-exercise premium to a lognormal European base; without a shift it still
  needs a positive underlying domain.
- Model residuals during stress regimes should be explainable as
  `unsupported_model_domain`, not mixed into provider/model IV disagreement.

## Current State

Verified behavior:

| Path | Current behavior for `F < 0` |
|---|---|
| Raw contract validation | `price <= 0` is quarantined generically. This may be too strict for futures during negative-price regimes. |
| `core.greek_inputs.resolve_greek_inputs()` | Treats non-positive underlying as invalid Greek input; reason is currently `missing_underlying`. |
| `core.greeks.batch_greeks()` | Masks `S_or_F <= 0`, `K <= 0`, `sigma <= 0`; returns `NaN` without log warnings. |
| `core.pricing.price()` | Calls `np.log(F / K)` directly and returns implicit `NaN` with a runtime warning. |
| `core.pricing.solve_iv()` | Calls `price()` during bracketing; returns `NaN` but can emit repeated invalid-log warnings. |
| `core.greeks.single_leg_greeks()` | Calls `np.log(F / K)` directly and returns `NaN` Greeks with a runtime warning. |
| `core.greeks.bump_greeks()` | No upfront domain guard; can divide by a bad underlying bump or propagate invalid prices. |
| `core.option_chain_export` | Clean export mask requires underlying `> 0`, so negative-underlying rows are not exported. |

## Target Policy

Do not mutate or discard raw negative futures rows by default. Instead, attach
pricing-domain status:

```text
pricing_domain_valid = false
pricing_domain_reason = "lognormal_underlying_nonpositive"
pricing_model_supported = false
```

For lognormal engines:

```text
black76
black76_european
black76_baw
bsm
```

required domain:

```text
S_or_F > 0
K > 0
T > 0
sigma > 0
r finite
right in {"C", "P"}
```

For future engines that support negative underlyings, add a separate model:

```text
bachelier
black76_shifted
```

Those models must have explicit units and metadata. A shifted model must stamp
the shift:

```text
pricing_shift = alpha
shifted_underlying = F + alpha
shifted_strike = K + alpha
```

IVs from shifted/lognormal/normal models are not interchangeable; summaries and
exports must label the model family.

## Refactor Plan

**Phase 1 — Lognormal domain guard**

- Add a shared pricing-domain validator in `core/pricing.py` or
  `core/pricing_models.py`.
- `price()` returns `NaN` or raises a clear `ValueError` according to an
  explicit policy; it must not emit NumPy invalid-log warnings.
- `solve_iv()` returns `NaN` immediately for invalid model domain and records
  the reason where the caller supports diagnostics.
- `single_leg_greeks()` returns all-`NaN` Greeks for invalid model domain,
  matching `batch_greeks()`.
- `bump_greeks()` refuses invalid domains before bumping.

**Phase 2 — Row diagnostics**

- Rename or split `missing_underlying` into:
  `missing_underlying` and `nonpositive_underlying`.
- Add `pricing_domain_valid` and `pricing_domain_reason` columns during option
  preparation and Greek-only input resolution.
- `validate_provided_iv()` should mark invalid-domain rows as not invertible
  without trying to solve IV.
- Near-money provider/model IV aggregates must exclude invalid-domain rows and
  report the excluded count.

**Phase 3 — Raw data policy**

- Replace generic `price > 0` for futures with instrument-aware bounds:
  option premium must be positive, but futures settlement may be non-positive
  if the product config allows it.
- Preserve raw negative futures rows with a stress-regime flag instead of
  quarantining them as vendor errors.
- Add config:

```yaml
pricing:
  lognormal_domain_policy: fail_closed

market_domain:
  allow_negative_underlying: true
  negative_underlying_policy: preserve_and_mark_unsupported
```

**Phase 4 — Negative-domain model support**

- Add `bachelier` or `black76_shifted` as explicit pricing engines only after
  validation fixtures exist.
- For WTI stress windows, compare:
  `black76` fail-closed coverage, shifted Black residuals, and Bachelier
  residuals.

## Acceptance Criteria

- [x] `price("black76", F <= 0, ...)` does not emit runtime warnings; it
      returns `NaN` or raises a clear domain error by policy.
- [x] `solve_iv("black76", ..., F <= 0, ...)` returns `NaN` immediately and
      does not call the root solver.
- [x] `single_leg_greeks("black76", F <= 0, ...)` returns all `NaN` without
      warnings, matching `batch_greeks()`.
- [x] Greek input diagnostics distinguish missing underlying from non-positive
      underlying.
- [ ] `validate_provided_iv()` excludes invalid lognormal-domain rows from
      near-money IV mismatch calculations and reports the count.
- [ ] Futures raw contract validation can preserve real negative settlements
      when the instrument config allows them; option premiums remain strictly
      positive.
- [x] `black76_baw` is documented as still invalid for `F <= 0` unless a shifted
      implementation is explicitly selected.
- [ ] A fixture with `F = -37.63`, positive option premium, and positive strike
      proves the pipeline marks the row as unsupported for lognormal pricing
      instead of crashing or silently producing misleading Greeks.

## Evidence Required

- Unit tests for pricing/IV/Greek scalar functions with `F <= 0`, `K <= 0`,
  `sigma <= 0`, and invalid right.
- End-to-end option-prep fixture showing preserved negative futures row plus
  blocked lognormal pricing domain.
- Summary artifact with invalid-domain counts.
- Export test proving invalid-domain rows do not enter clean downstream Greeks.
