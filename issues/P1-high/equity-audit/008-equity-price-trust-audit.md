# Equity Price Trust Audit

Urgency: `P1-high`

Status: `draft`

Source plan:

- `docs/design/data_test_measurement_criteria.md`

## Summary

Audit stock/equity price data for adjusted-close policy, split timing, dividend
timing, and survivorship visibility.

## Why It Matters

Even without IV, equity price history can be wrong for backtests if adjusted
prices or future split information leak into earlier decision dates.

## Scope

In scope:

- Record adjusted vs unadjusted price policy.
- Check split timing.
- Check dividend timing.
- Require delisting/survivorship fields or mark them `not_checked`.

Out of scope:

- Building complete corporate actions database.

## Acceptance Criteria

- [ ] Adjusted/unadjusted close policy is visible.
- [ ] Future split leakage risk is tested or marked `not_checked`.
- [ ] Dividend timing is point-in-time.
- [ ] Delisting coverage is present or visibly `not_checked`.
- [ ] Dashboard does not hide adjustment warnings.

## Evidence Required

- equity fixture
- price adjustment summary
- dashboard status snapshot

## Related Checks

- Gate: `G5 Domain Market Checks`
- Gate: `G6 PIT + Reproducibility`
- Gate: `G7 Dashboard Status`

