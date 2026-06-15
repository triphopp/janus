# v1.3 Test Scope

Goal: confirm the v1.3 implementation is correct enough to use as the baseline before v1.4 work.

## In Scope

- Ingestion contract
  - `RAW_SCHEMA`
  - settlement row parsing
  - schema violation detection
  - symbology uniqueness, round-trip, no-orphan checks
  - PIT/survivorship behavior through fixtures
- Core stage 1
  - logical bounds
  - missing completeness
  - PIT-safe outlier capping
- Core stage 2
  - stationarity/stability primitives
  - variance ratio
  - distribution shift
  - feature-quality tests such as IC and VIF
- Core stage 3
  - walk-forward split
  - purge and embargo
  - KL/JS regime diversity gate
- Core stage 4
  - return/risk/drawdown/tail/hit metrics
  - per-fold and per-regime breakdown
  - overfitting controls: DSR, PBO, minimum track record length
- Options math
  - Black-76
  - BS-Merton
  - IV solver round-trip
  - closed-form Greeks
  - bump-vs-analytic sanity checks
  - net Greeks for calendar spreads
- Adapter contracts
  - equity
  - futures
  - equity options
  - futures options
- Audit
  - schema hash
  - data hash
  - stage diff
  - deterministic rerun hash

## Out Of Scope For v1.3 Unit Tests

- Live vendor/API behavior
- Full production data quality certification
- Broker/execution integration
- Real bid-ask/liquidity calibration
- HMM/GMM runtime validation unless optional ML dependencies are added
- v1.4 raw versioning, txcost, attribution, except where separately tracked

## Pass Criteria

- `pytest` suite passes with no failing tests
- Black-76 golden file is used by tests
- adapter contract tests cover all adapter families
- audit determinism test passes
- no `wti|eia|ovx` hard-coded in `core/` or `adapters/`
