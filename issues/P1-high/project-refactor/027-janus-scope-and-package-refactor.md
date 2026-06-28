# Janus Scope and Package Refactor

Urgency: `P1-high`

Status: `draft`

Source plan:

- Current repository inventory
- `run_pipeline.py`
- `core/`
- `ingestion/`
- `adapters/`
- `web/`
- `core/evidence_harness/`
- `docs/design/audit_findings_pre_data_structure.md`
- `tests/governance/gap_register.md`
- `issues/P1-high/cli-simplification/026-progressive-cli-and-data-source-registry.md`

## Summary

Janus has grown into a broad framework: data ingestion, option cleaning,
quality gates, Greeks, exports, reporting, dashboard UI, evidence harness,
architecture docs, issue specs, database migrations, and experimental tools all
live in one runtime-shaped repository. That size now makes the project harder to
understand than the current product surface requires.

Refactor Janus around the actual v1 product:

```text
config -> ingest -> prepare -> validate/guard -> export -> summary/report
```

The goal is not to remove trust gates. The goal is to make the trusted path
small, obvious, testable, and easy to run.

## Why It Matters

The current structure increases maintenance risk:

- users must learn internal architecture to run simple workflows
- unrelated modules make code search noisy
- experimental features look production-ready because they sit beside runtime code
- docs, issue specs, dashboard, evidence harness, and pipeline code evolve at
  different speeds in the same namespace
- refactors are risky because the minimal end-to-end product path is not clearly
  isolated

After the P0 data-integrity work, Janus should make the correct path easier than
the incorrect path. A smaller package structure is part of that.

## Product Scope For v1

In scope for the slim runtime:

- ticker/profile resolution
- config loading and validation
- registered local data source resolution
- file-backed settlement ingestion
- equity/provider diagnostic ingestion where explicitly marked diagnostic
- futures-options preparation
- equity preparation needed by active examples/tests
- P0 guards: hash pinning, settlement availability, IV units, PIT timing,
  option-market readiness, contract gates
- option-chain Greeks downstream export
- summary/manifest writing
- minimal human-readable report
- progressive CLI facade from issue 026

Out of scope for the slim runtime:

- evidence harness as a default runtime dependency
- dashboard frontend as a required runtime path
- architecture papers/slides as runtime-adjacent files
- completed issue specs in active work folders
- experimental benchmarking tools in the main command surface
- provider-specific branches that are not used by a current workflow

## Target Package Shape

Introduce a package boundary that separates core runtime from apps and labs:

```text
janus/
  cli/
  core/
    config/
    data_sources/
    ingestion/
    preparation/
    quality/
    guards/
    options/
    export/
    pipeline/
    reporting/
  apps/
    dashboard/
  labs/
    evidence_harness/
    experiments/
  archive/
    docs/
    issues/
```

This is a target shape, not a first patch. Migration should happen in small
steps with compatibility shims.

## Refactor Principles

- Preserve behavior before renaming or moving logic.
- Keep P0 guard semantics fail-closed.
- Do not mix file moves with behavioral changes unless the issue explicitly
  requires it.
- Keep old import paths working temporarily through shims.
- Do not delete historical docs until links and issue indexes are updated.
- Archive or quarantine experimental code before deleting it.
- Every migration phase needs focused tests and a smoke command.
- Public examples must not include private data paths or raw vendor rows.

## Phase 0: Baseline And Safety Harness

Goal: create a stable baseline before moving files.

Tasks:

- Add a small public-safe end-to-end fixture run if it does not already exist.
- Ensure the fixture proves summary writing, guard status, and export artifact
  creation.
- Add a repo inventory document or generated report that classifies files as
  runtime, app, lab, docs, tests, or archive.
- Record current CLI compatibility expectations.
- Run the focused test suite and note known warnings.

Acceptance criteria:

- [ ] Minimal fixture run exercises the main pipeline without the large local WTI file.
- [ ] Fixture output includes `summary.json`.
- [ ] Fixture output includes downstream export artifacts for an options-like case.
- [ ] Test command is documented in the issue or README.
- [ ] Known warnings are documented and not confused with refactor failures.

Evidence required:

- Test output for the fixture run.
- Artifact paths from the fixture output.
- File classification summary.

## Phase 1: Progressive CLI Facade

Goal: give users a simpler entrypoint while the old pipeline internals remain in
place.

Related issue:

- `issues/P1-high/cli-simplification/026-progressive-cli-and-data-source-registry.md`

Tasks:

- Add `janus run`, `janus import`, `janus doctor`, `janus explain`, `janus list`,
  and `janus show` facade commands.
- Keep `run_pipeline.py` compatibility during migration.
- Convert user-facing language from `instrument` to ticker/symbol.
- Move normal external data-file usage into import/register flow.
- Keep custom dates with `--from` / `--to` and compatibility aliases
  `--start` / `--end`.

Acceptance criteria:

- [ ] A user can run `janus run WTI --window 2024Q4` when WTI is configured.
- [ ] A user can run `janus run WTI --from 2024-09-25 --to 2024-12-31`.
- [ ] Missing data errors include the next command to run.
- [ ] Old `run_pipeline.py` commands still work with deprecation warnings.

Evidence required:

- CLI unit tests.
- Golden output tests for missing-data guidance.
- Compatibility test for old command shape.

## Phase 2: Separate Runtime From Apps And Labs

Goal: reduce mental load by moving non-core systems out of the runtime path.

Tasks:

- Classify `web/` as app code.
- Classify `core/evidence_harness/` and related migrations as lab code unless a
  product decision says otherwise.
