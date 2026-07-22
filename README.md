# Janus

Janus is a research-grade market-data validation pipeline. It ingests provider
or settlement data, applies point-in-time and contract guards, prepares
asset-aware features, builds purged walk-forward folds, writes reproducible run
artifacts, and serves those artifacts through a React/FastAPI dashboard.

Janus is built to answer one question:

```text
Can this data, validation path, pricing model, and run lineage be trusted?
```

It does not pick instruments, generate production trading signals, or execute
orders. When strategy P&L columns are absent, Janus reports market diagnostics
rather than strategy backtest approval.

## Current Status

Working paths:

- Progressive `janus` CLI: import an external file once, then run many times by
  symbol and date window.
- Equity, futures, equity-options, and futures-options preparation through
  family-specific adapters and a shared generic core.
- Settlement-backed WTI futures-options runs with fixed-input guards.
- Contract/quarantine, coverage, CDC, break ledger, reports, dashboard scan,
  and option-quality summaries.
- Greek-only computation through `run_greeks.py`.
- Central risk-free-rate stamping through `core/rates.py`.
- Central pricing-model metadata through `core/pricing_models.py`.

Current verified test result:

```bash
pytest -q
```

```text
1055 passed, 10 skipped, 16 warnings
```

## What Changed Recently

### Rates and discounting

Rates now have one policy surface:

- `core/rates.py` resolves and stamps row-level `r`.
- Existing row-level `r` wins.
- Configured scalar rates and PIT rate series can stamp missing `r`.
- The built-in fallback is `0.05`, continuously compounded, ACT/365-style.
- `resolve_greek_inputs()` no longer invents rates itself; it expects row-level
  `r` already stamped by `core.rates`.
- `validate_provided_iv()`, option export, adapter Greeks, `net_greeks()`, and
  transaction-cost financing now use the same rate contract.

Practical rule: if you call low-level Greek input resolution directly, stamp
rates first with `core.rates.stamp_rate()`.

### Pricing models

Pricing model names and metadata now live in `core/pricing_models.py`.

Implemented runtime models:

| Model | Runtime | Use |
| --- | --- | --- |
| `black76` | Black-76 | European futures options |
| `black76_european` | Alias of `black76` | Explicit European futures option label |
| `bs` | Black-Scholes | Legacy equity option path |
| `bsm` | Black-Scholes-Merton | Equity/index options with dividend yield |
| `bachelier` / `normal` | Normal futures model | Negative/near-zero futures; absolute-price volatility |
| `black76_shifted` | Shifted Black-76 | Negative/near-zero futures with an explicit shift |
| `black76_baw` | Barone-Adesi-Whaley | American futures options |
| `black76_shifted_baw` | Shifted BAW | Experimental shifted American futures options |
| `bsm_baw` | Barone-Adesi-Whaley | American equity options |
| `crr_binomial` | CRR binomial tree | Slower American/European reference engine |

Registered but not runtime-enabled yet:

- `trinomial`
- `finite_difference`

Lognormal scalar paths now fail closed without NumPy runtime warnings. For
all implemented models, invalid domains such as
non-positive underlying, non-positive strike, non-positive volatility, missing
rate, expired `T`, or invalid right return `NaN` rather than silently running
`log()` on bad inputs.

Put-call parity checks now use registry metadata. European models keep parity
equality checks; American models use individual premium bounds instead.

## Pipeline Shape

```text
source data
  -> ingestion
  -> adapter preparation
  -> contracts, PIT, coverage, validators
  -> option/rate/pricing quality checks
  -> splitter, stability, metrics
  -> reports, run packages, dashboard artifacts
```

Greek-only mode uses the same core math but skips splitter, metrics, reports,
CDC, and dashboard generation.

```text
prepared option rows
  -> core.rates.stamp_rate()
  -> core.greek_inputs.resolve_greek_inputs()
  -> core.greeks.batch_greeks()
  -> Greek output + .greek_summary.json
```

## Install

```bash
pip install -r requirements.txt
```

Optional progress bars use `tqdm` when installed:

```bash
pip install tqdm
```

Run tests:

```bash
pytest -q
```

