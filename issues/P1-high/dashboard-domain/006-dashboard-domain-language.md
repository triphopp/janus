# Dashboard Domain Language

Urgency: `P1-high`

Status: `draft`

Source plan:

- `docs/design/csv_storage_bounded_context_redesign.md`
- `docs/design/data_test_measurement_criteria.md`

## Summary

Reduce technical terminology on the dashboard and present run risk in domain
language: run readiness, option market checks, volatility mismatch, call/put
price mismatch, held-back rows, and excluded-from-study rows.

## Why It Matters

Technical labels such as `_pcp_flag` and `iv_flag` hide the meaning from domain
users. The dashboard should make the trust decision obvious.

## Scope

In scope:

- Add domain labels to run list and detail view.
- Show `usable`, `needs_review`, `blocked`, and `not_checked`.
- Distinguish held-back rows from excluded-from-study rows.
- Show unit assumptions for IV/rate-sensitive runs.

Out of scope:

- Full visual redesign beyond trust-language changes.

## Acceptance Criteria

- [ ] First screen shows run readiness.
- [ ] First screen shows worst market area.
- [ ] Technical labels do not dominate first screen.
- [ ] `not_checked` is visible and not green.
- [ ] Option-market `blocked` status prevents green/available presentation.

## Evidence Required

- dashboard status snapshot
- view-model tests
- screenshot or DOM assertion for status labels

## Related Checks

- Gate: `G7 Dashboard Status`
- Metric: `run_status_matches_worst_check`
- Metric: `technical_label_leak_count`

