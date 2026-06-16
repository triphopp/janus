# Janus — Quant Pipeline Framework

Research-grade pipeline for validating quantitative strategies across equity and futures options.
Covers data ingestion → adapter → validators → stability → cross-validation → metrics → HTML report.

---

## Requirements

- Python 3.10+
- Install dependencies:

```bash
pip install -r requirements.txt
```

All commands must be run from the project root (same directory as `run_pipeline.py`).

---

## Running the pipeline

### Equity (AAPL, SPX)

Data is downloaded automatically from yfinance — no files needed.

```bash
python3 run_pipeline.py --instrument aapl --start 2023-01-01 --end 2024-12-31
python3 run_pipeline.py --instrument spx  --start 2023-01-01 --end 2024-12-31
```

### Futures Options (WTI, BZ)

Requires a pipe-delimited EOD settlement CSV from the exchange. Set the path once in the instrument config:

```yaml
# configs/instruments/wti.yaml
data_file: "/path/to/your/WTI.csv"
```

Then run:

```bash
python3 run_pipeline.py --instrument wti --start 2024-01-01 --end 2024-12-31
python3 run_pipeline.py --instrument bz  --start 2024-01-01 --end 2024-12-31
```

> **Date range tip:** futures options need enough data for cross-validation folds.
> Aim for at least 12 months. Shorter ranges (< 6 months) may produce 0 folds due to
> the `max_dte` purge window.

### Custom run ID

```bash
python3 run_pipeline.py --instrument aapl --start 2023-01-01 --end 2024-12-31 --run-id my_run_01
```

---

## Outputs

All outputs land in `outputs/` and are named by run ID.

| Path | Content |
|------|---------|
| `outputs/<run_id>_stability.html` | Interactive HTML report — open in any browser |
| `outputs/<run_id>_summary.json` | Machine-readable summary with all stage results |
| `outputs/perf_report/<run_id>_per_fold.csv` | Sharpe, Sortino, drawdown per CV fold |
| `outputs/perf_report/<run_id>_per_regime.csv` | Performance broken down by regime label |
| `outputs/fold_manifest/<run_id>_diversity.csv` | Fold diversity gate pass/fail |
| `outputs/attribution/<run_id>_waterfall.csv` | P&L attribution waterfall (if PnL columns present) |

The HTML report path is printed at the end of every run:

```
Done. Run ID: 20240101_120000
  HTML report: outputs/20240101_120000_stability.html
```

Open it directly in a browser — no server needed.

---

## Adding a new instrument

1. Create `configs/instruments/<name>.yaml` — set `family`, `provider`, `symbol`, and any overrides.
2. For equity: set `provider: yfinance` and `symbol.ticker`.
3. For futures/options: set `provider: settlement` and `data_file` pointing to your CSV.
4. Run with `--instrument <name>`.

Supported families: `equity`, `futures`, `equity_options`, `futures_options`.

Family-level defaults live in `configs/equity.yaml` and `configs/futures.yaml` — instrument configs override them.

---

## Pipeline stages

```
Ingestion → Adapter → Stage 1: Validators → Stage 2: Stability
         → Stage 3: Splitter (CV folds) → Stage 4: Metrics / Overfitting
         → Outputs (HTML report, JSON summary, CSVs)
```

| Stage | Module | What it does |
|-------|--------|-------------|
| Ingestion | `ingestion/` | Load raw data from yfinance or settlement CSV |
| Adapter | `adapters/` | Normalize, compute returns, IV, Greeks, PCP |
| Validators | `core/validators.py` | Bound checks, completeness, outlier capping |
| Stability | `core/stability.py` | ADF/KPSS, Hurst, ARCH, PSI, variance ratio |
| Splitter | `core/splitter.py` | Walk-forward CV split by date group, purge + embargo |
| Metrics | `core/metrics.py` | Per-fold Sharpe, Sortino, drawdown, stability score |
| Overfitting | `core/overfitting.py` | Deflated Sharpe Ratio |
| Reporting | `core/reporting.py` | HTML report + JSON summary |

---

## Project structure

```
janus/
├── run_pipeline.py          # Entry point
├── configs/
│   ├── equity.yaml          # Family defaults for equity
│   ├── futures.yaml         # Family defaults for futures/options
│   └── instruments/         # Per-instrument overrides (aapl, spx, wti, bz)
├── adapters/
│   ├── base.py              # AdapterBase — config normalization
│   ├── equity_adapter.py
│   ├── futures_adapter.py
│   ├── options_base.py      # Shared IV / Greeks / PCP / VRP logic
│   ├── equity_options_adapter.py
│   └── futures_options_adapter.py
├── core/
│   ├── config.py            # normalize_config() — flattens nested YAML keys
│   ├── validators.py        # Stage 1
│   ├── stability.py         # Stage 2
│   ├── splitter.py          # Stage 3 — walk-forward CV, purge, regime diversity
│   ├── metrics.py           # Stage 4 — performance metrics
│   ├── overfitting.py       # Deflated Sharpe Ratio
│   ├── pricing.py           # IV solver (Black-76, BSM)
│   ├── greeks.py            # Delta, gamma, vega, theta, rho
│   ├── regime.py            # Regime label assignment
│   └── reporting.py         # HTML + JSON output
├── ingestion/
│   ├── settlement_loader.py # Pipe-delimited EOD CSV loader
│   ├── equity_loader_a.py   # yfinance loader with PIT provenance
│   └── versioned_cache.py   # Raw data versioning primitives
├── outputs/                 # Generated on first run
└── tests/                   # 117 tests
```

---

## Running tests

```bash
python3 -m pytest
```

Useful flags:

```bash
python3 -m pytest -v                          # verbose — show each test name
python3 -m pytest tests/test_core/            # single directory
python3 -m pytest -k "outlier"               # tests matching a keyword
python3 -m pytest -x                          # stop on first failure
```

---

## Settlement CSV format

The `SettlementLoader` expects a pipe-delimited file with these columns (extra columns ignored):

| Column | Description |
|--------|-------------|
| `TRADE DATE` | Trade date (MM/DD/YYYY) |
| `HUB` | Delivery hub |
| `CONTRACT` | Contract root (e.g. T, B) |
| `CONTRACT TYPE` | C / P = option; blank / F = future |
| `STRIP` | Delivery month |
| `STRIKE` | Strike price (blank for futures) |
| `SETTLEMENT PRICE` | EOD settlement price |
| `EXPIRATION DATE` | Contract expiry |
| `PRODUCT_ID` | Numeric product ID |
| `OPTION_VOLATILITY` | Implied volatility (if available) |
| `DELTA_FACTOR` | Delta (if available) |

---

## Key design rules

- **`core/` contains zero instrument names.** All asset-specific logic lives in `adapters/`.
- **Point-in-time safety.** Adapter outputs carry `available_at` timestamps. No future data leaks into the training window.
- **Date-group CV.** Walk-forward splits on unique `as_of_date` values — all rows for the same decision date stay together in one split, preventing chain-row leakage.
- **Config normalization.** Nested YAML keys (`pricing.model`, `cv.n_folds`) are flattened by `core/config.py` so all downstream code sees flat keys.
