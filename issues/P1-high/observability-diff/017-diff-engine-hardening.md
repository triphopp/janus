# Diff Engine Hardening

Urgency: `P1-high`

Status: `draft`

Source plan:

- `memory/plans/data_diff_design.md`
- `Policy/diff_ledger_review_policy.md`

## Summary

Harden the diff engine around identity uniqueness, ambiguous reason attribution,
and manifest-compatible frame comparisons.

## Why It Matters

Diffs are only useful if they compare the same logical rows from compatible
vintages and explain changes without hiding ambiguous mutations.

## Scope

In scope:

- Add duplicate identity-key gate before diffing.
- Add `identity_key_version`.
- Distinguish `UNATTRIBUTED` from `AMBIGUOUS`.
- Require stage frames to carry source hash, contract version, knowledge cutoff,
  run manifest hash, and stage code hash.
- Refuse comparisons across incompatible manifests.

Out of scope:

- Adopting heavy external data-versioning infrastructure.

## Acceptance Criteria

- [ ] Duplicate identity keys create a break and stop unsafe diff.
- [ ] Ambiguous reason attribution is reported separately from unattributed changes.
- [ ] Diff refuses incompatible frame lineage.
- [ ] Change records include valid time and knowledge time.
- [ ] Review rollups include deterministic samples.

## Evidence Required

- diff fixture with duplicate identity
- diff fixture with ambiguous mutation reason
- generated diff summary

## Related Checks

- Gate: `G3 Lineage + Row Reconciliation`

