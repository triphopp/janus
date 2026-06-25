# PIT and Reproducibility for Official Runs

Urgency: `P0-critical`

Status: `draft`

Source plan:

- `docs/design/data_test_measurement_criteria.md`

## Summary

Official runs must use fixed input versions, preserve source hashes, and pass
point-in-time availability checks before their results are trusted.

## Why It Matters

A run can look correct but still be unreproducible or based on data that was not
knowable at decision time.

## Scope

In scope:

- Require source hash and config hash.
- Require code version.
- Enforce `available_at <= decision_time`.
- Mark mutable `latest` input as exploration-only.
- Check rerun output hash for data artifacts.

Out of scope:

- Full strategy execution/PnL model.

## Acceptance Criteria

- [ ] Official run without fixed input version is `blocked`.
- [ ] Missing source hash is `blocked`.
- [ ] Any availability violation is `blocked`.
- [ ] Rerun data hash mismatch is `blocked` unless explicitly explained.
- [ ] Market diagnostics are not labelled as strategy performance when PnL is absent.

## Evidence Required

- manifest
- `test_report.json`
- rerun hash comparison

## Related Checks

- Gate: `G0 Source Identity`
- Gate: `G6 PIT + Reproducibility`
- Metric: `available_at_le_decision_time_rate`
- Metric: `rerun_output_hash_match`

