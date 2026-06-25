# CSV Bundle Storage Redesign

Urgency: `P1-high`

Status: `draft`

Source plan:

- `docs/design/csv_storage_bounded_context_redesign.md`

## Summary

Replace the current single wide prepared-data artifact as the canonical output
with a public, inspectable bundle of narrow CSV files and a manifest.

## Why It Matters

The wide prepared output mixes source facts, futures support rows, option rows,
analytics, and quality flags. This makes grain mistakes and unit mistakes hard
to detect.

## Scope

In scope:

- Define and write `dataset_manifest.json`.
- Define and write canonical CSVs for market prices, option contracts, market
  checks, research universe, analytics values, and run health.
- Keep old prepared CSV only as a compatibility view.

Out of scope:

- Removing existing exports immediately.
- Building a database store.

## Acceptance Criteria

- [ ] Each canonical CSV has one grain.
- [ ] Canonical CSVs use ISO dates and RFC3339 UTC timestamps.
- [ ] Source lineage is preserved.
- [ ] Manifest records schema versions and unit assumptions.
- [ ] Compatibility prepared export is labelled as a view.

## Evidence Required

- generated sample bundle
- schema checks
- manifest review

## Related Checks

- Gate: `G1 Format`
- Gate: `G2 Schema + Grain`
- Gate: `G3 Lineage + Row Reconciliation`

