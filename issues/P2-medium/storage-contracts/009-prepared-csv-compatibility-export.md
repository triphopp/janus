# Prepared CSV Compatibility Export

Urgency: `P2-medium`

Status: `draft`

Source plan:

- `docs/design/csv_storage_bounded_context_redesign.md`
- `docs/design/data_test_measurement_criteria.md`

## Summary

Keep `prepared.csv` only as a compatibility export after canonical narrow CSV
tables exist. It must not be treated as the source of truth.

## Why It Matters

Existing tools may rely on `prepared.csv`, but the wide mixed-grain shape is a
known trust risk.

## Scope

In scope:

- Add compatibility metadata.
- Add row-order warning.
- Point users to canonical CSV bundle.
- Ensure tests do not rely on row index alignment.

Out of scope:

- Removing `prepared.csv`.

## Acceptance Criteria

- [ ] Export is labelled as compatibility view.
- [ ] Warning says row order is not stable across contexts.
- [ ] Manifest identifies canonical source tables.
- [ ] Public docs say not to reconcile by row index.

## Evidence Required

- manifest
- generated compatibility CSV
- docs update

## Related Checks

- Gate: `G3 Lineage + Row Reconciliation`
- Gate: `G7 Dashboard Status`