If your Python installation does not expose `pytest` directly:

```bash
python3 -m pytest -q
```

## Quick Start: `janus`

The user-facing CLI is `janus.py`. The normal pattern is import once, run many
times.

### Settlement-backed WTI

```bash
python3 janus.py import WTI data/WTI.csv
python3 janus.py run WTI --window 2024Q4
```

Custom date range:

```bash
python3 janus.py run WTI --from 2024-09-25 --to 2024-12-31
```

Export-oriented run:

```bash
python3 janus.py run WTI --window 2024Q4 --preset export --universe near-term
```

### Equity diagnostics

Live provider reads are diagnostic rather than reproducible:

```bash
python3 janus.py run NVDA --from 2024-01-01 --to 2024-12-31 --preset diagnostic
```

### Inspect before running

```bash
python3 janus.py doctor WTI
python3 janus.py explain WTI --window 2024Q4
python3 janus.py list
python3 janus.py show wti_q4
python3 janus.py data list WTI
```

## Greek-only Mode

Use `run_greeks.py` when you already have prepared option rows or want a fast
Greek artifact without the full pipeline.

Futures options:

```bash
python3 run_greeks.py \
  --input wti_options.csv \
  --model black76 \
  --backend numpy \
  --output outputs/greeks/wti_greeks.parquet
```

Explicit European futures label:

```bash
python3 run_greeks.py \
  --input wti_options.csv \
  --model black76_european \
  --output outputs/greeks/wti_greeks.parquet
```

Equity options:

```bash
python3 run_greeks.py \
  --input aapl_options.csv \
  --model bsm \
  --div-yield 0.005 \
  --rf-rate 0.05 \
  --output outputs/greeks/aapl_greeks.parquet
```

Instrument-config mode:

```bash
python3 run_greeks.py \
  --instrument wti \
  --data-file data/WTI_2024.csv \
  --start 2024-01-01 \
  --end 2024-12-31 \
  --min-dte 1 \
  --max-dte 90 \
  --output outputs/greeks/wti_2024.parquet
```

Supported model flags today:

```text
black76
black76_european
bs
bsm
```

Supported backends:

| Backend | Notes |
| --- | --- |
| `numpy` | CPU vectorized default |
| `loop` | Scalar per-row, mainly for debugging |
| `auto` | Chooses numpy unless CUDA threshold is met |
| `cuda` | GPU via CuPy; requires a matching CuPy install |

Greek output includes:

- Identity columns from the input.
- `delta`, `gamma`, `vega`, `theta`, `rho`.
- `greek_model`, `greek_backend`, `greek_dtype`.
- `greek_input_valid`, `greek_invalid_reason`.
- A sibling `.greek_summary.json` with input-quality, universe-filter, rate,
  convention, and provenance metadata.

## Rate Policy

Common config keys:

```yaml
rf_rate: 0.05
rf_rate_source: constant
rf_rate_col: r
```

PIT rate-series config can provide row-available rates. The rate table must be
available before the option row decision time; future rates are not used.

Minimal conceptual shape:

```yaml
rf_rate_source: sofr
rate_data_path: /absolute/path/to/sofr_rates.csv
```

Expected rate table fields are normalized by `core.rates`, including rate/date
and `available_at` when present. ACT/360 simple SOFR-style rates can be
converted to continuously compounded ACT/365 through the rate utilities.

## Pricing Model Policy

Janus does not auto-switch pricing models row by row. A run has one canonical
pricing model. `--compare-model` writes separate row-level comparison artifacts
without replacing canonical output.

Important consequences:

- Negative futures prices can be real data.
- Lognormal models such as `black76` cannot price `F <= 0`.
- Bad model domain is a pricing-domain state, not necessarily bad raw data.
- Bachelier/normal uses absolute-price volatility, while Black-family models use
  fractional volatility; Janus blocks provided IV when those units disagree.
- Shifted Black requires an explicit `pricing_shift`.
- BAW is the fast American approximation and emits a review warning beyond one
  year; CRR is the slower reference engine.

## Low-level Full Pipeline

`janus.py` is the preferred facade. `run_pipeline.py` remains the low-level
entry for advanced combinations.

