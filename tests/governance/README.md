# Testing Governance

This folder tracks whether the test scope is appropriate.

Executable tests live directly under `tests/`. This folder is for review artifacts:

- `v1_3_scope.md` - what v1.3 must prove
- `coverage_matrix.md` - blueprint requirement to test-file mapping
- `gap_register.md` - known weak spots and untested risks
- `runbook.md` - how to run the suite consistently
- `test_run_log.md` - latest verified runs and outcomes

Rule: a feature is not considered validated only because a file exists. It needs:

1. executable tests in `tests/`
2. a row in `coverage_matrix.md`
3. no open critical gap in `gap_register.md`
4. a passing run recorded in `test_run_log.md`
