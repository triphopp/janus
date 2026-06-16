# Janus — Data Operations Architecture (Institutional Grade)

> Scope: target architecture for change-tracking, validation, and lineage at the level a
> hedge-fund / prop-desk **data operations team** runs. Design only — no implementation.
> Supersedes the lightweight `data_diff_design.md`; the diff engine from that doc survives
> as §6 (Change Data Capture), now embedded in a full ops framework.
> Companions: `audit_findings_pre_data_structure.md`, `leakage_guard_design.md`.
> Date: 2026-06-16

---

## 0. What "institutional grade" changes

The POC question was *"how do I see what changed in cleaned data?"* A data-ops desk asks a
bigger question: *"can I prove every number a strategy traded on was correct, knowable at
the time, reproducible, and that any deviation raised a tracked break with an owner?"*

That reframes the design around **six invariants** every data-ops desk enforces:

| # | Invariant | Janus today | This design |
|---|-----------|-------------|-------------|
| I1 | **Immutable raw** — provider data never overwritten | `VersionedCache` exists, unused | promote to bitemporal append-only store (§3) |
| I2 | **Contract-validated** — data meets a versioned schema+semantic contract before use | `validate_schema` (columns only) | Data Contracts (§2) |
| I3 | **Point-in-time** — only knowable data feeds a decision | `available_at` partial | bitemporal, audited (§4) |
| I4 | **Lineage** — every cell traceable to source + transform | none | column-level lineage graph (§5) |
| I5 | **Break-managed** — every out-of-tolerance change is a tracked break with an owner + SLA | none | break lifecycle (§7) |
| I6 | **Reproducible** — a run replays bit-identical from pinned inputs | `run_id` = timestamp only | content-pinned run manifest (§8) |

---

## 1. Layered architecture (medallion)

Data flows through quality tiers. **Nothing reaches a strategy from a lower tier.**

```
  PROVIDER                BRONZE                 SILVER                 GOLD
  (yfinance,    →  immutable raw,      →  cleaned + validated,  →  features / signals,
   settlement)     bitemporal,            contract-passed,         PIT-aligned,
                   content-addressed      lineage-tracked          strategy-ready
                        │                       │                      │
                        └──── QUARANTINE ◄──────┴──── contract fail ───┘
                              (failed gates, held for triage)
```

| Tier | Store | Gate to enter | Maps to Janus |
|------|-------|---------------|---------------|
| **Bronze** | `raw/<symbol>/knowledge_date=…/` immutable parquet | schema contract (structural) | ingestion output, `VersionedCache` |
| **Silver** | `clean/<instrument>/…` | semantic contract + validators pass + reconciliation within tolerance | adapter + Stage-1 validators output |
| **Gold** | `features/<instrument>/…` | leakage gate (no look-ahead) + feature contract | regime/IV/greeks + folds |
| **Quarantine** | `quarantine/…` | — (holding pen) | NEW — currently bad rows flow downstream silently |

**Key shift:** today validators *flag* bad rows (`_bound_flag`) but the rows continue
(`run_pipeline.py:157-159`). Institutional desks **divert** contract-failing rows to
quarantine and require explicit analyst sign-off before they re-enter — the data never
silently poisons a backtest.

---

## 2. Data Contracts (I2)

A **versioned, machine-checked interface** between every producer and consumer. Replaces the
column-only `validate_schema` (`ingestion/base.py:66-72`).

A contract = `{ structural + semantic + distributional + SLA }`:

```yaml
# contracts/settlement_options.v3.yaml
contract_id: settlement_options
version: 3
tier: bronze
structural:
  columns:
    as_of_date:   {dtype: "datetime64[ns]",        nullable: false}
    available_at: {dtype: "datetime64[ns, UTC]",   nullable: false}
    product_id:   {dtype: int64,  nullable: false, in_set_ref: symbology}
    strike:       {dtype: float64, nullable: true, key_round: 6}     # §9 float key
    price:        {dtype: float64, nullable: false}
    iv_provided:  {dtype: float64, nullable: true}
semantic:
  - "price > 0"
  - "right in ['C','P'] when instrument_type == 'option'"
  - "strike > 0 when instrument_type == 'option'"
  - "as_of_date <= expiry"
  - "available_at >= as_of_date"                    # PIT sanity
distributional:                                     # drift guards, PIT-safe windows
  - {col: iv_provided, check: psi, ref: trailing_60d, threshold: 0.25}
  - {col: price, check: null_rate, max: 0.01}
sla:
  freshness: "settlement available within 4h of as_of_date close"
  completeness: ">= 99% of expected contracts per as_of_date"
owner: "energy-data-ops"
breaking_change_policy: "version bump + consumer migration window"
```

