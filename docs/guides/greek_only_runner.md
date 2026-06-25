# Greek-Only Runner

`run_greeks.py` computes option Greeks without running the full Janus pipeline.
It shares the same `core.greeks.batch_greeks()` engine as `run_pipeline.py`,
so full-pipeline and Greek-only results are numerically identical for the same inputs.

Diagrams: [fig5](../architecture/sections/fig5_greek_only_workflow.mmd) ·
[fig5a](../architecture/sections/fig5a_greek_engine.mmd) ·
[fig5b](../architecture/sections/fig5b_greek_only_flow.mmd)

---

## When to use each mode

| Mode | Use when |
|------|----------|
| `run_pipeline.py` | Full backtest: need splitter, metrics, reports, dashboard |
| `run_greeks.py` | Need Greeks only: IV surface QA, position monitoring, quick sensitivity check |

---

## Modes

### Mode A — Prepared rows (CSV or Parquet)

Input file already has pricing columns. Fastest path; no ingestion or adapter.

```bash
python run_greeks.py \
  --input wti_options.csv \
  --model black76 \
  --backend numpy \
  --output outputs/greeks/wti_greeks.parquet
```

**Required input columns** (at least one per group):

| Group | Accepted columns |
|-------|-----------------|
| Underlying | `underlying_price`, `S`, `F`, `price_std` (first non-null wins) |
| Strike | `K`, `strike` |
| IV | `iv` (computed mode), `iv_provided` then `iv` (provided mode) |
| T | `T` (years), or `as_of_date` + `expiry` → DTE computed automatically |
| Rate | `r` (row-level), or `--rf-rate` flag |
| Right | `right` (`C`/`P`), or `option_type` (`CALL`/`PUT`) |

### Mode B — Instrument config + raw chain

Runs ingestion → adapter prep (DTE, IV, underlying mapping, universe filters) → Greeks.
Skips validators, splitter, metrics, reporting, CDC, dashboard.

```bash
python run_greeks.py \
  --instrument bz \
  --data-file data/WTI_settlement.csv \
  --start 2024-01-01 \
  --end 2024-12-31 \
  --min-dte 1 \
  --max-dte 90 \
  --output outputs/greeks/bz_greeks.parquet
```

---

## Model selection

| Flag | Model | Use for |
|------|-------|---------|
| `--model black76` | Black-76 | Futures options (Brent, WTI, grains) |
| `--model bsm` | Black-Scholes-Merton | Equity options (AAPL, SPX) |

---

## Backends

| Flag | Backend | When to use |
|------|---------|-------------|
| `--backend numpy` | CPU vectorized (default) | All production use, ~40–50× faster than loop |
| `--backend loop` | Scalar per-row | Debugging only |
| `--backend auto` | numpy unless GPU threshold met | Automatic selection |
| `--backend cuda` | GPU via CuPy | Very large chains, requires `cupy-cuda12x` |

---

## Universe filters

Applied before Greek computation. Rows dropped by filters are counted in the summary.

```bash
--min-dte 1          # Minimum DTE in calendar days
--max-dte 90         # Maximum DTE in calendar days
--min-option-price 0.01   # Minimum option price
--max-iv 2.0         # Maximum implied volatility
```

---

## Output

### Greek output file (`.csv` or `.parquet`)

| Column | Description |
|--------|-------------|
| `delta` | Option delta (Black-76: `e^(-rT)·N(d₁)` for call) |
| `gamma` | Option gamma |
| `vega` | Option vega (**per 1.0 vol unit**, not per 1%) |
| `theta` | Option theta (**annualized calendar-time decay**, `-dV/dT`) |
| `rho` | Option rho |
| `greek_model` | Model used (`black76` / `bsm`) |
| `greek_backend` | Backend used (`numpy` / `loop` / `cuda`) |
| `greek_input_valid` | `True` if all required inputs were present and valid |

Identity columns (`as_of_date`, `expiry`, `right`, `K`/`strike`, etc.) are preserved from input.

Invalid rows (missing underlying, IV, T, or bad right) receive `NaN` for all Greek columns
and `greek_input_valid = False`. The runner **never raises** on individual bad rows.

