# Run Output Artifact Simplification

Urgency: `P1-high`

Status: `draft`

Source plan:

- User feedback on `outputs/` readability
- `run_pipeline.py`
- `core/reporting.py`
- `core/manifest.py`
- `core/option_chain_export.py`
- `issues/P1-high/cli-simplification/026-progressive-cli-and-data-source-registry.md`
- `issues/P1-high/project-refactor/027-janus-scope-and-package-refactor.md`

## Summary

Janus run outputs are currently too noisy for day-to-day use. A completed run
writes machine artifacts, large prepared datasets, diagnostic tables, reports,
manifests, and downstream exports into one folder shape. Users must inspect the
filesystem and know which files matter.

Refactor run outputs into a human-first surface:

```text
outputs/
  index.jsonl
  latest/
    WTI.json
  runs/
    WTI/
      wti_q4_2024/
        README.md
        summary.json
        artifacts.json
        report.html
        exports/
          option_chain_greeks/
            option_chain_greeks.csv
            manifest.json
            schema.json
            data_dictionary.md
        debug/
          prepared.parquet
          prepared_preview.csv
          tables/
          audit/
          cdc/
```

Opening a run folder should answer:

- What happened?
- Did it pass?
- Which output should I use?
- Where are the debug details if I need them?

## Why It Matters

The pipeline can be technically correct while still being hard to use. If users
must manually inspect many output files to understand a run, they will miss
important guard failures, confuse prepared/debug data with downstream-ready
exports, or rely on heavy CSVs that were meant for diagnostics.

The output folder is part of the product surface. It should guide users the same
way the new CLI should guide users.

## Design Principles

- Default output should be readable by humans.
- Machine artifacts should be indexed, not discovered by folder walking.
- Downstream exports should be separated from debug/prepared artifacts.
- Large debug artifacts should not be written by default when a smaller, stable
  artifact exists.
- Existing `summary.json` consumers should keep working during migration.
- Debug detail must remain available through an explicit output profile.
- Run output must be public-safe and must not include raw vendor rows in reports.
- Windows compatibility matters; prefer JSON pointer files over symlink-only
  designs.

## Output Profiles

### `minimal`

For normal users who want the result and downstream files.

Writes:

- `README.md`
- `summary.json`
- `artifacts.json`
- export artifacts when eligible
- compact report or report link

Does not write:

- full `prepared.csv`
- audit snapshots
- CDC ledgers
- full diagnostic tables unless required by a failed gate summary

### `standard`

Default profile.

Writes everything in `minimal`, plus:

- `prepared.parquet`
- `prepared_preview.csv`
- compact per-fold/per-regime summaries
- human report

Does not write:

- full `prepared.csv` by default
- verbose debug ledgers unless requested

### `debug`

For development, incident review, and deep audit.

Writes:

- all `standard` artifacts
- full `prepared.csv`
- audit snapshots
- CDC/diff ledgers
- full diagnostic tables
- any compatibility artifacts needed by existing tools

## Proposed Run Folder Contract

### First-level files

Each run folder should keep the top level small:

```text
README.md
summary.json
artifacts.json
report.html
exports/
debug/
```

Top-level files:

- `README.md`: short human run card with status, rows, guards, exports, warnings,
  and next actions.
- `summary.json`: stable machine summary.
- `artifacts.json`: canonical index of files produced by the run.
- `report.html`: optional human report, or a small redirect/entry page when rich
  reports move to app/reporting.

Directories:

- `exports/`: downstream-ready outputs.
- `debug/`: prepared data, tables, audit, CDC, and other inspection material.

### `artifacts.json`

Add a structured artifact index so tools do not need to guess from paths:

```json
{
  "run_id": "wti_q4_2024",
  "ticker": "WTI",
  "created_at": "2026-06-28T00:00:00Z",
  "profile": "standard",
  "artifacts": [
    {
      "key": "option_chain_greeks_csv",
      "audience": "downstream",
      "path": "exports/option_chain_greeks/option_chain_greeks.csv",
      "format": "csv",
      "rows": 174574,
      "bytes": 26976204,
      "description": "Clean downstream option-chain Greeks export"
    },
    {
      "key": "prepared_parquet",
      "audience": "debug",
      "path": "debug/prepared.parquet",
      "format": "parquet",
      "description": "Prepared internal pipeline frame"
    }
  ]
}
```

Artifact fields:

- `key`
- `audience`: `human | downstream | machine | debug`
- `path`
- `format`
- `rows`
- `bytes`
- `description`
- `generated_at`
- `schema_path` when available
- `manifest_path` when available

### `README.md`

The run card should be short:

```text
# WTI Q4 2024

Status: needs_review
Rows: raw 267185, prepared 179655, exported 174574
Guards: cache pass, settlement pass, IV pass, option readiness needs_review

Use:
- exports/option_chain_greeks/option_chain_greeks.csv

Review:
- summary.json
- report.html

Debug:
- debug/prepared.parquet
- debug/tables/
```

Do not include raw vendor data rows.

## Output Index

Add a lightweight global index:

```text
outputs/index.jsonl
outputs/latest/WTI.json
```

`index.jsonl` should append one compact record per run:

```json
{"run_id":"wti_q4_2024","ticker":"WTI","status":"needs_review","path":"runs/WTI/wti_q4_2024","created_at":"..."}
```

`latest/WTI.json` should point to the latest run for a ticker. Use JSON pointer
files instead of requiring symlinks.

## CLI Integration

Related issue:

- `issues/P1-high/cli-simplification/026-progressive-cli-and-data-source-registry.md`

New CLI should support:

```text
janus run WTI --window 2024Q4 --output minimal
janus run WTI --window 2024Q4 --output standard
janus run WTI --window 2024Q4 --output debug
janus show wti_q4_2024
janus clean --failed --dry-run
```

`janus show` should read `artifacts.json` and `summary.json`, then print a
compact run card without requiring the user to open folders.

## Backward Compatibility

During migration:

- Keep `summary["artifacts"]` populated.
- Keep existing artifact keys stable.
- Either write compatibility copies or expose compatibility paths through
  `artifacts.json` until dashboard/reporting consumers are migrated.
- Do not remove old output paths until tests prove consumers no longer need them.
- Avoid changing the semantics of downstream exports such as
  `option_chain_greeks.csv`.

## Migration Plan

1. Add `artifacts.json` generation while keeping the current folder layout.
2. Add `README.md` run card generation.
3. Add output profile config: `minimal`, `standard`, `debug`.
4. Stop writing full `prepared.csv` by default in `standard`; write
   `prepared.parquet` and `prepared_preview.csv` instead.
5. Move debug artifacts under `debug/` while keeping compatibility pointers.
6. Move downstream-ready outputs under `exports/`.
7. Add `outputs/index.jsonl` and `outputs/latest/*.json`.
8. Wire `janus show` and `janus clean` to the artifact index.
9. Update dashboard/reporting/tests to read `artifacts.json`.
10. Remove compatibility copies after a deprecation window.

## Public-Safe Notes

- Do not include local absolute paths in issue docs.
- Do not paste raw vendor records.
- Human reports and run cards must summarize data, not expose raw licensed rows.
- Test fixtures should be synthetic or minimized public-safe data.

## Acceptance Criteria

- [ ] Every run writes `artifacts.json`.
- [ ] Every run writes a short top-level `README.md` run card.
- [ ] Standard output profile does not write full `prepared.csv` by default.
- [ ] Standard output profile writes `prepared.parquet` and `prepared_preview.csv`.
- [ ] Downstream-ready files live under `exports/`.
- [ ] Debug-heavy files live under `debug/`.
- [ ] Top-level run folder contains only the small human/machine entrypoints and
      `exports/` / `debug/` directories.
- [ ] `summary.json` still contains existing artifact keys during migration.
- [ ] `outputs/index.jsonl` records completed runs.
- [ ] `outputs/latest/<ticker>.json` points to the latest run.
- [ ] `janus show <run>` can summarize a run without folder inspection.
- [ ] Existing dashboard/report tests are updated or compatibility paths remain.

## Evidence Required

- Unit tests for artifact index generation.
- Unit tests for output profile behavior.
- Fixture run showing minimal/standard/debug profile differences.
- Fixture run proving downstream export path under `exports/`.
- Test proving full `prepared.csv` is only written in debug profile or explicit
  compatibility mode.
- `janus show` golden output test.
- Compatibility test proving old summary artifact keys still resolve.

## Related Checks

- Gate: downstream option-chain export remains withheld when readiness is blocked.
- Gate: summary/report must display guard failures without requiring debug files.
- Metric: top-level run folder entry count is small and stable.
- Metric: default output byte size decreases for large WTI-style runs.
- Expected status: users can identify the correct downstream output from the run
  folder or `janus show` without reading README internals.
