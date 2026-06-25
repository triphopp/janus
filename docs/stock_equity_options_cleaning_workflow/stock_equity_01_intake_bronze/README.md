# Stock and Equity Options Layer 01: Intake and Bronze

This folder separates stock/equity-option source reads, fixed-input guards, bronze
contracts, quarantine, coverage, and family-schema handoff.

- Use `../bounded_context_glossary.md` or `../bounded_context_glossary_th.md` before
  changing figure labels or paper wording.
- `slides/stock_equity_intake_bronze_slides.mmd` is the wider slide version.
- `two_col/stock_equity_intake_bronze_2col.mmd` is the compact paper version.

Checked against `run_pipeline.py`, `ingestion/equity_loader_a.py`,
`ingestion/equity_options_loader_yf.py`, `contracts/equity_price.v1.yaml`, and
`contracts/equity_options.v1.yaml`.

Export examples:

```bash
mmdc -i docs/stock_equity_options_cleaning_workflow/stock_equity_01_intake_bronze/slides/stock_equity_intake_bronze_slides.mmd -o docs/images/stock_equity_intake_bronze_slides.svg -b white
mmdc -i docs/stock_equity_options_cleaning_workflow/stock_equity_01_intake_bronze/two_col/stock_equity_intake_bronze_2col.mmd -o docs/images/stock_equity_intake_bronze_2col.pdf -b white
```
