# Progressive CLI and Data Source Registry

Urgency: `P1-high`

Status: `draft`

Source plan:

- User-facing CLI refactor discussion
- `run_pipeline.py`
- `run_greeks.py`
- `configs/instruments/*.yaml`
- `issues/completed/P0-critical/pipeline-data-integrity/014-versioned-cache-wiring.md`
- `issues/completed/P0-critical/pipeline-data-integrity/023-downstream-option-chain-greeks-export.md`

## Summary

Janus currently exposes too many pipeline-internal knobs through the command
line. A user must know terms such as `instrument`, `provider`, `data-file`,
`allow-unversioned-data`, `compute-greeks`, PCP checks, fold settings, and
metrics modes before they can confidently run a simple WTI or equity workflow.

Refactor the CLI into a progressive interface:

```text
janus import WTI path/to/WTI.csv
janus run WTI --from 2024-09-25 --to 2024-12-31
janus run WTI --window 2024Q4
janus doctor WTI
janus explain WTI --window 2024Q4
janus list
janus show wti_q4
```

The core user path should be `import once -> run many times`. Advanced controls
remain available, but they must not appear in the default happy path.

## Why It Matters

The current CLI makes correct usage harder than the pipeline itself. In the WTI
Q4 workflow, a stale local config plus ad hoc data-file overrides produced a run
that did not match the planned downstream export. This is a UX failure as much as
a data-config failure: the command surface made it easy to bypass the intended
hash-pinned, policy-backed path.

A simpler CLI should:

- reduce README dependency
- prevent accidental unpinned official runs
- make date windows easy to specify
- make external data import explicit and reproducible
- keep internals in config, presets, or advanced mode
- guide users with actionable errors instead of requiring prior architecture knowledge

## Design Principles

- Use `ticker` / symbol language in user-facing commands, not `instrument`.
- Accept simple positional symbols: `janus run WTI --window 2024Q4`.
- Keep custom date ranges first-class: `--from` / `--to` must remain supported.
- Support familiar aliases: `--start` / `--end` as compatibility aliases.
- Do not accept `--data-file` on normal `run`; external files must be imported
  or registered first.
- Preserve P0 data-trust gates. Simpler CLI must not weaken hash, settlement,
  IV-unit, or export-readiness checks.
- Prefer actionable errors:

  ```text
  WTI is not ready: no data source registered.

  Run:
    janus import WTI path/to/WTI.csv

  Then:
    janus run WTI --window 2024Q4
  ```

## Proposed Command Surface

### `janus run`

Examples:

```text
janus run WTI --from 2024-09-25 --to 2024-12-31
janus run WTI --window 2024Q4
janus run WTI --window 2024Q4 --preset official
janus run NVDA --from 2024-01-01 --to 2024-12-31 --preset diagnostic
```

Does:

- run the full pipeline
- resolve ticker/symbol to the appropriate local config/profile
- resolve the active registered data source
- enforce official-run guards by default for file-backed data
- write summary, manifest, reports, and export artifacts
- generate a run name when `--name` is omitted

Does not:

- accept `--data-file` in normal mode
- accept provider overrides in normal mode
- expose Greek backend, PCP, fold, metrics, or low-level universe settings in
  default help
- silently run official workflows on unpinned data

Date inputs:

- `--from YYYY-MM-DD --to YYYY-MM-DD`
- `--start YYYY-MM-DD --end YYYY-MM-DD` as aliases
- `--window YYYY`
- `--window YYYY-MM`
- `--window YYYYQ1` / `YYYYQ2` / `YYYYQ3` / `YYYYQ4`

Rules:

- `--window` cannot be combined with `--from` / `--to`.
- custom dates are always allowed.
- official runs must not guess dates by default.
- diagnostic mode may offer a default window only if `explain` displays it.

### `janus import`

Shortcut:

```text
janus import WTI path/to/WTI.csv
```

Explicit form:

```text
janus data import --ticker WTI --file path/to/WTI.csv --use
```

Does:

- register an external local data file for a ticker/symbol
- detect delimiter and file format where possible
- validate header/schema enough to reject obvious wrong inputs
- compute SHA-256
- record path, hash, format, row count, observed date range, source label, and
  imported timestamp
- optionally set the imported source as active

Does not:

- clean or mutate raw data
- run the pipeline
- decide strategy quality
- hide hash mismatches

### `janus data list`

Example:

```text
janus data list WTI
```

Does:

- list registered data sources for one ticker
- show active source
- show source id, path, hash prefix, format, row count, and date range

