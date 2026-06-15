# Test Run Log

| Date | Scope | Command | Result | Notes |
|---|---|---|---|---|
| 2026-06-15 | v1.3 baseline + existing v1.4 unit tests | `pytest -q --basetemp .pytest_tmp` | `86 passed in 1.97s` | Ran with workspace-local `.codex_pydeps` |

## Issues Found During Validation

- `core/metrics.py` drawdown used invalid pandas API.
- `core/metrics.py` Sharpe needed near-zero volatility guard.
- Black-76 golden CSV had inconsistent reference values.
- One Greeks test used an oversimplified Black-76/BS delta-ratio assertion.
- `pd.Timestamp.utcnow()` warning fixed in settlement parser.
