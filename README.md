# Janus — Quant Pipeline Framework

Research-grade data pipeline for quantitative strategy validation across equities and futures options.

**What it does:** raw provider data → bronze contract gate → validators → stability tests → walk-forward CV → metrics/DSR → audit trail + HTML report.

**What it does NOT do:** pick stocks, generate signals, execute trades.

---

## Quick start

```bash
pip install -r requirements.txt

# Exploratory provider fetch — no YAML needed
python run_pipeline.py -i TSLA --start 2020-01-01 --end 2024-12-31 --allow-unversioned-data

# Futures options (needs settlement CSV — see below)
python run_pipeline.py -i wti --start 2023-01-01 --end 2024-12-31 --allow-unversioned-data

# Live dashboard (reads outputs/ — refresh after a run)
python run_dashboard.py  # → http://127.0.0.1:8800
```

Output printed at the end of every run:

```
Done. Run ID: 20240101_120000
  HTML report: outputs/runs/20240101_120000__TSLA__equity__2020-01-01_to_2024-12-31/report/final_report.html
```

Open the HTML path in any browser — no server needed.

---

## Price semantics (important)

yfinance `Close` with `auto_adjust=False` is **split-adjusted, not dividend-adjusted** (verified 1.3.0). The loader exposes this as three separate columns:

| Column | Meaning | Use for |
|--------|---------|---------|
| `raw_close` | Split-adjusted close (provider default) | Returns — no fake split-day jump |
| `raw_close_unadj` | True traded price (raw_close × cumulative future splits) | Price levels, thresholds |
| `adj_factor` | Adj Close / Close = **dividend factor only** | Dividend yield research |
| `split_factor` | Retroactive split multiplier baked in by provider | Audit / de-adjustment |

`price_std` (what pipeline stages use) defaults to `raw_close`.

To include dividend adjustment: set `allow_retro_adjusted_prices: true` in your instrument YAML. This introduces look-ahead bias (Yahoo dividend factor is retroactive) — acceptable for research, wrong for strict PIT backtest.

---

## PIT (point-in-time) model

Every row carries three timestamps:

```
as_of_date ≤ available_at ≤ decision_time
```

| Timestamp | Meaning |
|-----------|---------|
| `as_of_date` | Trading day the bar describes |
| `available_at` | Earliest moment the data was knowable (close + 3h for equity) |
| `decision_time` | Earliest moment a strategy could act (= available_at by default; strategies overwrite) |

The PIT timing guard runs at the end of each pipeline run and fails if any row violates this ordering.

**Splits are flagged separately** — `split_factor` != 1.0 means the provider retroactively divided this row's `raw_close` by a future split. The `split_adjustments` guard in `summary.json` surfaces this as a warning with event count and factor range.

---

## Outputs

Each run writes to `outputs/runs/<run_id>__<instrument>__<family>__<start>_to_<end>/`:

```
summary.json               ← full machine-readable results + all guard statuses
report/final_report.html   ← HTML report, open in browser
report/summary_report.md
tables/per_fold.csv        ← Sharpe, Sortino, drawdown per CV fold
tables/per_regime.csv      ← performance by regime label
tables/diversity.csv       ← fold diversity gate pass/fail
data/prepared.csv          ← final DataFrame after all stages
data/prepared.parquet
attribution/waterfall.csv  ← P&L waterfall (if pnl columns present)
```

Global audit outputs (across runs):

```
outputs/ledger/            ← CDC change records (cell-level diffs)
outputs/breaks/            ← break ledger (high-severity: fail, medium: warn)
outputs/quarantine/        ← rows rejected by the bronze contract gate
outputs/manifest/          ← content-pinned run manifests (reproducibility)
```

---

## Guard status in summary.json

Every run reports a `guard_status` block:

| Guard | Pass means |
|-------|-----------|
| `pit_timing` | as_of ≤ available ≤ decision on every row |
| `contract_gate` | raw data passes bronze schema + semantic rules |
| `coverage_sla` | ≥ configured % of expected trading days present |
| `split_adjustments` | no retroactive split factor baked in (or explicitly warned) |
| `price_adjustments` | dividend adj factor behavior |
| `cache_version_fixed` | versioned cache used with a pinned data_version |
| `strategy_pnl_present` | strategy return column found (DSR computable) |

---

## Pipeline stages

```
Ingestion → Bronze gate → Adapter → Stage 1: Validators → CDC + Breaks
         → Stage 2: Stability → Stage 3: Splitter (CV) → Stage 4: Metrics
         → Manifest → Outputs
```

| Stage | Module | What it does |
|-------|--------|-------------|
| Ingestion | `ingestion/` | Fetch from yfinance or settlement CSV; add PIT timestamps |
| Bronze gate | `core/contracts.py` | Validate schema + semantics; quarantine bad rows |
| Coverage SLA | `core/coverage.py` | Check trading-day completeness vs requested window |
| Adapter | `adapters/` | Normalize to standard columns; compute returns, IV, Greeks |
| Validators | `core/validators.py` | Bound checks, completeness, MAD outlier capping |
| CDC | `core/cdc.py` | Cell-level diff adapter→validators; attribute every change |
| Breaks | `core/breaks.py` | Lifecycle-tracked break ledger (high/medium severity) |
| Stability | `core/stability.py` | ADF/KPSS, Hurst, ARCH, PSI, variance ratio, Ljung-Box |
| Splitter | `core/splitter.py` | Walk-forward CV by date-group; purge + embargo; regime diversity gate |
| Metrics | `core/metrics.py` | Per-fold Sharpe, Sortino, drawdown, stability score |
| Overfitting | `core/overfitting.py` | Deflated Sharpe Ratio |
| Manifest | `core/manifest.py` | Content-pinned reproducibility record (input hash + config) |
| Reporting | `core/reporting.py` | HTML report + JSON summary + CSVs |

