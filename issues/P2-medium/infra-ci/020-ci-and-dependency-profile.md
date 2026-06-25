# CI and Dependency Profile

Urgency: `P2-medium`

Status: `draft`

Source plan:

- `tests/governance/gap_register.md`
- `docs/reports/implementation_status_v1_4.md`

## Summary

Add a project test/runtime dependency profile and CI configuration so the public
test suite can run reproducibly.

## Why It Matters

Without a stable dependency profile and CI, local tests can pass or fail based on
machine state rather than project state.

## Scope

In scope:

- Define runtime/test dependencies.
- Add CI configuration.
- Add pytest markers for baseline, current scope, and integration tests.
- Keep live provider tests out of default unit suite.

Out of scope:

- GPU CI.
- Live vendor credentials.

## Acceptance Criteria

- [ ] Public CI can run unit tests from a clean clone.
- [ ] Dependencies are declared in one maintained file.
- [ ] Tests are marked by scope/profile.
- [ ] Live provider tests are opt-in.

## Evidence Required

- CI config
- dependency file
- passing CI run

## Related Checks

- Gap register: `G-004`, `G-005`, `G-006`