### Summary file (`.greek_summary.json`)

Written beside the output file. Contains:

```json
{
  "model": "black76",
  "backend": "numpy",
  "dtype": "float64",
  "universe_filter": { "input_rows": 1000, "rows_after_filter": 850, "rows_dropped": 150 },
  "input_quality": {
    "total_rows": 850,
    "valid_rows": 847,
    "invalid_rows": 3,
    "invalid_by_reason": { "missing_underlying": 1, "missing_iv": 2, "missing_or_expired_T": 0, "bad_right": 0 }
  },
  "output_rows": 847,
  "conventions": {
    "theta": "annualized calendar-time decay, -dV/dT",
    "vega": "per 1.0 vol unit",
    "rate": "continuously compounded"
  },
  "config_warnings": [],
  "provenance": {
    "input_file": "wti_options.csv",
    "input_hash": "a3f9b1c2d4e5f678",
    "git_commit": "35ff84d"
  }
}
```

---

## Convention reference

### Theta

Theta is **annualized** calendar-time decay: `−dV/dT` where `T` is in years.

To convert to per-day: `theta_per_day = theta / 365`.

Black-76 call theta:
```
θ = −e^(−rT) · F · φ(d₁) · σ / (2√T)
    − r · K · e^(−rT) · N(d₂)
    + r · F · e^(−rT) · N(d₁)
```

### Vega

Vega is **per 1.0 volatility unit** (not per 1% move).

To convert to per-1% move: `vega_per_pct = vega / 100`.

### Rate

Rates are **continuously compounded**. Match against continuously compounded risk-free rates,
not simple or annually compounded rates.

### Black-76 vs BSM

| | Black-76 | BSM |
|--|----------|-----|
| Underlying | Futures price `F` | Spot price `S` |
| Delta call | `e^(−rT)·N(d₁)` | `N(d₁)` |
| d₁ | `[ln(F/K) + σ²T/2] / (σ√T)` | `[ln(S/K) + (r−q+σ²/2)T] / (σ√T)` |
| Rho identity | `ρ = −T·price` | Standard BSM rho |

---

## Examples

### WTI futures options (Black-76)

```bash
python run_greeks.py \
  --instrument bz \
  --data-file data/WTI_2024.csv \
  --start 2024-01-01 --end 2024-12-31 \
  --min-dte 1 --max-dte 90 \
  --output outputs/greeks/wti_2024.parquet
```

### AAPL equity options (BSM)

```bash
python run_greeks.py \
  --input aapl_options.csv \
  --model bsm \
  --rf-rate 0.05 \
  --min-dte 7 --max-dte 60 \
  --output outputs/greeks/aapl_greeks.csv
```

### Prepared CSV with explicit rate

```bash
python run_greeks.py \
  --input prepared_options.csv \
  --model black76 \
  --rf-rate 0.045 \
  --output greeks.csv
```

### Prepared Parquet (requires `pyarrow`)

```bash
python run_greeks.py \
  --input prepared_options.parquet \
  --model black76 \
  --backend numpy \
  --output outputs/greeks/out.parquet
```

---

## Architecture

```
Full pipeline:    run_pipeline.py → OptionsBase.compute_greeks() ──┐
                                                                    ├→ core.greeks.batch_greeks()
Greek-only mode:  run_greeks.py   → run_greek_only()    ──────────┘
                                  → core.greek_inputs.resolve_greek_inputs()
```

`core.greeks.batch_greeks()` is the shared engine — one source of truth for formulas.
`core.greek_inputs.resolve_greek_inputs()` is the shared input contract resolver.

---

## External reference validation

Reference values from `tests/fixtures/greek_reference.json` are generated by
`tools/generate_greek_reference.py` using **scipy.stats.norm** — no shared code
with `core/greeks.py`. Tests in `tests/test_core/test_greek_external_reference.py`
verify both scalar and batch paths against this independent reference.

To regenerate after a formula change:

```bash
python tools/generate_greek_reference.py
python3 -m pytest tests/test_core/test_greek_external_reference.py -v
```
