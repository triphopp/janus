# IV Validation: Trust Exchange Settlement IV As Primary

Urgency: `P0-critical`

Status: `implemented` (2026-06-28)

Implementation evidence (WTI smoke, window `2024-09-25 .. 2024-10-04`):

```text
before: option exclusion 2,012 / 9,616 (20.9%); run readiness BLOCKED (export withheld)
after : option exclusion 0 / 9,616 (0.0%);     run readiness NEEDS_REVIEW (export proceeds)
        near_money_iv: invertible_rows=4,592  mismatch_rate=0.100 (thr 0.05)
                       median_abs_diff=0.0026  p95_abs_diff=0.084
        deep-ITM record (strike 36 call) recovered, exported with exchange IV 0.559373
```

Source plan:

- `issues/completed/P0-critical/pipeline-data-integrity/002-unit-registry-iv-scaling.md`
- `issues/completed/P0-critical/pipeline-data-integrity/003-option-market-checks-run-status.md`
- `issues/completed/P0-critical/pipeline-data-integrity/000-implementation-sequence.md`
- Smoke evidence: WTI settlement run, window `2024-09-25 .. 2024-10-04`
- CME settlement methodology (exchange-published settlement volatility surface)

## Summary

Make the exchange-published settlement implied volatility (`OPTION_VOLATILITY`)
the authoritative IV for futures options, and demote our price-inverted IV from a
per-row exclusion gate to a near-the-money, run-level sanity diagnostic.

Today the pipeline already *uses* exchange IV for Greeks (`iv_source: provided`),
but it also re-derives IV by inverting the printed settlement price and then flags
and excludes every row where the two disagree. For deep in/out-of-the-money
contracts the printed price is essentially all intrinsic value (time value smaller
than one tick), so inverting it cannot recover a meaningful IV. The result is that
good exchange data is dropped because it disagrees with an unreliable inversion.

## Why It Matters

On the observed WTI sample this single mechanism removes about one fifth of the
option universe and blocks the downstream export, even though the exchange data is
sound. It both understates usable data and creates false "blocked" run readiness,
undermining trust in the very gate that issue 003 introduced.

It is also internally contradictory: the run keeps exchange IV for pricing while
simultaneously treating that same exchange IV as untrustworthy for the row's
release eligibility.

## Current Failure Mode

`core/pricing.validate_provided_iv` sets, per row:

```text
iv_solved = solve_iv(settlement_price, F, K, T, r, right)
iv_diff   = |iv_solved - iv_provided|
iv_flag   = iv_diff > iv_validate_threshold      # default 0.005 (0.5 vol points)
```

`core/option_chain_export._clean_release_mask` then excludes any `iv_flag` row, and
`core/run_readiness` escalates the run on the `iv_flag` rate.

Observed WTI smoke evidence (validated sample, `iv_validate_threshold = 0.005`):

```text
option rows:                    9,616
iv-flagged (excluded):          2,012   (20.9% of all; ~40% of the validated 5,000 sample)
  - deep ITM/OTM unstable inversion:   1,101  (55%)
  - near-money genuine difference:       651  (32%)
  - deep ITM/OTM, zero time value:       260  (13%)
genuinely degenerate (price <= intrinsic): ~4%
```

Example (deep ITM call; price equals intrinsic exactly):

```text
right=C strike=36 underlying=69.69 settlement=33.69 time_value=0.00
iv_provided=0.5594 (exchange surface)  iv_solved=1.2826 (price inversion)  iv_diff=0.7233
```

The exchange IV (0.56) is sensible; the inverted IV (1.28) is an artifact of
inverting a penny-rounded intrinsic price. The flag penalizes the exchange value.

The threshold is also an absolute band: 0.5 vol points is applied identically to a
15% IV and an 80% IV, so flag rate is highly threshold-sensitive (40% at 0.005 vs
15% at 0.05 on the validated sample).

## Final Decision (option #1 — do not invert by default)

Smoke analysis showed the near-money aggregate only reproduces the exchange IV
(median diff 0.26 vol pts) and its residual tail is concentrated entirely in the
wings (>20% from ATM: 26.5% mismatch) and long-dated rows — exactly where
price-inversion is ill-conditioned. Since inversion is unreliable wherever it
disagrees and merely echoes the exchange where it agrees, it adds no information.

Decision: **price-inversion is OFF by default** (`validate_provided_iv: false`).
The exchange settlement IV is used as-is. `validate_provided_iv` is retained as an
opt-in model self-test (near-money-only, aggregate) for debugging a suspected
systemic error (rate/forward/model/units); when off, the readiness IV check is
`ready` with basis `exchange_authoritative`, not `needs_review`. Unit-scaling
integrity remains owned by the unit registry (002); broader surface sanity is
deferred to arbitrage-free checks (future), not price-inversion.

## Scope

In scope:

- Treat `iv_source: provided` runs as exchange-IV-authoritative: the provider IV is
  never overridden or excluded solely because it disagrees with price inversion.
- Restrict price-inversion cross-checks to a near-the-money band where inversion is
  numerically reliable (settlement carries recoverable time value).
- Convert the provider/model IV comparison into a run-level aggregate sanity
  diagnostic (systemic mismatch detector) rather than a per-row exclusion.
- Keep deep ITM/OTM and zero-time-value rows in the export when the exchange IV and
  Greeks are present; mark them informationally, do not drop them.