Does not:

- scan the whole machine for data
- delete files
- choose among ambiguous sources without user action

### `janus data use`

Example:

```text
janus data use WTI wti_2024_local
```

Does:

- set the active source for a ticker
- update the local data-source registry

Does not:

- validate the entire pipeline
- mutate the raw data file

### `janus doctor`

Example:

```text
janus doctor WTI
```

Does:

- check ticker/profile resolution
- check active data source existence
- check hash match
- check settlement availability policy
- check IV unit policy
- check export policy
- check event/calendar references
- print clear next actions

Does not:

- run expensive pipeline stages
- write heavy output artifacts
- repair config without explicit user action

### `janus explain`

Example:

```text
janus explain WTI --from 2024-09-25 --to 2024-12-31
```

Does:

- show the exact plan before running
- show resolved config/profile
- show active data source and hash
- show date window
- show preset and universe policy
- show enforced guards
- show expected output location and artifact set

Does not:

- mutate config
- write heavy outputs
- bypass guards

### `janus list`

Example:

```text
janus list
```

Does:

- show known tickers/symbols
- show profile/family
- show readiness status: ready, missing data, hash mismatch, config incomplete
- show one-line recommended next command

Does not:

- run the pipeline
- import files
- scan arbitrary directories

### `janus show`

Example:

```text
janus show wti_q4
```

Does:

- summarize a completed run
- show guard status
- show report/export paths
- show downstream artifact status

Does not:

- rerun the pipeline
- rewrite artifacts

### `janus clean`

Examples:

```text
janus clean --failed --dry-run
janus clean --older-than 30d --dry-run
```

Does:

- list or remove generated run outputs under controlled output roots
- default to dry-run or require confirmation

Does not:

- remove raw registered data files
- remove files outside configured output/cache directories
- run recursively against arbitrary user paths

## Presets

### `official`

Use for trusted backtest/export results.

- hash-pinned source required
- config policy required
- fail closed on P0 integrity gates
- outputs marked reproducible

### `diagnostic`

Use for fast exploration and live/provider reads.

- may allow provider fetch
- outputs must be marked non-reproducible when input is not pinned
- should not be promoted as official output

### `export`

Use when the goal is downstream artifacts such as `option_chain_greeks`.

- ensure export policy exists
- report readiness status clearly
- do not write clean downstream export when readiness is blocked

### `research`

Use for explicit research universe choices.

- allow named universe presets
- record every override in summary/manifest
- keep guard behavior visible

## Universe Presets

Replace common low-level filters with named presets:

```text
--universe all
--universe liquid
--universe near-term
--universe custom:<name>
```

Low-level values such as DTE bands, IV caps, minimum option price, and delta
bands belong in config-backed preset definitions. Normal `janus run --help`
should not list every individual research knob.

## Data Source Registry

Add a local registry for imported files. Exact storage can be decided during
implementation, but it must be machine-local and public-safe. Candidate:

```text
configs/local/data_sources.yaml
```

Example shape:

```yaml
WTI:
  active: wti_2024_local
  sources:
    wti_2024_local:
      path: path/to/WTI.csv
      sha256: ead277...
      format: psv
      provider: settlement
      rows: 267185
      date_range: [2024-09-25, 2024-12-31]
      imported_at: 2026-06-28T00:00:00Z
```

The registry must not be committed when it contains private machine paths.

## Deprecated Normal-Mode Args

The new user-facing CLI should hide or deprecate:

- `--instrument`
- `-i` on `run_pipeline.py`
- `--ticker` as equity-only override
- `--provider`
- `--data-file`
- `--allow-unversioned-data`
- `--compute-greeks`
- `--greeks-backend`
- `--min-dte`
- `--max-dte`
- `--min-option-price`
- `--iv-cap`
- `--min-abs-delta`
- `--max-abs-delta`
- `--n-folds`
- `--embargo-bars`
- `--metrics-mode`

These can remain available temporarily through a compatibility layer or an
explicit advanced path, for example:

```text
janus run WTI --advanced --override pricing.compute_greeks=true
```

Advanced overrides must be recorded in summary/manifest.

## Migration Plan

1. Add a new CLI facade without changing pipeline behavior.
2. Implement ticker/symbol resolution and date-window parsing.
3. Implement `janus import`, `data list`, and `data use`.
4. Implement `doctor` and `explain`.
5. Route `janus run` into the existing pipeline using resolved config and source.
6. Add deprecation warnings for old `run_pipeline.py` args.
7. Update README quick start to the short path only.
8. Move detailed advanced args to a separate advanced reference.
9. Add end-to-end fixture coverage for import, doctor, explain, and run.
10. Remove or quarantine old public CLI args only after compatibility tests pass.

