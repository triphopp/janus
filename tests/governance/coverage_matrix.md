# Coverage Matrix

| Area | Requirement | Test File | Status | Notes |
|---|---|---|---|---|
| Ingestion | Parse real settlement row | `tests/test_ingestion/test_symbology.py` | Covered | Real pipe-delimited row fixture |
| Ingestion | Symbology uniqueness | `tests/test_ingestion/test_symbology.py` | Covered | Catches ambiguous product maps |
| Ingestion | Symbology round-trip | `tests/test_ingestion/test_symbology.py` | Covered | `resolve()` then `reverse()` |
| Ingestion | No orphan product IDs | `tests/test_ingestion/test_symbology.py` | Covered | Flags unmapped rows |
| Ingestion | Schema violation raises | Not explicit | Gap | Add direct `validate_schema()` missing-column test |
| Core validators | Logical bounds | `tests/test_core/test_validators.py` | Covered | price, volume, IV, strike |
| Core validators | Missing completeness | `tests/test_core/test_validators.py` | Covered | date gaps and low OI |
| Core validators | PIT outlier capping | `tests/test_core/test_validators.py` | Covered | Expanding-window behavior |
| DTE | Calendar/trading basis | `tests/test_dte/test_calendar.py` | Covered | Includes edge cases |
| DTE | Post-expiry NaN | `tests/test_dte/test_calendar.py` | Covered | Explicit edge case |
| Pricing | Black-76 formula | `tests/test_core/test_pricing.py` | Covered | Includes golden file |
| Pricing | BS-Merton parity | `tests/test_core/test_pricing.py` | Covered | Put-call parity |
| Pricing | IV round-trip | `tests/test_core/test_pricing.py` | Covered | Solver sanity |
| Greeks | Closed-form identities | `tests/test_core/test_greeks.py` | Covered | Delta/gamma/vega/theta |
| Greeks | Bump-vs-analytic | `tests/test_core/test_greeks.py` | Covered | finite-diff tolerance |
| Greeks | Calendar net Greeks | `tests/test_core/test_greeks.py` | Covered | vega buckets and term risk |
| Stability | Feature/stability primitives | `tests/test_core/test_stability.py` | Covered | VR, JB, shift, IC, VIF |
| Splitter | No look-ahead | `tests/test_core/test_splitter.py` | Covered | val after train |
| Splitter | Purge gap | `tests/test_core/test_splitter.py` | Covered | purge separation |
| Splitter | Diversity gate | `tests/test_core/test_splitter.py` | Covered | unseen regime fails |
| Metrics | Numeric stability | `tests/test_core/test_metrics.py` | Covered | constant return, drawdown |
| Metrics | Per-fold breakdown | `tests/test_core/test_metrics.py` | Covered | structure and stability score |
| Overfitting | DSR/PBO/MTRL | `tests/test_core/test_overfitting.py` | Covered | trial penalty and bounds |
| Regime | Rule-based labels | `tests/test_core/test_regime.py` | Covered | configured axes |
| Regime | Transition matrix | `tests/test_core/test_regime.py` | Covered | probability rows |
| Audit | Snapshot + diff | `tests/test_core/test_audit.py` | Covered | JSONL + delta checks |
| Audit | Deterministic hashes | `tests/test_core/test_audit.py` | Covered | rerun hash equality |
| Adapters | Equity contract | `tests/test_adapters/test_contract.py` | Covered | `(df, cfg)` shape |
| Adapters | Futures contract | `tests/test_adapters/test_contract.py` | Covered | term structure columns |
| Adapters | Equity options contract | `tests/test_adapters/test_contract.py` | Covered | IV + Greeks columns |
| Adapters | Futures options contract | `tests/test_adapters/test_contract.py` | Covered | futures + options fields |
| Architecture | No hard-coded instrument names | Manual command | Covered | `rg -n -i "wti|eia|ovx" core adapters` |
| End-to-end | Full pipeline run on fixture | Not covered | Gap | Needs committed small fixture and CLI test |
