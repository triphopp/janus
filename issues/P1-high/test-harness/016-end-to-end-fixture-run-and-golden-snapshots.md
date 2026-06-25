# End-to-End Fixture Run and Golden Snapshots

Urgency: `P1-high`

Status: `draft`

Source plan:

- `tests/governance/gap_register.md`
- `docs/reports/implementation_status_v1_4.md`

## Summary

Add a small committed public-safe fixture that exercises `run_pipeline.py` end to
end and stores golden snapshots for critical adapter outputs.

## Why It Matters

Unit tests can pass while CLI orchestration, config wiring, output artifacts, or
dashboard contracts break.

## Scope

In scope:

- Add a tiny public-safe raw fixture.
- Run `run_pipeline.py` through subprocess or functional test.
- Store lightweight JSON/CSV golden snapshots.
- Add tests for expected output columns, statuses, and artifacts.

Out of scope:

- Including licensed raw vendor data.
- Full large-file performance test.

## Acceptance Criteria

- [ ] Full CLI pipeline fixture runs in CI/test profile.
- [ ] Golden snapshots cover adapter output and summary status.
- [ ] Snapshot diffs are reviewable and deterministic.
- [ ] Tests fail on missing expected artifacts.

## Evidence Required

- fixture file
- golden snapshot files
- end-to-end test output

## Related Checks

- Gap register: `G-002`, `G-003`

