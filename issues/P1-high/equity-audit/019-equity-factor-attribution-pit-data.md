# Equity Factor Attribution Needs PIT Factor Data

Urgency: `P1-high`

Status: `draft`

Source plan:

- `docs/reports/implementation_status_v1_4.md`

## Summary

Require point-in-time factor returns and factor loadings before equity factor
attribution is treated as trusted.

## Why It Matters

Factor attribution can leak future information or explain returns with data that
was not known at the time.

## Scope

In scope:

- Define factor data contract.
- Require `available_at` for factor returns and loadings.
- Use PIT joins for attribution inputs.
- Mark attribution `not_checked` when PIT factor data is absent.

Out of scope:

- Selecting a final commercial factor model.

## Acceptance Criteria

- [ ] Factor data has source hash, as-of date, and available-at time.
- [ ] Factor joins assert `available_at <= decision_time`.
- [ ] Attribution is `not_checked` when PIT factor data is absent.
- [ ] Dashboard does not show trusted factor attribution without PIT inputs.

## Evidence Required

- factor fixture
- attribution summary
- PIT join tests

## Related Checks

- Gate: `G6 PIT + Reproducibility`
- Gate: `G7 Dashboard Status`

