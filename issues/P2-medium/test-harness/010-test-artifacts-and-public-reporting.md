# Test Artifacts and Public Reporting

Urgency: `P2-medium`

Status: `draft`

Source plan:

- `docs/design/data_test_measurement_criteria.md`

## Summary

Standardize the test artifacts that prove data readiness decisions.

## Why It Matters

Without stable artifacts, a run can fail in code but remain hard to review or
explain publicly.

## Scope

In scope:

- Write `test_report.json`.
- Write `gate_summary.csv`.
- Write `row_reconciliation.csv`.
- Write `unit_assumptions.json`.
- Write `market_checks_summary.csv`.
- Write `failing_examples.csv`.
- Write `dashboard_status_snapshot.json`.

Out of scope:

- Full report UI redesign.

## Acceptance Criteria

- [ ] Every gate writes metric, threshold, status, and example artifact.
- [ ] Failing examples are public-safe and do not include licensed raw rows.
- [ ] Dashboard status snapshot matches run summary.
- [ ] Artifacts are deterministic where practical.

## Evidence Required

- generated artifact folder
- artifact schema tests

## Related Checks

- Gate: all gates `G0` to `G7`

