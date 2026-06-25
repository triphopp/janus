# Options Cleaning Layer 03: Validation and Observability

This folder separates run-level validation, data-quality enforcement, CDC,
break-ledger, manifest, and reproducible output artifacts.

- Use `../bounded_context_glossary.md` or `../bounded_context_glossary_th.md` as the
  shared terminology reference before changing figure labels or paper wording.
- `slides/validation_observability_workflow_slides.mmd` is the wider version for presentation slides.
- `two_col/validation_observability_workflow_2col.mmd` is the compact version for a single column in a 2-column paper.

Checked against `run_pipeline.py`, `core/validators.py`, `core/data_quality.py`,
`core/cdc.py`, `core/breaks.py`, `core/manifest.py`, `core/options_quality.py`,
and `core/quarantine.py`.

Suggested caption:

After adapter preparation, Janus applies return-outlier policy hooks, stage-1 validators,
and a run-level data-quality scorecard. It then diffs ingestion, adapter, return-clip,
and validator stages, routes unexplained mutations into the break ledger, records a
content-pinned manifest, and writes prepared data plus summary, quarantine, option-quality,
diff, and break artifacts.

Export examples:

```bash
mmdc -i docs/options_cleaning_workflow/options_cleaning_03_validation_observability/slides/validation_observability_workflow_slides.mmd -o docs/images/validation_observability_workflow_slides.svg -b white
mmdc -i docs/options_cleaning_workflow/options_cleaning_03_validation_observability/two_col/validation_observability_workflow_2col.mmd -o docs/images/validation_observability_workflow_2col.pdf -b white
```
