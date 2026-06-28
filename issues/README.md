# Public Issues

This folder contains public-safe implementation issues derived from the data
trust reset plan.

The issue files are intended to be committed to the repository. Do not include
private local paths, raw vendor data rows, credentials, proprietary data
samples, or screenshots containing licensed data.

## Urgency Folders

| Folder | Meaning | Expected action |
| --- | --- | --- |
| `P0-critical` | Trust, correctness, or public-safety issue that can invalidate results | Fix before trusting new backtests or dashboards |
| `P1-high` | Important hardening needed before broad use | Plan and implement after P0 is stable |
| `P2-medium` | Compatibility, reporting, or polish work | Implement once core trust gates exist |

Completed issues are moved under `completed/<urgency>/...` so active urgency
folders contain only open work.

## Issue Naming

Use this format:

```text
issues/<urgency>/<branch-group>/<number>-<short-kebab-title>.md
```

Example:

```text
issues/P0-critical/pipeline-data-integrity/001-wti-incident-regression.md
```

## Branch Groups

Each urgency folder is split by the branch/workstream the issue belongs to. When
creating implementation branches, prefer the `codex/<branch-group>` naming shape
unless the work needs a narrower one.

| Branch group | Suggested branch | Owns |
| --- | --- | --- |
| `pipeline-data-integrity` | `codex/pipeline-data-integrity` | urgent pipeline data distortion, IV units, row reconciliation, grain, PIT, calendars, cache |
| `strategy-metrics` | `codex/strategy-metrics` | strategy PnL layer, transaction costs, metric truthfulness |
| `storage-contracts` | `codex/storage-contracts` | CSV bundle and compatibility exports |
| `dashboard-domain` | `codex/dashboard-domain` | domain-language dashboard status and labels |
| `equity-audit` | `codex/equity-audit` | equity options, equity price, PIT factor data audits |
| `test-harness` | `codex/test-harness` | end-to-end fixtures, golden snapshots, public test artifacts |
| `observability-diff` | `codex/observability-diff` | CDC/diff hardening and review evidence |
| `infra-ci` | `codex/infra-ci` | dependency profile and CI |
| `greeks-performance` | `codex/greeks-performance` | CUDA verification and Greek benchmark work |
| `cli-simplification` | `codex/cli-simplification` | progressive CLI, ticker-first UX, data-source import/registry |
| `project-refactor` | `codex/project-refactor` | scope reduction, package boundaries, runtime/app/lab separation |
| `output-artifacts` | `codex/output-artifacts` | human-first run folders, artifact index, output profiles |

## Public-Safe Rules

- Link to repo docs, not local absolute paths.
- Use synthetic or minimized public fixtures.
- Do not paste raw vendor records.
- Use domain labels where possible.
- Keep evidence requirements explicit and reproducible.

## Source Plans

- `docs/design/csv_storage_bounded_context_redesign.md`
- `docs/design/data_test_measurement_criteria.md`
- `docs/design/audit_findings_pre_data_structure.md`
- `docs/design/data_structure_reading_map.md`
- `docs/reports/implementation_status_v1_4.md`
- `tests/governance/gap_register.md`
- `memory/plans/data_ops_architecture.md`
- `memory/plans/data_diff_design.md`
- `memory/plans/2026-06-23_greek_compute_acceleration_cuda_transition.md`
- `memory/plans/2026-06-23_remove-scalar-greek-path.md`

## Current Issue Index

### P0 Critical

No active P0 issues. Completed P0 issues are tracked under
`completed/P0-critical`.

### P1 High

`storage-contracts`

- `P1-high/storage-contracts/005-csv-bundle-storage-redesign.md`

`dashboard-domain`

- `P1-high/dashboard-domain/006-dashboard-domain-language.md`

`equity-audit`

- `P1-high/equity-audit/007-equity-options-trust-audit.md`
- `P1-high/equity-audit/008-equity-price-trust-audit.md`
- `P1-high/equity-audit/019-equity-factor-attribution-pit-data.md`

`test-harness`

- `P1-high/test-harness/016-end-to-end-fixture-run-and-golden-snapshots.md`

`observability-diff`

- `P1-high/observability-diff/017-diff-engine-hardening.md`

`strategy-metrics`

- `P1-high/strategy-metrics/018-transaction-cost-calibration.md`

`cli-simplification`

- `P1-high/cli-simplification/026-progressive-cli-and-data-source-registry.md`

`project-refactor`

- `P1-high/project-refactor/027-janus-scope-and-package-refactor.md`

`output-artifacts`

- `P1-high/output-artifacts/028-run-output-artifact-simplification.md`

### P2 Medium

`storage-contracts`

- `P2-medium/storage-contracts/009-prepared-csv-compatibility-export.md`

`test-harness`

- `P2-medium/test-harness/010-test-artifacts-and-public-reporting.md`

`infra-ci`

- `P2-medium/infra-ci/020-ci-and-dependency-profile.md`

`greeks-performance`

- `P2-medium/greeks-performance/021-greek-cuda-verification-and-benchmark.md`

## Completed Issue Index

### P0 Critical

Moved to `completed/P0-critical` on 2026-06-28 after the full repository test
suite reported `953 passed, 8 skipped`.

`pipeline-data-integrity`

- `completed/P0-critical/pipeline-data-integrity/000-implementation-sequence.md`
- `completed/P0-critical/pipeline-data-integrity/001-wti-incident-regression.md`
- `completed/P0-critical/pipeline-data-integrity/002-unit-registry-iv-scaling.md`
- `completed/P0-critical/pipeline-data-integrity/003-option-market-checks-run-status.md`
- `completed/P0-critical/pipeline-data-integrity/004-pit-reproducibility-official-runs.md`
- `completed/P0-critical/pipeline-data-integrity/012-split-date-and-contract-grain.md`
- `completed/P0-critical/pipeline-data-integrity/013-exchange-calendar-coverage.md`
- `completed/P0-critical/pipeline-data-integrity/014-versioned-cache-wiring.md`
- `completed/P0-critical/pipeline-data-integrity/015-event-calendar-pit-normalization.md`
- `completed/P0-critical/pipeline-data-integrity/022-settlement-availability-anchor.md`
- `completed/P0-critical/pipeline-data-integrity/023-downstream-option-chain-greeks-export.md`
- `completed/P0-critical/pipeline-data-integrity/024-option-chain-greeks-data-dictionary.md`
- `completed/P0-critical/pipeline-data-integrity/025-iv-validation-trust-exchange-primary.md`

`strategy-metrics`

- `completed/P0-critical/strategy-metrics/011-strategy-pnl-layer-required.md`
