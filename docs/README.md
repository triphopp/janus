# Janus Documentation Map

Use this folder as a source-of-truth map for Janus design, operations, and
paper figures. The top-level folders are intentionally separated by audience and
document lifecycle.

## Start Here

- `architecture/high_level_architecture.mmd` - first-read system architecture.
- `architecture/pipeline_execution.md` - detailed pipeline execution notes and
  Mermaid source.
- `guides/greek_only_runner.md` - Greek-only CLI and output contract.
- `design/data_ops_architecture.md` - institutional data-ops target design.

## Folder Structure

```text
docs/
├── architecture/              # high-level and execution architecture diagrams
│   └── sections/              # paper-friendly architecture figures
├── design/                    # design notes, audits, leakage, CDC, data-ops
├── guides/                    # user-facing operational guides
├── reference/                 # schema and domain reference diagrams
├── workflows/                 # workflow-specific diagram packs
├── reports/                   # validation and implementation reports
├── archive/                   # historical design inputs kept for traceability
└── assets/                    # generated or exported images
```

## Keep, Archive, Delete Policy

- Keep current operating docs in `architecture/`, `guides/`, `design/`, and
  `reference/`.
- Keep paper or slide diagram packs in `architecture/sections/` and
  `workflows/`.
- Keep historical implementation notes in `reports/`.
- Move superseded blueprints to `archive/` instead of mixing them with current
  docs.
- Delete only generated or superseded artifacts that have no references and no
  current source-of-truth role.

## Cleanup Performed

- Moved the v1.4 blueprint source and HTML export to
  `archive/blueprints/`.
- Deleted `quant_pipeline_blueprint_v1_3.html`; it was an old generated
  blueprint export superseded by v1.4 and had no active repo references.