- **Enforced at every tier boundary**, not once. Bronze gets structural+PIT; silver adds
  semantic; gold adds distributional+leakage.
- **Versioned** — a contract change is a `version` bump with a migration window, exactly
  like an API. Consumers pin the contract version they were validated against.
- Failures route to quarantine (§1) and open a break (§7).

> This subsumes audit findings **H2 (symbology not enforced)** and **H3 (dtype not
> enforced)** — both become contract clauses checked automatically.

---

## 3. Immutable bitemporal store (I1, I3)

Elevate `VersionedCache` to a **bitemporal, append-only, hash-chained** store.

Two time axes — the core of institutional PIT correctness:

| Axis | Meaning | Question it answers |
|------|---------|---------------------|
| **valid_time** (`as_of_date`) | the date the data *describes* | "what was the settlement price *for* 2024-03-15?" |
| **knowledge_time** (`available_at` / `ingested_at`) | when we *knew* it | "what did we *know* on 2024-03-15, restated or not?" |

- **Restatements never overwrite.** A corrected price for `as_of_date=2024-03-15` arriving
  later is a new row with a later `knowledge_time`. The original is preserved. A backtest
  replays "as known on date T" by filtering `knowledge_time <= T` — true PIT, immune to
  vendor restatement leakage (the classic yfinance `Adj Close` trap the loader already warns
  about, `ingestion/equity_loader_a.py:1-6`).