---

## Adding an instrument

**Equity (simplest):** no YAML needed — pass any ticker directly for exploratory diagnostics:

```bash
python run_pipeline.py -i KO --start 2020-01-01 --end 2024-12-31 --allow-unversioned-data
```

Backtest-grade runs require `versioned_cache.read: true` with a fixed `data_version`
such as an explicit partition date or `as_of_backtest_start`.

**Equity with custom settings:** create `configs/instruments/mystock.yaml`:

```yaml
family: equity
provider: yfinance
symbol:
  ticker: KO
allow_retro_adjusted_prices: false  # true = include dividends (look-ahead)
cv:
  n_folds: 6
```

**Futures options:** create `configs/instruments/myfuture.yaml` with `data_file` pointing to your settlement CSV, then run with `--instrument myfuture`.

Supported families: `equity`, `futures`, `equity_options`, `futures_options`.

---

## Settlement CSV format

The `SettlementLoader` expects pipe-delimited EOD data. Required columns:

| Column | Description |
|--------|-------------|
| `TRADE DATE` | MM/DD/YYYY |
| `CONTRACT` | Contract root |
| `CONTRACT TYPE` | C/P = option; blank/F = future |
| `STRIP` | Delivery month |
| `STRIKE` | Strike (blank for futures) |
| `SETTLEMENT PRICE` | EOD settlement |
| `EXPIRATION DATE` | Contract expiry |
| `PRODUCT_ID` | Numeric ID |
| `OPTION_VOLATILITY` | IV (if available) |
| `DELTA_FACTOR` | Delta (if available) |

Extra columns are ignored.

---

## Project structure

```
janus/
├── run_pipeline.py          ← entry point
├── run_dashboard.py         ← live dashboard at :8800
├── requirements.txt         ← pinned deps (yfinance==1.3.0)
├── configs/
│   ├── equity.yaml          ← family defaults
│   ├── futures.yaml
│   └── instruments/         ← per-instrument overrides
├── ingestion/
│   ├── equity_loader_a.py   ← yfinance; splits + dividends exposed separately
│   ├── settlement_loader.py ← pipe-delimited EOD CSV
│   └── versioned_cache.py   ← immutable raw versioning + PIT join utilities
├── adapters/
│   ├── equity_adapter.py    ← price_std, returns, PIT MAD clip
│   ├── futures_adapter.py
│   ├── options_base.py      ← shared IV / Greeks / PCP / VRP
│   ├── equity_options_adapter.py
│   └── futures_options_adapter.py
├── core/
│   ├── contracts.py         ← bronze data contracts + quarantine
│   ├── coverage.py          ← trading-day SLA gate
│   ├── cdc.py               ← cell-level change data capture
│   ├── breaks.py            ← break ledger
│   ├── causal.py            ← PIT timing guard + causal transforms
│   ├── validators.py        ← stage 1
│   ├── stability.py         ← stage 2
│   ├── splitter.py          ← stage 3 (walk-forward CV, purge, embargo)
│   ├── metrics.py           ← stage 4 (Sharpe, Sortino, drawdown)
│   ├── overfitting.py       ← deflated Sharpe ratio
│   ├── manifest.py          ← reproducibility manifest
│   ├── audit.py             ← snapshot + content hash
│   ├── regime.py            ← regime label assignment
│   ├── lineage.py           ← feature lookback graph
│   └── reporting.py         ← HTML report + JSON summary
├── outputs/                 ← generated on first run
├── tests/                   ← 180 passing tests
└── Memory/                  ← agent memory + findings
```

---

## Tests

```bash
python -m pytest                    # all tests
python -m pytest tests/test_core/   # single directory
python -m pytest -k "outlier" -v    # keyword filter
python -m pytest -x                 # stop on first failure
```

180 tests pass. 22 errors are pre-existing Windows `tmp_path` permission issues in pytest's cleanup — test logic is unaffected.

---

## Design rules

- **`core/` has zero instrument names.** Asset-specific logic lives in `adapters/`.
- **Returns use split-adjusted Close.** No fake split-day return jumps. For price levels, use `raw_close_unadj`.
- **PIT by default.** Dividend adjustment is opt-in (`allow_retro_adjusted_prices: true`) because Yahoo's factor is retroactive.
- **Pinned raw data by default.** Direct provider fetch is blocked unless `--allow-unversioned-data` or `require_fixed_data_version: false` is set for exploration.
- **Every change attributed.** CDC diffs every cell adapter→validators; unattributed changes become high-severity breaks.
- **Date-group CV.** Walk-forward splits on unique `as_of_date` — all rows for the same date stay together, preventing chain-row leakage.
- **Deps pinned.** `yfinance==1.3.0` — provider semantics changed between 0.2.x and 1.x. Re-test `ingestion/equity_loader_a.py` before bumping.
