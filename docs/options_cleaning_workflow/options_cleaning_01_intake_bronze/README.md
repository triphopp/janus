# Options Cleaning Layer 01: Intake and Bronze

This folder separates the input, fixed-version guard, bronze contract, quarantine,
coverage SLA, and family-schema handoff from the full options cleaning workflow.

- Use `../bounded_context_glossary.md` or `../bounded_context_glossary_th.md` as the
  shared terminology reference before changing figure labels or paper wording.
- `slides/intake_bronze_workflow_slides.mmd` is the wider version for presentation slides.
- `two_col/intake_bronze_workflow_2col.mmd` is the compact version for a single column in a 2-column paper.

Checked against `run_pipeline.py`, `core/contracts.py`,
`contracts/equity_options.v1.yaml`, `contracts/settlement_options.v1.yaml`,
`core/coverage.py`, and the provider loaders.

Suggested caption:

Bronze intake first pins the raw input version, reads an equity or settlement option source,
then applies structural, semantic, point-in-time, symbology, and frame-level contract checks.
Failed rows go to quarantine with reason counts; passed rows continue through coverage and
family-schema gates before silver adapter processing.

Export examples:

```bash
mmdc -i docs/options_cleaning_workflow/options_cleaning_01_intake_bronze/slides/intake_bronze_workflow_slides.mmd -o docs/images/intake_bronze_workflow_slides.svg -b white
mmdc -i docs/options_cleaning_workflow/options_cleaning_01_intake_bronze/two_col/intake_bronze_workflow_2col.mmd -o docs/images/intake_bronze_workflow_2col.pdf -b white
```
