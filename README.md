# Janus - Quant Pipeline Framework

Janus is a research-grade market data pipeline for quantitative validation. It
ingests provider data, applies point-in-time data guards, validates contract and
coverage quality, runs diagnostics, writes reproducible run artifacts, and
serves a live React dashboard over those outputs.

It is built to answer: "Can this data and run lineage be trusted?" It does not
pick stocks, generate trading signals, or execute trades.

## What Janus Does

```text
Raw provider data
  -> bronze contract gate
  -> adapters
  -> validators and CDC
  -> break ledger
  -> stability diagnostics
  -> walk-forward CV
  -> metrics and DSR
  -> manifests, reports, dashboard
```

Primary surfaces:

- `run_pipeline.py` - CLI entry point for pipeline runs.
- `run_dashboard.py` - FastAPI server for the React dashboard.
- `web/frontend/` - React + Vite dashboard source.
- `outputs/runs/` - run-scoped summaries, reports, prepared data, and tables.
- `outputs/breaks/` and `outputs/ledger/` - global audit and data-change records.
- `Memory/` - durable agent memory, findings, plans, and current project state.

## Quick Start

Install Python dependencies:

```powershell
pip install -r requirements.txt
```

Run an exploratory equity diagnostic:

```powershell
python run_pipeline.py -i TSLA --start 2020-01-01 --end 2024-12-31 --allow-unversioned-data
```

Run a settlement-backed instrument:

```powershell
python run_pipeline.py -i wti --data-file "D:\data\WTI.csv" --provider settlement --start 2023-01-01 --end 2024-12-31 --allow-unversioned-data
```

Start the dashboard as the main UI:

```powershell
cd web/frontend
npm install
npm run build
cd ../..
python run_dashboard.py
```

Then open:

```text
http://127.0.0.1:8800/
```

For frontend development, keep the backend running on port `8800`, then run:

```powershell
cd web/frontend
npm run dev
```

Open the Vite dev server:

```text
http://127.0.0.1:5173/
```

Vite proxies `/api`, `/diff`, `/report`, and `/healthz` to the FastAPI server.

## Pipeline CLI

`run_pipeline.py` uses two separate inputs:

- `-i` / `--instrument` is the instrument name, config name, or equity ticker.
- `--data-file` is a local settlement file path on disk.

Examples:

```powershell
# Equity ticker direct from provider
python run_pipeline.py -i NVDA --start 2024-01-01 --end 2024-12-31 --allow-unversioned-data

# Instrument config with local settlement file
python run_pipeline.py -i wti --data-file "D:\Agents\Codex\janus\Memory\plans\Data\WTI.csv" --provider settlement --start 2024-09-25 --end 2026-05-29 --allow-unversioned-data
```

Do not pass a CSV path to `-i`. If you do, Janus treats the path as a ticker
symbol and the equity loader will fail while looking up that "ticker".

## Dashboard

The old inline HTML dashboard has been removed. The supported dashboard is now:

- React + TypeScript frontend in `web/frontend/`
- FastAPI JSON API in `web/dashboard.py`
- single-server production path via `python run_dashboard.py`
- optional Vite dev path via `npm run dev`

Main routes:

| Route | Purpose |
| --- | --- |
| `/` | React app served from `web/frontend/dist` |
| `/api/runs` | Live scan of run outputs |
| `/api/runs/{run_id}` | One run, breaks, warnings, and change samples |
| `/api/runs/{run_id}/raw-row` | Raw-source row lookup for modal drilldowns |
| `/api/breaks` | Break ledger, with status/severity/run filters |
| `/api/trend` | Break trend summary |
| `/api/compare?a=&b=` | Prepared-data diff between two runs |
| `/diff/{run_id}` | Existing self-contained stage diff HTML |
| `/report/{run_id}` | Existing static final report HTML |
| `/healthz` | Health check |

If `web/frontend/dist/index.html` is missing, `/` returns a `503` page with the
build command. Build the frontend once and restart or refresh the server.

## Outputs

Each run writes to:

```text
outputs/runs/<run_id>__<instrument>__<family>__<start>_to_<end>/
```

Typical run contents:

```text
summary.json
report/final_report.html
report/summary_report.md
tables/per_fold.csv
tables/per_regime.csv
tables/diversity.csv
data/prepared.csv
data/prepared.parquet
attribution/waterfall.csv
```

Global audit outputs:

```text
outputs/ledger/       # CDC cell-level diffs
outputs/breaks/       # lifecycle-tracked break ledgers
outputs/quarantine/   # bronze contract rejects
outputs/manifest/     # content-pinned reproducibility manifests
```

## Price and PIT Semantics

yfinance `Close` with `auto_adjust=False` is split-adjusted, not
dividend-adjusted. The equity loader exposes these fields separately:

