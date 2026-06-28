# Split Date-Grain and Contract-Grain Data

Urgency: `P0-critical`

Status: `draft`

Source plan:

- `docs/design/audit_findings_pre_data_structure.md`
- `docs/design/data_structure_reading_map.md`
- `docs/design/csv_storage_bounded_context_redesign.md`

## Summary

Separate date-grain market series from contract-grain option chains before
computing rolling features, regimes, VRP, folds, or dashboard health.

## Why It Matters

Option chains contain many rows per date. Rolling or expanding calculations over
the long chain can mix strikes and expiries, producing regimes and fold metrics
that are not well-defined for a decision date.

## Scope

In scope:

- Define date-grain and contract-grain tables.
- Reject rolling/time-series operations on mixed-grain frames.
- Require feature declarations to state their grain and selection rule.
- Add same-date shuffle and future-truncation tests.

Out of scope:

- Full volatility surface interpolation.

## Acceptance Criteria

- [ ] Date-grain features use one row per decision date.
- [ ] Contract-grain checks stay on option contract rows.
- [ ] Rolling/regime code rejects mixed-grain inputs.
- [ ] Same-date row shuffle does not change date-level features.
- [ ] Future truncation produces identical past features.
- [ ] VRP, skew, and term-structure features declare selection rules.

## Evidence Required

- grain contract tests
- feature metadata
- failing example for mixed-grain rolling operation

## Related Checks

- Gate: `G2 Schema + Grain`
- Gate: `G5 Domain Market Checks`

