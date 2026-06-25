# Event Calendar PIT Normalization

Urgency: `P0-critical`

Status: `draft`

Source plan:

- `docs/reports/implementation_status_v1_4.md`
- `docs/design/data_structure_reading_map.md`

## Summary

Normalize event calendar rows with `available_at` and require point-in-time joins
for any event feature used by decisions.

## Why It Matters

Events can leak future knowledge if joined by event date alone. Inventory,
earnings, macro, and other calendar data must be knowable before a decision can
use it.

## Scope

In scope:

- Add `available_at` normalization for event CSVs.
- Use PIT joins for event features.
- Record event release-time assumptions.
- Block or mark `not_checked` when event availability is unknown.

Out of scope:

- Building a full event vendor integration.

## Acceptance Criteria

- [ ] Event CSV ingestion produces `available_at`.
- [ ] Event feature joins assert `available_at <= decision_time`.
- [ ] Missing event availability is visible as `not_checked` or `blocked`.
- [ ] Tests include an event row whose release time is after the decision time.

## Evidence Required

- event fixture
- PIT join test
- run summary event-check status

## Related Checks

- Gate: `G6 PIT + Reproducibility`