| Column | Meaning | Use for |
| --- | --- | --- |
| `raw_close` | Split-adjusted provider close | Returns without fake split-day jumps |
| `raw_close_unadj` | Reconstructed traded price level | Price levels and thresholds |
| `adj_factor` | Dividend adjustment factor | Dividend yield research |
| `split_factor` | Retroactive split multiplier | Audit and diagnostics |

Pipeline stages use `price_std`, which defaults to `raw_close`.

Every row carries:

```text
as_of_date <= available_at <= decision_time
```

The PIT timing guard fails rows that violate this ordering. Dividend adjustment
is opt-in through `allow_retro_adjusted_prices: true` because Yahoo's adjustment
factor is retroactive and introduces look-ahead bias for strict PIT backtests.

## Guard Status

Each `summary.json` includes a `guard_status` block:

| Guard | Pass means |
| --- | --- |
| `pit_timing` | `as_of_date <= available_at <= decision_time` for every row |
| `contract_gate` | raw data passes bronze schema and semantic checks |
| `coverage_sla` | enough expected trading days are present |
| `split_adjustments` | split adjustment behavior is explicit and surfaced |
| `price_adjustments` | dividend adjustment behavior is explicit and surfaced |
| `cache_version_fixed` | raw data version is pinned |
| `strategy_pnl_present` | strategy return column is available for strategy metrics |

## Project Structure

```text
janus/
├── run_pipeline.py              # pipeline CLI
├── run_dashboard.py             # dashboard server on :8800 by default
├── requirements.txt             # Python dependencies
├── configs/                     # defaults and instrument configs
├── ingestion/                   # provider loaders and versioned cache
├── adapters/                    # asset-family normalization
├── core/                        # contracts, guards, CDC, metrics, reports
├── web/
│   ├── dashboard.py             # FastAPI routes and React app serving
│   ├── scanner.py               # output scanning and dashboard data assembly
│   └── frontend/                # React + Vite dashboard
├── tests/                       # pytest suite
├── outputs/                     # generated run artifacts
└── Memory/                      # agent memory, findings, plans, runbooks
```

## Adding an Instrument

For exploratory equities, no YAML is required:

```powershell
python run_pipeline.py -i KO --start 2020-01-01 --end 2024-12-31 --allow-unversioned-data
```

For custom equity settings, create `configs/instruments/mystock.yaml`:

```yaml
family: equity
provider: yfinance
symbol:
  ticker: KO
allow_retro_adjusted_prices: false
cv:
  n_folds: 6
```

For settlement data, keep the instrument name in `-i` and pass the file with
`--data-file`:

```powershell
python run_pipeline.py -i myfuture --data-file "D:\data\settlement.csv" --provider settlement --start 2024-01-01 --end 2024-12-31 --allow-unversioned-data
```

Supported families:

- `equity`
- `futures`
- `equity_options`
- `futures_options`

## How Futures Options and Equity Options Differ

Janus treats these as two separate families with different loaders, contracts,
and adapters. The distinction is set by the `family` key in the instrument YAML.

| | `futures_options` | `equity_options` |
| --- | --- | --- |
| **Loader** | `SettlementLoader` (pipe-delimited EOD file) | `EquityOptionsLoaderYF` (yfinance API) |
| **Underlying** | Front-month futures price (Black-76) | Spot equity price (BS-Merton) |
| **Pricing model** | Black-76 — futures as underlying, no carry | BS-Merton — spot + dividend yield |
| **DTE basis** | Calendar days, act/365 | Trading days, bus/252 |
| **History** | Full historical settlement file from exchange | Snapshot only — yfinance has no option history |
| **Contract schema** | `settlement_options.v1.yaml` | `equity_options.v1.yaml` |
| **Regime axes** | vol_regime, term_structure, vrp_sign, skew_direction, eia_week | vol_regime |
| **Extra context** | Roll schedule, term structure slope, event calendars (EIA, FOMC) | Dividend yield, split adjustment |

### How the Settlement Loader Separates Rows

A single settlement file contains both futures and options rows mixed together.
`SettlementLoader` disambiguates each row using two columns:

```text
is_option = (CONTRACT TYPE in ["C", "P"]) AND (STRIKE is not null)
is_future = everything else
```

Both conditions must hold. A row with `CONTRACT TYPE = C` but a blank strike is
treated as a future. A row with a strike value but no valid right (`C`/`P`) is
also treated as a future.

The loader writes an `instrument_type` column (`"option"` or `"future"`) on
every row. `FuturesOptionsAdapter` consumes the mixed frame — it builds the
front-month continuous price from futures rows first, then PIT-safe-joins that
price onto the options rows as the underlying `F` before running Black-76.
If an options row has no matching futures row for its delivery month and
as-of-date, the adapter raises immediately rather than silently using a stale
or mismatched underlying.

