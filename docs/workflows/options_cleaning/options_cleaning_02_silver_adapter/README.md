# Options Cleaning Layer 02: Silver Adapter

This folder separates the silver adapter cleaning and standardization workflow for
`equity_options` and `futures_options`.

- Use `../bounded_context_glossary.md` or `../bounded_context_glossary_th.md` as the
  shared terminology reference before changing figure labels or paper wording.
- `slides/silver_adapter_workflow_slides.mmd` is the wider version for presentation slides.
- `two_col/silver_adapter_workflow_2col.mmd` is the compact version for a single column in a 2-column paper.

Checked against `adapters/equity_options_adapter.py`,
`adapters/futures_options_adapter.py`, `adapters/options_base.py`,
`core/dte.py`, `core/pricing.py`, and `core/greeks.py`.

Suggested caption:

The silver layer normalizes option-chain columns, routes equity options to BS-Merton
inputs and futures options to Black-76 inputs, then applies shared option transforms for
returns, DTE, IV, Greeks, VRP, skew, PCP diagnostics, universe exclusions, and silver
quality flags before emitting the prepared DataFrame and `core_cfg`.

Export examples:

```bash
mmdc -i docs/workflows/options_cleaning/options_cleaning_02_silver_adapter/slides/silver_adapter_workflow_slides.mmd -o docs/assets/images/silver_adapter_workflow_slides.svg -b white
mmdc -i docs/workflows/options_cleaning/options_cleaning_02_silver_adapter/two_col/silver_adapter_workflow_2col.mmd -o docs/assets/images/silver_adapter_workflow_2col.pdf -b white
```