- Move app/lab code under clear namespaces or archive paths.
- Keep import compatibility shims where tests still rely on old locations.
- Update docs and test paths after each move.

Candidate moves:

```text
web/ -> janus/apps/dashboard/
core/evidence_harness/ -> janus/labs/evidence_harness/
db/migrations/evidence_graph/ -> janus/labs/evidence_harness/db/migrations/
tools/* experimental scripts -> janus/labs/experiments/ or archive/tools/
```

Acceptance criteria:

- [ ] Core runtime imports do not depend on dashboard frontend files.
- [ ] Core runtime imports do not depend on evidence-harness modules.
- [ ] Dashboard tests still pass or are clearly separated as app tests.
- [ ] Evidence-harness tests still pass or are clearly separated as lab tests.

Evidence required:

- Import graph or `rg` evidence showing no core-to-app/lab dependency.
- Focused app/lab test output.

## Phase 3: Repackage Core Runtime

Goal: turn the current broad `core/`, `ingestion/`, and `adapters/` layout into
an explicit runtime package.

Candidate grouping:

```text
janus/core/config/
janus/core/data_sources/
janus/core/ingestion/
janus/core/preparation/
janus/core/quality/
janus/core/guards/
janus/core/options/
janus/core/export/
janus/core/pipeline/
janus/core/reporting/
```

Tasks:

- Move config normalization and profile resolution into `janus/core/config`.
- Move source registry/hash resolution into `janus/core/data_sources`.
- Move ingestion providers into `janus/core/ingestion`.
- Move adapters into `janus/core/preparation`.
- Move validators, readiness, coverage, and contracts into quality/guards.
- Move option Greeks, option quality, and option-chain export into options/export.
- Keep old module import shims until migration is complete.

Acceptance criteria:

- [ ] Public runtime imports use the new package paths.
- [ ] Existing tests pass through shims during transition.
- [ ] No behavioral diff in fixture summary/export compared with baseline except
      path metadata where expected.

Evidence required:

- Fixture summary diff.
- Full or focused test output.
- Import-shim removal checklist.

## Phase 4: Reporting Slim-Down

Goal: keep a useful report without making reporting the largest runtime module.

Tasks:

- Split machine artifacts from human reports.
- Keep `summary.json`, manifest, schema, data dictionary, and export files as
  core outputs.
- Move rich HTML/dashboard report generation into app/reporting package.
- Keep a minimal text/Markdown summary in core.

Acceptance criteria:

- [ ] Core pipeline can complete without dashboard frontend dependencies.
- [ ] Core artifacts remain stable.
- [ ] Rich reports are still available through an app/report command.

Evidence required:

- Run output listing.
- Report-generation test output.

## Phase 5: Archive Historical And Planning Material

Goal: reduce active tree noise while preserving useful history.

Tasks:

- Move completed implementation specs into archive or keep only index links in
  active issue docs.
- Keep active issues under `issues/P*-*/`.
- Move architecture slides/papers that are not used by current development into
  `docs/archive/`.
- Keep a concise `docs/README.md` pointing to current docs only.

Acceptance criteria:

- [ ] Active issue index lists only active work.
- [ ] Completed work remains discoverable.
- [ ] README quick start no longer links users into archive material.

Evidence required:

- Updated issue index.
- Updated docs index.
- Link check or `rg` check for stale paths.

## Phase 6: Remove Compatibility Shims

Goal: finish the refactor after users and tests have moved to new paths.

Tasks:

- Remove deprecated CLI args after the deprecation window.
- Remove old import shims.
- Remove archived experimental runtime entrypoints from default docs.
- Update tests to import only new package paths.

Acceptance criteria:

- [ ] No tests import deprecated package paths.
- [ ] `janus run` is the documented primary path.
- [ ] Old CLI help no longer appears in quick-start docs.

Evidence required:

- `rg` output for deprecated imports/args.
- Test output.

## What Not To Do

- Do not weaken P0 data-integrity checks to make refactor easier.
- Do not delete evidence-harness or dashboard code in the same patch that moves
  core runtime logic.
- Do not mix a package move with CLI behavior changes unless covered by a
  specific phase.
- Do not commit local data-source registries containing private paths.
- Do not update generated run outputs as part of package-only refactors.

## Public-Safe Notes

- Use synthetic or minimized public fixtures.
- Do not include local absolute paths.
- Do not include raw vendor rows.
- Keep archived material public-safe before moving it.

## Acceptance Criteria

- [ ] Janus has a documented slim runtime scope.
- [ ] User-facing entrypoint is `janus run`, not `run_pipeline.py`.
- [ ] Core runtime package can be understood without reading dashboard,
      evidence-harness, or architecture-paper code.
- [ ] External data uses import/register flow before official runs.
- [ ] Large experimental/lab systems are separated from runtime imports.
- [ ] Minimal fixture protects the trusted path through every migration phase.
- [ ] Old paths and args have a compatibility/deprecation plan.
- [ ] README quick start is shorter than the current one and does not require
      advanced architecture terms.

## Evidence Required

- Baseline fixture output before moves.
- Fixture output after each major phase.
- Focused test output for core runtime.
- Focused test output for apps/labs when moved.
- `rg` checks for deprecated args/imports before shim removal.
- Updated README and issue index.

## Related Checks

- Gate: fixed input hash remains required for official file-backed runs.
- Gate: settlement availability remains required for settlement options.
- Gate: IV unit assumptions remain explicit and checked.
- Gate: downstream export remains withheld when option-market readiness is blocked.
- Metric: README quick-start command count decreases.
- Metric: number of modules imported by the core runtime decreases.
- Expected status: a new user can run or diagnose WTI without reading internal
  architecture docs.