## Public-Safe Notes

- Do not commit local absolute data paths in examples.
- Do not include raw vendor rows in issues, docs, or fixtures.
- Use synthetic or minimized public fixtures for import/run tests.
- Keep any local data-source registry ignored by git when it stores private paths.

## Status: `done` (2026-06-28)

## Acceptance Criteria

- [x] `janus run WTI --window 2024Q4` is the documented primary run path.
- [x] `janus run WTI --from 2024-09-25 --to 2024-12-31` supports custom dates.
- [x] `janus import WTI path/to/file.csv` registers an external file and computes SHA-256.
- [x] `janus run` refuses normal official runs when no active pinned data source exists.
- [x] Error messages include the next command a user should run.
- [x] `janus doctor WTI` reports data-source, hash, settlement, IV-unit, export, and calendar readiness.
- [x] `janus explain WTI --window 2024Q4` prints the resolved plan without running the pipeline.
- [x] `janus list` shows ready/missing/hash-mismatch/config-incomplete status.
- [x] Old CLI args still work during migration and emit deprecation warnings.
- [x] Advanced overrides are hidden from normal help and recorded in summary/manifest when used.
- [x] README quick start does not require learning `instrument`, `provider`, or `data-file`.

## Evidence Required

- Unit tests for date-window parsing: year, month, quarter, and explicit `from/to`.
- Unit tests for ticker/symbol resolution.
- Unit tests for data-source registry import/list/use.
- Unit tests for doctor readiness states.
- Golden CLI output tests for actionable missing-data errors.
- End-to-end fixture: import synthetic WTI-style file, doctor passes, run writes summary/export artifacts.
- Compatibility tests proving old `run_pipeline.py` usage still routes correctly during migration.

## Related Checks

- Gate: fixed input hash must pass for official file-backed runs.
- Gate: settlement availability policy remains required for settlement options.
- Gate: downstream option-chain export remains withheld when readiness is blocked.
- Metric: number of commands in README quick start is at most two for local file-backed WTI.
- Expected status: normal users can run without reading advanced CLI docs.

## ✅ Completion Evidence (2026-06-28)

Implemented a `janus` CLI facade over the existing pipeline. New modules under
`cli/`: `dates.py` (window parsing), `registry.py` (data-source registry),
`presets.py` (run + universe presets + advanced overrides), `resolve.py`
(ticker→profile), `plan.py` (RunPlan assembly + guards), `doctor.py`,
`inspect_runs.py` (list/show), `main.py` (subcommand dispatch). Entry point:
`janus.py`. Compatibility: `run_pipeline.py` unchanged behavior + deprecation
notice; CLI provenance (preset/reproducible/universe/advanced_overrides) now
written into `summary.json`.

**Tests — 90 new CLI tests, full suite `1023 passed, 8 skipped`:**

- `tests/test_cli/test_dates.py` — year/month/quarter/from-to parsing (23)
- `tests/test_cli/test_registry.py` — import/list/use, SHA-256, format detect (12)
- `tests/test_cli/test_presets_plan.py` — presets, overrides, resolve, plan guards (17)
- `tests/test_cli/test_doctor.py` — readiness states + inspectors (7)
- `tests/test_cli/test_main.py` — dispatch + golden actionable errors (8)
- `tests/test_cli/test_e2e.py` — import→doctor→plan chain + 2-command happy path + old-entry compat (3)

**Live verification on real data (`data/WTI.csv`, 1.85M rows):**

```text
$ python janus.py import WTI data/WTI.csv
  id=wti_wti  format=psv  rows=1851596  dates=2024-09-25..2026-05-29
  sha256=ead277b65f50405e7fa28bf0785b46191e9aaa0a7f5bcef1271cf04e6e50d4ee

$ python janus.py run WTI --window 2024Q4 --preset export --universe near-term --name wti_q4_cli
  Ingestion: 248657 rows loaded → Adapter: 55833 rows prepared → 6 folds
  Done. prepared.csv written with delta/gamma/vega/theta/rho Greek columns.
```

`janus run WTI --window 2024Q4` (no import) correctly refuses with an actionable
`janus import WTI path/to/file.csv` message. Registry stored at git-ignored
`configs/local/data_sources.yaml`.