- **Content-addressed + hash-chained manifest.** Each partition keyed by canonical data hash
  (`round(8)` not `to_csv`, fixing the float-repr drift in `versioned_cache._data_hash:47`).
  The `_versions.jsonl` manifest chains `prev_hash → this_hash` → **tamper-evident** audit
  log (you can prove the history wasn't rewritten).
- **Time-travel read** is first-class: `read(symbol, knowledge_time=T, valid_range=…)`.

---

## 4. Point-in-time as an audited invariant (I3)

Today PIT is *intended* (`available_at`, `pit_join` in `versioned_cache.py:116`) but **not
audited**. Make it a gate:

- **No consumer reads `as_of_date`** for cross-source joins — only `available_at`. A static
  check (extend the leakage lint, `leakage_guard_design.md` L4) bans `as_of_date` in any
  merge/join key in feature code.
- **PIT assertion in the gold gate**: the future-perturbation test
  (`leakage_guard_design.md` L3) becomes a *release gate*, not just a unit test — gold tier
  rejects any feature that fails it.
- **Restatement diff** (§6) specifically watches knowledge_time changes: "price for an old
  valid_time changed under a new knowledge_time" → restatement break (§7), high priority.

---

## 5. Lineage graph (I4)

**Column-level** lineage — every output column declares its inputs + transform. Static
structure, complements the runtime CDC (§6).

```jsonc
// lineage/<instrument>.json  (generated from adapter declarations)
{
  "underlying_price": {"inputs": ["raw_close","adj_factor"], "op": "multiply", "tier": "silver"},
  "iv":               {"inputs": ["option_price","underlying_price","strike","T","r"],
                       "op": "solve_iv(black76)", "tier": "gold", "lookback_bars": 0},
  "vol_regime":       {"inputs": ["return_std"], "op": "causal_rank(expanding)",
                       "tier": "gold", "lookback_bars": 21}            // feeds purge (§ L5)
}
```

Payoffs:
- **Impact analysis**: "settlement `price` is suspect on date D — which features/signals are
  contaminated?" → walk the graph, don't grep.
- **Lookback rollup** feeds purge/embargo automatically (closes audit C2 + leakage L5):
  `purge_bars = max(lookback_bars)` across the lineage of the signal.
- **Reason at structural level**: when CDC (§6) sees `iv` change, lineage says *which* input
  moved → auto-attributes the break.

---

## 6. Change Data Capture — the diff engine (recast)

The prior diff design becomes the **CDC layer**: it detects change, classifies it, and feeds
breaks (§7). Mechanics unchanged from `data_diff_design.md`, summarized:

- **Row identity key** from `identity_cols` (per family), float keys snapped (§9).
- **Hybrid capture**: (A) instrumented emit at mutation sites (`outlier_cap`, strike-adjust,
  `price_std` overwrite) — carries reason for free; (B) post-hoc bitemporal align — catches
  **UNATTRIBUTED** changes.
- **ChangeRecord JSONL** atom — but now every record carries `contract_version`,
  `knowledge_time`, and a `lineage_ref`, and routes to break management when it breaches a
  tolerance band.

Three diff axes a desk runs (the POC only imagined one):

| Axis | Compares | Catches |
|------|----------|---------|
| **stage diff** | within one run, tier→tier | cleaning bugs, silent mutation |
| **vintage diff** | same data, two knowledge_times | vendor restatements |
| **cross-source recon** | two providers, same valid_time | feed disagreement, golden-source breaks |

---

## 6b. Cross-source reconciliation (the desk's daily bread)

Single-source data is a single point of failure. Institutional desks run **≥2 feeds** and
reconcile:

- **Golden-source hierarchy** per field: e.g. settlement price → exchange primary, vendor
  secondary; on disagreement beyond tolerance, primary wins and a break opens.
- **Tolerance bands** per field (price: ticks; IV: vol points — reuse
  `iv_validate_threshold`, already in configs).
- Janus already has the seed: `validate_provided_iv` (`core/pricing.py:179`) reconciles
  *provided vs self-solved* IV — that **is** a one-field reconciliation. Generalize it to a
  config-driven recon matrix across providers and fields.
- yfinance being "POC only" is fine — the architecture says *swap in a second feed and recon
  becomes active*; no code restructure needed.

---

## 7. Break management lifecycle (I5)

The institutional differentiator. Every out-of-tolerance change is a **break** — a tracked
object with an owner and an SLA, not a log line.

```
DETECTED ──► TRIAGED ──► { AUTO_RESOLVED | ACKNOWLEDGED | ESCALATED } ──► CLOSED
   │            │              │ (rule-fixed)   │ (analyst signs)  │ (to provider)
   └─ CDC/recon └─ classify    └───────────── audit trail ─────────┘
      raises       by severity
```

```jsonc
// breaks/<run_id>.jsonl
{
  "break_id": "BRK-20240315-00042",
  "type": "restatement",          // schema | semantic | drift | recon | restatement | unattributed
  "severity": "high",
  "detected_at": "...", "knowledge_time": "...",
  "key": {"as_of_date":"2024-03-15","product_id":254,"strike":85.0,"right":"C"},
  "field": "price", "before": 88.40, "after": 85.10, "tolerance": 0.02,
  "lineage_impact": ["iv","delta","vol_regime"],   // from §5 graph
  "owner": "energy-data-ops", "sla_hours": 4,
  "status": "ACKNOWLEDGED", "resolution": "vendor confirmed corrected settlement",
  "signed_by": "analyst_id", "signed_at": "..."
}
```

- **Severity routing**: structural/PIT breaks block the tier (data can't promote); drift
  breaks warn; cosmetic breaks log.
- **UNATTRIBUTED changes (from §6) are auto-high** — a value moved and no rule owns it = a
  bug until proven otherwise.
- **SLA + ownership** from the contract (`owner:`, `sla:`). Unsigned high breaks past SLA →
  escalate.
- **Segregation of duties**: the analyst who acknowledges a break ≠ the pipeline that raised
  it. Sign-off is recorded, immutable.

---

## 8. Reproducibility & run manifest (I6)

A run is reproducible only if every input is pinned. Replace `run_id = timestamp`
(`run_pipeline.py:127`) with a content-pinned manifest:

```jsonc
// outputs/manifest/<run_id>.json
{
  "run_id": "20240101_120000",
  "code_version": "git:f2d0672",                 // commit hash
  "config_hash": "…",                            // normalized cfg hash
  "contract_versions": {"settlement_options": 3},
  "input_data_hashes": {"B:254": "sha…"},        // exact bronze partitions read
  "knowledge_time_cutoff": "2024-12-31T21:00Z",  // PIT boundary
  "env": {"python":"3.12","numpy":"…","pandas":"…"},
  "output_data_hashes": {"prepared": "sha…"}
}
```

- **Replay guarantee**: same manifest → bit-identical output. CI re-runs a golden manifest
  and asserts hash match (catches nondeterminism, the float-hash drift, dependency creep).
- **DSR honesty** (audit H5): the manifest records the *actual* number of configs/trials
  across a research campaign → `n_trials` for Deflated Sharpe becomes real, not the
  hardcoded `40`.

---

## 9. Numeric & key discipline (carried from precision discussion)

- **Float keys** (`strike`) snapped to tick / `round(6)` before any join/group/diff
  (`check_pcp` key, `adapters/options_base.py:299`).
- **Cell compare**: tolerance band per field (`atol+rtol`), defined in the contract.
- **Canonical hashing**: `pd.util.hash_pandas_object(df.round(8))` everywhere, never
  `to_csv` text — required for I6 replay and I1 hash-chain integrity.
- float64 for math (no Decimal); int-cents only if real cash settlement reconciliation is
  ever added (not now).

---

## 10. Observability (cross-cutting)

Files alone don't run a desk. Emit **metrics** per run, per tier:

- rows in/out/quarantined, null-rate, contract pass-rate, drift PSI, break counts by
  severity, SLA breaches, reconciliation match-rate.
- → time-series store + dashboard + threshold alerts (analyst is paged on a high break, not
  discovering it in a CSV next week).
- The HTML diff viewer (`data_diff_design.md` §5.3) stays as the **drill-down** surface
  behind a metric: click "12 unattributed breaks" → see the rows.

---

## 11. Component map (build targets)

```
contracts/                 # §2 versioned data contracts (yaml)
core/contracts.py          # contract loader + validator (replaces validate_schema)
ingestion/biteporal_store.py  # §3 (evolve versioned_cache.py)
core/lineage.py            # §5 graph from adapter declarations
core/cdc.py                # §6 diff engine (was data_diff_design)
core/reconcile.py          # §6b cross-source (generalize validate_provided_iv)
core/breaks.py             # §7 break lifecycle + ledger
core/manifest.py           # §8 run pinning + replay check
core/observability.py      # §10 metric emit
quarantine/                # §1 holding pen
```

---

## 12. Phased rollout (institutional target ≠ build-it-all-at-once)

| Phase | Delivers | Invariants | Why first |
|-------|----------|-----------|-----------|
| **P0** | Data Contracts (§2) + quarantine routing (§1) | I2 | stops bad data poisoning backtests today; subsumes H2/H3 |
| **P1** | Bitemporal store + run manifest (§3,§8) | I1,I6 | reproducibility + restatement safety; foundation for everything |
| **P2** | CDC engine + break ledger (§6,§7) | I5 | the "what changed + who owns it" core |
| **P3** | Lineage graph (§5) + auto-purge | I4 | impact analysis; closes leakage L5 / audit C2 |
| **P4** | Cross-source recon (§6b) + observability (§10) | — | activates when 2nd feed lands; dashboards |
| **P5** | HTML drill-down viewer | — | analyst speed; build once model stable |

**Honest scope note:** this is the *target* a funded desk reaches over quarters, not a
weekend. For Janus-as-research-tool, **P0+P1 alone** already lift it from "POC" to
"defensible" — contracts stop silent corruption, bitemporal+manifest make every result
reproducible and restatement-proof. P2+ are the full ops desk; pursue as the data scales and
real capital depends on it.

---

## 13. Open decisions
1. Build the bitemporal store in-house (evolve `versioned_cache.py`) vs adopt **Apache
   Iceberg / Delta Lake** (gives time-travel, schema evolution, ACID for free at scale). →
   in-house for POC; Iceberg when partition count / concurrency grows.
2. Break store: JSONL ledger (simple, greppable) vs a real ticketing/DB backend with
   dashboards. → JSONL P2, DB at P4.
3. Contract checking engine: hand-rolled vs **Pandera** (declarative, typed) vs **Great
   Expectations** (heavy, full suite). → Pandera fits §2 best; GE is over-eng here.
4. Where does `n_trials` for DSR actually get counted — per run, or per research campaign
   manifest? (§8) Needs a campaign-level registry above run-level.
