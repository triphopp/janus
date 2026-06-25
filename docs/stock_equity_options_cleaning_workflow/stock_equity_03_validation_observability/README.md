# Stock and Equity Options Layer 03: Validation and Observability

This folder separates run-level validators, stock return-outlier policy,
data-quality scorecard, CDC, break ledger, manifest, and prepared-data artifacts.

- Use `../bounded_context_glossary.md` or `../bounded_context_glossary_th.md` before
  changing figure labels or paper wording.
- `slides/stock_equity_validation_observability_slides.mmd` is the wider slide version.
- `two_col/stock_equity_validation_observability_2col.mmd` is the compact paper version.

Checked against `run_pipeline.py`, `core/validators.py`, `core/data_quality.py`,
`core/cdc.py`, `core/breaks.py`, `core/manifest.py`, and `core/options_quality.py`.

Export examples:

```bash
mmdc -i docs/stock_equity_options_cleaning_workflow/stock_equity_03_validation_observability/slides/stock_equity_validation_observability_slides.mmd -o docs/images/stock_equity_validation_observability_slides.svg -b white
mmdc -i docs/stock_equity_options_cleaning_workflow/stock_equity_03_validation_observability/two_col/stock_equity_validation_observability_2col.mmd -o docs/images/stock_equity_validation_observability_2col.pdf -b white
```