- Preserve a genuine-corruption path: rows that are truly unusable (missing IV,
  non-positive IV, price below intrinsic beyond tolerance) remain excluded.
- Make the near-money band and the IV sanity thresholds config-driven per
  instrument (no instrument names in `core/`).

Out of scope:

- Full arbitrage-free surface validation (calendar/butterfly/smile monotonicity).
  Tracked separately; this issue only stops the false per-row exclusion and adds the
  aggregate sanity check.
- Re-fitting our own volatility surface.
- Changing `iv_source: solve` instruments (where we own the IV and there is no
  provider to compare).

## Resolved Design Decisions

### Exchange IV Is Authoritative Under `iv_source: provided`

When the run declares `iv_source: provided`, the canonical `iv` is the exchange IV
(already true). Price inversion may inform a diagnostic, never an override or a
row drop.

### Inversion Is Only Valid Near The Money

A price-inverted IV is only trustworthy where the settlement price carries time
value larger than the inversion can resolve. Define eligibility by time value in
ticks (preferred) or an absolute moneyness band:

```text
iv_invertible = time_value >= min_time_value_ticks * price_tick
                (optionally AND |1 - moneyness| <= max_abs_moneyness_gap)
```

Rows outside this band are `iv_check = not_applicable` (not `flag`).

### Provider/Model Comparison Becomes A Run-Level Sanity Diagnostic

Compute `iv_diff` only on invertible (near-money) rows and report an aggregate:

```text
near_money_iv_median_abs_diff
near_money_iv_p95_abs_diff
near_money_iv_mismatch_rate   (diff > threshold among invertible rows)
```

A large aggregate near-money mismatch signals a systemic bug (units, underlying
mapping, rate, or model) and escalates run readiness. A normal aggregate passes.
Individual near-money outliers are informational flags, not exclusions.

### Unit Errors Are Owned By The Unit Registry

The scaling-error case (percent-as-decimal, decimal-divided-twice) is already
covered by `core/unit_registry` (issue 002). The IV comparison must not duplicate
that responsibility; its residual purpose is underlying/rate/model sanity only.

### Genuine Corruption Still Excluded

Rows with missing or non-positive IV, or `settlement_price < intrinsic - tolerance`,
are genuinely unusable and remain excluded from the downstream export and counted
as real data loss.

## Public-Safe Notes

- Use synthetic chains that reproduce the deep-ITM zero-time-value structure.
- Do not include licensed raw vendor rows.

## Acceptance Criteria

- [ ] Under `iv_source: provided`, no option row is excluded from the downstream
      export solely because `iv_provided` disagrees with price inversion.
- [ ] Price-inversion IV checks run only on near-the-money / sufficient-time-value
      rows; deep ITM/OTM rows are `not_applicable`, not flagged.
- [ ] The provider/model comparison is reported as a run-level aggregate
      (`near_money_iv_*`) and only that aggregate can move run readiness.
- [ ] A synthetic deep-ITM, zero-time-value row keeps its exchange IV and Greeks and
      appears in the downstream export.
- [ ] A genuinely corrupt row (price below intrinsic beyond tolerance, or missing /
      non-positive IV) is still excluded.
- [ ] On the WTI smoke fixture, the IV-driven exclusion rate falls from ~20% to the
      genuine-corruption residual (~4% or configured), and the run is no longer
      blocked solely by deep-ITM inversion mismatch.
- [ ] Near-money band and IV sanity thresholds are config-driven; no instrument
      names appear in `core/` (Iron Rule).
- [ ] `iv_mismatch_review` artifact continues to explain any residual flags by reason.

## Agent Test Matrix

Issue coverage:

- `025`

Required tests:

- `test_provided_iv_run_does_not_exclude_on_inversion_disagreement`
- `test_deep_itm_zero_time_value_row_is_exported_with_exchange_iv`
- `test_inversion_check_skips_rows_below_min_time_value`
- `test_near_money_iv_aggregate_diagnostic_computed`
- `test_systemic_near_money_mismatch_sets_run_review_or_blocked`
- `test_isolated_near_money_outlier_is_informational_not_excluded`
- `test_genuinely_corrupt_row_below_intrinsic_still_excluded`
- `test_missing_or_nonpositive_iv_still_excluded`
- `test_iv_exclusion_rate_drops_on_wti_smoke_fixture`
- `test_near_money_band_and_thresholds_are_config_driven`

Required evidence:

- before/after IV exclusion-rate comparison on the WTI smoke fixture
- run summary `near_money_iv_*` aggregate block
- `iv_mismatch_review` reason breakdown after the change

## Evidence Required

- near-money aggregate IV diagnostic in `summary.json`
- export row-count delta (before vs after) on the public-safe fixture
- test proving exchange IV is retained for deep-ITM rows
- test proving genuine corruption is still excluded

## Related Checks

- Gate: `G4 Unit Assumptions`
- Gate: `G5 Domain Market Checks`
- Metric: `near_money_iv_mismatch_rate`
- Metric: `iv_provider_authoritative`
- Metric: `downstream_export_clean_row_rate`
- Expected status: downstream export is no longer blocked by deep-ITM price-inversion
  disagreement; only systemic near-money mismatch or genuine corruption blocks/excludes.

## Implementation Note: Timing

This is the policy half of the IV story and is intended to land when the backtest
dataset universe is defined (it decides which contracts are tradable/usable). Until
then, issue 003 visibility (`iv_mismatch_review` drill-down) explains the current
~20% so the blocked status is transparent rather than silent.
