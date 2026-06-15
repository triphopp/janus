# Test Gap Register

| ID | Severity | Gap | Risk | Proposed Test |
|---|---|---|---|---|
| G-001 | Medium | Direct `validate_schema()` missing-column test absent | Schema drift could be under-tested | Add test in `tests/test_ingestion/test_schema.py` |
| G-002 | Medium | Full CLI pipeline not tested with a committed raw fixture | `run_pipeline.py` could break while unit tests pass | Add tiny fixture and subprocess/functional test |
| G-003 | Medium | Adapter golden snapshots not stored | Accidental output changes may slip through | Add lightweight CSV/JSON expected snapshots |
| G-004 | Low | HMM/GMM validators not tested | Optional offline validator path may rot | Add optional tests guarded by dependency availability |
| G-005 | Low | Provider live behavior not tested | Real feeds may differ from fixtures | Keep out of unit tests; add integration test profile later |
| G-006 | Low | v1.4 modules tested but not separated by marker | v1.3 baseline and v1.4 scope can blur | Add pytest markers: `v13`, `v14`, `integration` |

## Current Assessment

v1.3 unit coverage is now broad enough for baseline validation.

Main remaining weakness: no full end-to-end fixture run through `run_pipeline.py`.
