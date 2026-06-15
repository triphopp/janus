---
name: quant-pipeline-summary
description: Quant Pipeline Framework v1.4 — architecture, key decisions, and agent work guide
metadata:
  type: project
---

# Quant Pipeline Framework v1.4

## What

Multi-asset systematic strategy validation pipeline. 5-layer architecture: ingestion → adapters → core → outputs. Single core shared across equity, futures, and options via adapter pattern (~100% core reuse, ~65% options reuse).

## Architecture (5 layers)

1. **ingestion/** — fetch + cache external data → raw frame (RAW_SCHEMA). Provider-agnostic.
2. **adapters/** — asset-aware preparation. Instrument family only. No business logic.
3. **core/** — asset-agnostic processing. Receives `DataFrame + cfg dict` only. 4 stages.
4. **outputs/** — clean_data, stability_report, fold_manifest, perf_report, audit.
5. **configs/** — all thresholds, instrument names, symbology rules live here.

## Iron Rule (never break)

**`core/` must contain ZERO instrument names or `if asset == ...` statements.**
All column references go through `cfg[...]` keys.
Instrument names (B, North Sea, EIA, OVX) exist ONLY in `configs/`.

Verify: `grep -ri "wti\|eia\|ovx" core/ adapters/` → must find nothing in `.py` files.

## Key Files

- `run_pipeline.py` — entry point (argparse, load config, run full pipeline)
- `ingestion/base.py` — ProviderBase ABC + RAW_SCHEMA
- `ingestion/settlement_loader.py` — pipe-delimited EOD parser
- `ingestion/symbology.py` — PRODUCT_ID/HUB/CONTRACT → internal symbol
- `core/pricing.py` — Black-76, BS-Merton, IV solver (Brent root-find)
- `core/greeks.py` — closed-form Greeks + net Greeks for spreads
- `core/dte.py` — DTE single source of truth (calendar/trading convention)
- `core/audit.py` — lightweight before/after snapshot per stage
- `core/txcost.py` — v1.4 transaction cost model (fixed/scaled/impact)
- `core/attribution.py` — v1.4 performance attribution waterfall
- `ingestion/versioned_cache.py` — v1.4 immutable raw cache + available_at PIT joins
- `core/splitter.py` — walk-forward + purge/embargo + diversity gate (KL+JS)
- `adapters/options_base.py` — ~65% shared options logic
- `adapters/futures_options_adapter.py` — Black-76, roll, term structure, events
- `configs/instruments/bz.yaml` — Brent instrument spec (example of config-driven)

## v1.3 Additions

- Greeks & spread math (Black-76 vs BS-Merton, net greeks for calendar, IV solver)
- DTE module (calendar convention, single source of truth)
- Tests & data audit as hard requirements (symbology, DTE, pricing, schema drift)
- Audit module (lightweight snapshot, not full observability)
- Vendor-agnostic provider language
- Symbology tests (uniqueness + round-trip + no-orphan)
- KL + JS divergence gate

## v1.4 Additions

- Raw data versioning with immutable `ingested_at` partitions
- `available_at`/`ingested_at` ingestion contract updates
- PIT-safe `pit_join()` for external data joins
- Transaction cost model: fixed, bid-ask scaling, market impact
- Attribution waterfall: Greek decomposition for options, factor decomposition for equity
- Status tracker at `docs/implementation_status_v1_4.md`

## Build Order (5 phases)

- **Phase 0**: Scaffold + contracts (~2 hrs)
- **Phase 1**: Ingestion layer + symbology tests (~1 week)
- **Phase 2**: Core + DTE + equity adapter end-to-end (~1-2 weeks)
- **Phase 3**: Futures adapter + Greeks/Pricing (~1-2 weeks)
- **Phase 4**: Options adapter — most complex (~1-2 weeks)

## For New Agents

1. Read this file first.
2. Read `INDEX.md` for file-by-file descriptions.
3. The blueprint is at `docs/quant_pipeline_blueprint_v1_4.html` — full spec.
4. All code is in `quant_pipeline/`.
5. Config drives everything — check `configs/` before touching code.
6. Tests are hard requirements — symbology, DTE, pricing must pass before merge.
7. Never put instrument names in `core/` or `adapters/`.

**Why:** This project was built from a detailed HTML blueprint (v1.3) specifying every function signature, build order, and acceptance criteria. The architecture prevents common quant pipeline failures: survivorship bias, look-ahead leakage, PIT violations, and silent symbology/DTE corruption.

**How to apply:** Agents working on this project should: (1) understand the 5-layer architecture before touching files, (2) never break the iron rule (no instrument names in core/adapters), (3) always add corresponding tests when adding functionality, (4) use `configs/` for all thresholds and instrument-specific values.