### Implied Volatility Source

`futures_options` defaults to `iv_source: provided` — IV comes from the
`OPTION_VOLATILITY` column in the exchange settlement file (exchange-computed,
validated against a configurable threshold). `equity_options` re-solves IV
from mid bid-ask via the BS-Merton model when `iv_provided` is absent.

### When to Use Which

Use `futures_options` when you have an exchange settlement file (WTI, Brent,
natural gas, etc.) and want full historical analysis with roll-adjusted
underlying prices and event-tagged regimes.

Use `equity_options` for a current-state snapshot of an equity option chain
(single as-of-date). yfinance cannot provide historical chains — the coverage
and sample gates will flag the run as not backtestable, which is the correct
behavior. Swap the provider for a vendor feed (ORATS, OptionMetrics) for
historical equity option analysis.

## Settlement CSV Format

`SettlementLoader` expects pipe-delimited EOD data. Required columns include:

| Column | Description |
| --- | --- |
| `TRADE DATE` | `MM/DD/YYYY` |
| `CONTRACT` | contract root |
| `CONTRACT TYPE` | `C`/`P` for options; blank or `F` for futures |
| `STRIP` | delivery month |
| `STRIKE` | strike; blank for futures |
| `SETTLEMENT PRICE` | EOD settlement |
| `EXPIRATION DATE` | contract expiry |
| `PRODUCT_ID` | numeric product id |
| `OPTION_VOLATILITY` | implied volatility, if available |
| `DELTA_FACTOR` | delta, if available |

Extra columns are ignored.

## Tests

Run all tests:

```powershell
python -m pytest
```

Run a focused subset:

```powershell
python -m pytest tests/test_core/
python -m pytest tests/test_web/test_scanner.py
python -m pytest -k "outlier" -v
```

On this Windows workspace, pytest cache or temp cleanup can hit access-denied
warnings. For focused web tests, this local-safe form avoids the temp/cache path:

```powershell
New-Item -ItemType Directory -Force -Path _codex_tmp | Out-Null
$env:TMP=(Resolve-Path _codex_tmp).Path
$env:TEMP=$env:TMP
python -m pytest tests\test_web\test_scanner.py --basetemp _codex_tmp\pytest -o cache_dir=_codex_tmp\.pytest_cache
```

## Future Features

### Asset Context Panel

Planned: a per-run summary block showing basic asset facts over the analysis
window — dividend count and total amount, split events with ex-dates and ratios,
data gaps, and coverage ratio. The goal is to give a quick cross-check layer:
if the break ledger tags an anomaly on a specific date, a reader can verify
immediately whether a dividend or split event explains it without opening
prepared.csv manually.

Information already computed during the run (dividend days, total_dividend,
split events, coverage ratio) would be surfaced together in `summary.json` and
the dashboard run-detail card. No additional provider calls required.

### Options Data Cleaning and Protection

Planned: a dedicated data quality layer for options families (`equity_options`,
`futures_options`). Options data has failure modes that price-bar logic does
not cover:

- **Stale IV** — implied volatility can go stale at zero volume; stale IV rows
  should be quarantined at the bronze gate, not carried silently into metrics.
- **Moneyness filters** — deep in-the-money or far out-of-the-money contracts
  have unreliable IV; a configurable moneyness band should drop fringe strikes
  before adapter normalization.
- **Expiry proximity** — near-expiry options have mechanical IV blowups; a
  minimum DTE floor (e.g. 5 days) should be enforced as a contract rule.
- **Put-call parity check** — for the same strike and expiry, the call and put
  prices should satisfy put-call parity within a configurable tolerance; large
  deviations indicate a data error and should emit a break-ledger entry.
- **Snapshot-vs-history guard** — yfinance provides only same-day option chains,
  not historical snapshots. Any options run from yfinance should be explicitly
  tagged `backtest_grade: false` and the coverage gate should fail it as
  non-backtestable. Historical options require a vendor feed
  (ORATS, OptionMetrics, exchange settlement).

Implementation is not started. The contract schema (`contracts/equity_options.v1.yaml`)
exists as a starting point; the cleaning rules above would be enforced there and
in a new `adapters/options_cleaner.py` stage wired before the main adapter.

## Design Rules

- Keep asset-specific behavior in `adapters/`; `core/` should stay generic.
- Use split-adjusted `raw_close` for returns; use `raw_close_unadj` for price levels.
- Keep PIT semantics explicit and fail closed when timing is unsafe.
- Require pinned raw data for backtest-grade runs; use `--allow-unversioned-data` for exploration only.
- Attribute every material data change through CDC and break ledgers.
- Keep walk-forward CV grouped by `as_of_date` to avoid same-date leakage.
- Keep the React dashboard under `web/frontend`; there is no separate `webapp` app.

