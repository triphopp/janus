# WTI Incident Regression

Urgency: `P0-critical`

Status: `draft`

Source plan:

- `docs/design/data_test_measurement_criteria.md`
- `docs/design/csv_storage_bounded_context_redesign.md`

## Summary

Add a regression suite for the WTI data incident so the pipeline cannot again
present a run as normal when option-market checks are severely unreliable.

## Why It Matters

The current prepared output can contain large IV and call/put consistency
failures while the run still appears mostly usable. This can invalidate
backtests and dashboard conclusions.

## Scope

In scope:

- Build a public-safe minimized WTI-style fixture.
- Assert raw fixture contains both futures and option rows.
- Assert futures rows and option rows are separated by grain in canonical output.
- Assert row reconciliation uses domain keys, not row index.
- Assert high IV or call/put mismatch rates change run readiness.

Out of scope:

- Publishing licensed raw vendor data.
- Redesigning all storage tables in this issue.

## Public-Safe Notes

- Use synthetic or minimized rows that reproduce the structure and failure mode.
- Do not include the private source file path.
- Do not paste raw vendor data from the full source file.

## Acceptance Criteria

- [ ] Fixture includes source futures rows and option rows.
- [ ] Futures rows map to `market_prices.csv` or equivalent canonical table.
- [ ] Option rows map to `option_contracts.csv` or equivalent canonical table.
- [ ] Row reconciliation fails if row index is used as the join key.
- [ ] Provider IV and model IV mismatch can trigger `needs_review` or `blocked`.
- [ ] Call/put mismatch can trigger `needs_review` or `blocked`.
- [ ] Dashboard or summary no longer reports option checks as merely `available`.

## Evidence Required

- `gate_summary.csv`
- `row_reconciliation.csv`
- `market_checks_summary.csv`
- run summary containing domain run readiness

## Related Checks

- Gate: `G3 Lineage + Row Reconciliation`
- Gate: `G5 Domain Market Checks`
- Gate: `G7 Dashboard Status`

