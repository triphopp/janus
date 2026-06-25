# Wire Versioned Cache Into Pipeline Ingestion

Urgency: `P0-critical`

Status: `draft`

Source plan:

- `docs/reports/implementation_status_v1_4.md`
- `docs/design/data_ops_architecture.md`

## Summary

Make `run_pipeline.py` use the versioned, point-in-time cache by default for
official runs instead of reading providers directly.

## Why It Matters

Provider-direct reads are not reproducible and can change between runs. Official
backtests need fixed input versions and manifests.

## Scope

In scope:

- Default official runs to fixed data version or source hash.
- Support explicit exploration mode for live/latest provider reads.
- Ensure cache read/write records source hash and knowledge time.
- Retire or clearly deprecate non-versioned cache paths.

Out of scope:

- Replacing the storage engine with a database.

## Acceptance Criteria

- [ ] Official run without fixed input is `blocked` or requires explicit override.
- [ ] Pipeline can read a committed fixture through versioned cache.
- [ ] Manifest records cache version and source hash.
- [ ] Provider-direct mode is labelled exploration-only.

## Evidence Required

- pipeline fixture run
- manifest
- cache read/write tests

## Related Checks

- Gate: `G0 Source Identity`
- Gate: `G6 PIT + Reproducibility`