```bash
python3 run_pipeline.py \
  -i wti \
  --start 2024-10-01 \
  --end 2024-12-31 \
  --run-id wti_q4
```

Advanced option universe and Greek controls:

```bash
python3 run_pipeline.py \
  -i wti \
  --start 2024-09-25 \
  --end 2024-12-31 \
  --max-dte 90 \
  --min-option-price 0.00001 \
  --iv-cap 2.0 \
  --min-abs-delta 0.15 \
  --max-abs-delta 0.80 \
  --compute-greeks
```

## Outputs

Run-scoped outputs:

```text
outputs/runs/<instrument>/<run_id>/
  summary.json
  report/final_report.html
  report/summary_report.md
  tables/per_fold.csv
  tables/per_regime.csv
  tables/diversity.csv
  data/prepared.csv
  data/prepared.parquet
```

Cross-run artifacts:

```text
outputs/diff/<run_id>_changes.jsonl
outputs/diff/<run_id>_diff.html
outputs/breaks/<run_id>.jsonl
outputs/manifest/<run_id>.json
quarantine/<run_id>/
```

Greek-only artifacts:

```text
outputs/greeks/<name>.parquet
outputs/greeks/<name>.greek_summary.json
```

Downstream option-chain export artifacts include a manifest, schema, data
dictionary, and clean `option_chain_greeks.csv` when readiness permits export.

## Dashboard

Build the frontend:

```bash
cd web/frontend
npm install
npm run build
cd ../..
```

Start the FastAPI dashboard:

```bash
python3 run_dashboard.py
```

Open:

```text
http://127.0.0.1:8800/
```

Frontend development:

```bash
cd web/frontend
npm run dev
```

Vite serves `http://127.0.0.1:5173/` and proxies API calls to the FastAPI
server.

## Project Layout

```text
janus/
|-- janus.py
|-- run_pipeline.py
|-- run_greeks.py
|-- run_dashboard.py
|-- cli/
|-- configs/
|-- contracts/
|-- ingestion/
|-- adapters/
|   |-- options_base.py
|   |-- futures_options_adapter.py
|   `-- equity_options_adapter.py
|-- core/
|   |-- rates.py
|   |-- pricing_models.py
|   |-- pricing.py
|   |-- greeks.py
|   |-- greek_inputs.py
|   |-- dte.py
|   `-- options_quality.py
|-- docs/
|-- issues/
|-- memory/
|-- tests/
|-- tools/
|-- web/
|-- outputs/
|-- quarantine/
`-- data/
```

## Adding Instruments

Supported families:

- `equity`
- `futures`
- `equity_options`
- `futures_options`

Minimum settlement-backed futures-options config shape:

```yaml
family: futures_options
provider: settlement
symbol:
  product_id: 425
  contract_root: T
  hub: WTI
data_file: /absolute/path/to/WTI.csv
data_version: sha256:<file-sha256>
data_file_sha256: <file-sha256>
pricing:
  model: auto
iv_source: provided
```

Local absolute paths should stay in ignored local config, not committed shared
config.

## Design Rules

- Keep asset-specific behavior in `adapters/`; keep `core/` generic.
- Keep real instrument names in configs, not adapter/core code.
- Compute DTE through `core/dte.py`.
- Stamp rates through `core/rates.py`.
- Resolve model identity through `core/pricing_models.py`.
- Compute Greeks through `core/greeks.batch_greeks()`.
- Resolve low-level Greek inputs through `core/greek_inputs.resolve_greek_inputs()`.
- Keep PIT timing explicit and fail closed when unsafe.
- Require fixed raw inputs for backtest-grade runs.
- Treat CDC `UNATTRIBUTED` mutations as bugs until explained.
- Group walk-forward folds by `as_of_date`.
- Use `--allow-unversioned-data` only for exploration.

## More Documentation

- `docs/README.md` - documentation map.
- `docs/guides/` - operating guides.
- `docs/architecture/` - architecture diagrams and sections.
- `issues/P1-high/rates-discounting/` - rate and discounting work.
- `issues/P1-high/pricing-models/` - pricing-model rollout plan.
- `memory/README.md` - durable project memory guide.
