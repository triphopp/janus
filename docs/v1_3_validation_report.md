# Quant Pipeline v1.3 Validation Report

Updated: 2026-06-15

## Scope

This report validates the implementation created from the v1.3 blueprint:

- ingestion contract, schema, settlement parser, symbology
- core validators, DTE, pricing, Greeks, splitter, metrics, regime labels, overfitting, audit
- adapter `prepare()` contracts
- v1.3 hard gates from the blueprint test section

v1.4 modules (`versioned_cache`, `txcost`, `attribution`) are tested separately, but this report focuses on confirming the v1.3 baseline.

## Test Coverage Added

- `tests/test_core/test_validators.py`
  - logical bounds
  - missing completeness
  - point-in-time outlier capping
- `tests/test_core/test_audit.py`
  - deterministic schema/data hashes
  - JSONL snapshot writing
  - stage diff row/schema/NaN detection
  - audit determinism across reruns
- `tests/test_core/test_overfitting.py`
  - DSR multiple-trial penalty
  - insufficient-data behavior
  - PBO bounded output
  - minimum track record edge cases
- `tests/test_core/test_regime.py`
  - configured-axis regime label composition
  - transition matrix probability rows
- `tests/test_core/test_stability.py`
  - variance ratio
  - Jarque-Bera
  - distribution shift
  - information coefficient
  - VIF/condition number
- Expanded `tests/test_adapters/test_contract.py`
  - futures adapter
  - equity options adapter
  - futures options adapter
- Expanded `tests/test_core/test_pricing.py`
  - Black-76 golden fixture is now actually used

## Bugs Found And Fixed

- `core/metrics.py`
  - Fixed `drawdown_metrics()` call from invalid `cum.expanding().min_periods(1).max()` to `cum.expanding(min_periods=1).max()`.
  - Added near-zero volatility guard so constant returns do not produce huge Sharpe from floating-point noise.
- `tests/test_core/test_greeks.py`
  - Replaced an incorrect Black-76 vs BS delta-ratio assertion with direct Black-76 closed-form validation.
- `tests/golden/black76_reference.csv`
  - Existing values were inconsistent with Black-76 analytic equations. Replaced with high-precision analytic reference values.
- `ingestion/settlement_loader.py`
  - Replaced deprecated `pd.Timestamp.utcnow()` with `pd.Timestamp.now("UTC")`.

## Verification

Command:

```powershell
$env:PYTHONPATH='D:\Agents\Codex\janus\.codex_pydeps;D:\Agents\Codex\janus'
& 'C:\Users\markereversey\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m pytest -q --basetemp .pytest_tmp
```

Result:

```text
86 passed in 1.97s
```

## Remaining Caveats

- Tests validate synthetic and committed fixture data. They do not certify production data vendor behavior.
- HMM/GMM offline validators depend on optional ML packages not listed in the base v1.3 test run.
- Full end-to-end pipeline with live/downloaded provider data is not covered here; unit and contract tests now cover the v1.3 code paths.
