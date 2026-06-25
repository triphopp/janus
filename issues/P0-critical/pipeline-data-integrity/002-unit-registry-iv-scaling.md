# Unit Registry and IV Scaling Guards

Urgency: `P0-critical`

Status: `draft`

Source plan:

- `docs/design/data_test_measurement_criteria.md`

## Summary

Introduce an explicit unit registry for critical numeric fields, starting with
option IV. No loader should divide or multiply IV without preserving the raw
value and declaring the unit assumption.

## Why It Matters

Silent IV scaling errors can create 100x or 0.01x mistakes. The current trust
issue cannot be resolved until provider IV units are explicit and testable.

## Scope

In scope:

- Preserve raw IV and raw IV unit.
- Write canonical decimal IV separately.
- Store scale factor in manifest or equivalent metadata.
- Add smoke tests for percent-as-decimal and decimal-divided-twice failures.
- Apply the same pattern to rates where practical.

Out of scope:

- Building a full provider reconciliation system.
- Deciding the final IV eligibility universe.

## Public-Safe Notes

- Use synthetic examples such as `58.26110 percent -> 0.582611 decimal`.
- Do not include full vendor rows.

## Acceptance Criteria

- [ ] Unknown IV unit blocks official runs.
- [ ] Percent IV treated as decimal blocks.
- [ ] Decimal IV divided by 100 blocks.
- [ ] Raw IV, raw unit, canonical IV, and scale factor are recorded.
- [ ] Equity-option loader has an explicit IV unit declaration before its data is trusted.

## Evidence Required

- `unit_assumptions.json`
- unit registry tests
- failing examples for 100x and 0.01x cases

## Related Checks

- Gate: `G4 Unit Assumptions`
- Metric: `iv_raw_unit_known_rate`
- Metric: `iv_scale_smoke_status`

