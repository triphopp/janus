# Quant Pipeline v1.4 Implementation Status

Updated: 2026-06-15

## Implemented in this pass

- Added `ingestion/versioned_cache.py`
  - immutable `raw/<symbol>/ingested_at=YYYY-MM-DD/` partitions
  - `_versions.jsonl` manifest
  - `data_version` reads: `latest`, explicit date, `as_of_backtest_start`
  - `infer_available_at()`, `add_availability_columns()`, and PIT-safe `pit_join()`
- Updated ingestion contract
  - `RAW_SCHEMA` now includes `available_at` and `ingested_at`
  - settlement and equity POC loaders now add availability columns when provider data lacks them
- Added `core/txcost.py`
  - Level 1 fixed cost
  - Level 2 DTE/moneyness/vol-regime spread scaling
  - Level 3 simple market-impact add-on
  - aggregate `total()` and `financing_cost()` helpers
- Added `core/attribution.py`
  - unified `waterfall()` API
  - options Greek decomposition
  - equity factor decomposition
  - futures spot/basis/roll decomposition
  - residual, cost, financing, and net P&L
- Updated instrument configs
  - `data_version`
  - `available_at_lag`
  - `txcost`
- Added v1.4 test skeletons
  - `tests/test_ingestion/test_versioned_cache.py`
  - `tests/test_core/test_txcost.py`
  - `tests/test_core/test_attribution.py`
- Wired optional attribution output in `run_pipeline.py`
  - writes `outputs/attribution/<run_id>_waterfall.csv` when P&L columns are present
- Added dependency fallbacks
  - `core/audit.py` falls back from `xxhash` to `hashlib.sha256`
  - `core/pricing.py` and `core/greeks.py` no longer require SciPy for normal CDF/PDF or IV root solving
  - `ingestion/equity_loader_a.py` imports `yfinance` lazily

## Still not done

- `run_pipeline.py` does not yet use `VersionedCache`; it still calls providers directly.
- Attribution reports require trade-level P&L columns; the pipeline does not yet generate those columns itself.
- `available_at` is not yet applied to event calendar CSV ingestion; events still need release-time normalization before joins.
- Transaction cost model is not calibrated from real bid-ask/volume data.
- Level 3 market impact is a simple placeholder formula, not a calibrated Almgren-Chriss model.
- Equity factor attribution needs PIT factor returns/loadings data before it can be trusted.
- Versioned raw storage has no actual raw provider data committed yet.
- No CI config file exists yet.
- Tests cannot run in the current bundled runtime until test dependencies are installed.

## Current runtime dependency gaps

The bundled Python in this Codex thread has `pandas` and `numpy`, but is missing:

- `pytest`
- `PyYAML`
- `xxhash`
- `scipy`
- `statsmodels`
- `pyarrow` or `fastparquet`
- `yfinance`

Some fallbacks were added, but full pipeline/test execution still needs a project environment with these installed.

## Recommended next work

1. Add a project dependency file (`requirements.txt` or `pyproject.toml`) and install test/runtime packages.
2. Wire `VersionedCache` into ingestion in `run_pipeline.py`.
3. Normalize event calendars with `available_at` and force all joins through `pit_join()`.
4. Generate trade-level P&L columns so optional attribution output has real inputs.
5. Add real bid-ask fixtures and calibrate txcost Level 2.
6. Add a small committed raw fixture under `tests/fixtures/` so v1.4 tests run without external data.
