# Second-Order Greek P&L Attribution

Urgency: `P2-medium`

Status: `draft`

Source plan:

- (this issue)

## Summary

Extend Greek P&L attribution from a first-order/`gamma`-only explain to a
standard second-order explain (`delta`, `gamma`, `vega`, `vanna`, `volga`,
`theta`, plus `rho`/carry) with a tracked residual. The residual, not a fixed
Greek list, decides when a higher-order term must be added.

## Why It Matters

For a vol-relative-value / VRP book, a delta-hedged position that looks flat
under a 5-Greek explain still shows unexplained P&L when IV moves: the
`dS·dsigma` (vanna) and `dsigma^2` (volga) terms are missing. Without them,
daily P&L cannot be honestly decomposed into spot vs vol vs time, and
calendar-spread risk (term-structure, near-expiry decay of `delta`/`vega`)
is invisible. An unattributed residual is a data-trust hole: we cannot claim
we understand where the P&L came from.

## Background: the expansion

```
dV ~=  delta*dS + 0.5*gamma*dS^2                 (spot)
     + vega*dsigma + 0.5*volga*dsigma^2 + vanna*dS*dsigma   (vol)
     + theta*dt                                  (time)
     + rho*dr                                    (carry)
     + residual
```

Closed-form additions (Black-76; `core/greeks.py` already computes
`d1`, `d2`, `phi(d1)`, `vega`, `disc`):

- `vanna = -disc * phi(d1) * d2 / sigma`
- `volga = vega * d1 * d2 / sigma`

Both are near-free given existing intermediates in `_batch_greeks_numpy`.

## Scope

In scope:

- Add `vanna` and `volga` to `core/greeks.py`:
  - `single_leg_greeks` (scalar reference)
  - `_batch_greeks_numpy` (vectorized, reuse existing `d1`/`d2`/`phi`/`vega`)
  - `_batch_greeks_loop` and `_batch_greeks_cuda` for backend parity
  - both `black76` and `bs`/`bsm` model branches
- New P&L attribution module (`core/pnl_attribution.py` or nearest existing
  home) that, given start/end market states, decomposes realized P&L into the
  6 terms + carry + residual per leg and netted per position.
- Residual tracking: emit `residual` and `residual_pct = |residual| / |dV|`
  as a first-class output column/field for every attribution row.
- Wire into the existing Greek P&L attribution path used by the backtest.

Out of scope:

- Third-order terms (`speed`, `ultima`, `zomma`, `color`) — add later only if
  the residual demonstrably correlates with the corresponding move term.
- `charm`/`veta` (time-cross Greeks) — separate follow-up issue scoped to
  calendar spreads / near-expiry (candidate: `030`).
- Any change to the pricing model or IV construction.

## Public-Safe Notes

- No local absolute paths in code, tests, or fixtures.
- Use the existing synthetic Greek grid / reference fixtures; no vendor rows.
- Second-order reference values must come from closed-form or bump, not
  licensed data.

## Acceptance Criteria

- [ ] `vanna` and `volga` returned by every backend (`numpy`, `loop`, `cuda`)
      and both models, agreeing across backends within existing tolerances.
- [ ] Closed-form `vanna`/`volga` match a numerical bump (finite-difference of
      `delta` w.r.t. `sigma`, and `vega` w.r.t. `sigma`) within bump tolerance.
- [ ] Invalid rows (`T<=0`, `sigma<=0`, non-finite, unknown `right`) return
      `NaN` for the new Greeks, consistent with the existing 5.
- [ ] P&L attribution reproduces total realized P&L: sum of terms + residual
      equals actual `dV` exactly (identity holds by construction).
- [ ] `residual_pct` is emitted per row and aggregated per run.
- [ ] On the backtest, median daily `residual_pct` is below an agreed
      threshold (target: explain > 95% of |dV|); large-move days flagged.

## Evidence Required

- Backend-parity + bump-match test output for `vanna`/`volga`.
- Attribution identity test (terms + residual == realized P&L).
- Backtest residual distribution summary (median / p95 `residual_pct`,
  count of days above threshold).

## Related Checks

- Gate: Greek P&L attribution / strategy-metrics explain gate
- Metric: `residual_pct` (median, p95)
- Expected status: explain coverage > 95% of |dV| on normal-move days
