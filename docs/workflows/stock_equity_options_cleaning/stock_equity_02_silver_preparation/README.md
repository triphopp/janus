# Stock and Equity Options Layer 02: Silver Preparation

This folder separates stock price preparation from equity-option preparation while
keeping both in the same equity-family workflow.

- Use `../bounded_context_glossary.md` or `../bounded_context_glossary_th.md` before
  changing figure labels or paper wording.
- `slides/stock_equity_silver_preparation_slides.mmd` is the wider slide version.
- `two_col/stock_equity_silver_preparation_2col.mmd` is the compact paper version.

Checked against `adapters/equity_adapter.py`, `adapters/equity_options_adapter.py`,
`adapters/options_base.py`, `core/dte.py`, `core/pricing.py`, and `core/greeks.py`.

Export examples:

```bash
mmdc -i docs/workflows/stock_equity_options_cleaning/stock_equity_02_silver_preparation/slides/stock_equity_silver_preparation_slides.mmd -o docs/assets/images/stock_equity_silver_preparation_slides.svg -b white
mmdc -i docs/workflows/stock_equity_options_cleaning/stock_equity_02_silver_preparation/two_col/stock_equity_silver_preparation_2col.mmd -o docs/assets/images/stock_equity_silver_preparation_2col.pdf -b white
```
