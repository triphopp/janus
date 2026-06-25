# Equity Options Trust Audit

Urgency: `P1-high`

Status: `draft`

Source plan:

- `docs/design/data_test_measurement_criteria.md`

## Summary

Audit equity-option data before old or new results are treated as trusted. The
WTI incident may not be isolated to futures options.

## Why It Matters

Equity options can have the same IV unit, underlying-price, dividend, rate, and
snapshot-history risks as futures options, plus equity-specific adjustment
risks.

## Scope

In scope:

- Confirm provider IV unit with fixture.
- Record underlying price source.
- Record dividend and rate assumptions.
- Run IV and call/put checks on an explicit eligible universe.
- Label snapshot-only option data as not historical backtest-grade.

Out of scope:

- Vendor replacement decision.
- Full equity strategy implementation.

## Acceptance Criteria

- [ ] Equity-option IV unit is declared and tested.
- [ ] Underlying source is explicit.
- [ ] Dividend and rate assumptions are recorded.
- [ ] PCP/IV checks are not `not_checked` for trusted runs.
- [ ] Snapshot-only data is not presented as historical backtest data.

## Evidence Required

- equity-option fixture
- unit assumptions artifact
- market checks summary

## Related Checks

- Gate: `G4 Unit Assumptions`
- Gate: `G5 Domain Market Checks`
- Gate: `G6 PIT + Reproducibility`

