# Janus — P0 Implementation Report (Data Contracts + Quarantine)

> Phase P0 of `Memory/plans/data_ops_architecture.md` §12. Delivers invariant **I2**
> (contract-validated data) + quarantine routing (§1). Subsumes audit findings **H2**
> (symbology not enforced) and **H3** (dtype not enforced).
> Date: 2026-06-16 · Status: **implemented, 143/143 tests pass**

---

## 1. What shipped

| File | Role |
|------|------|
| `contracts/settlement_options.v1.yaml` | bronze contract for futures + options (RAW_SCHEMA) |
| `contracts/equity_price.v1.yaml` | bronze contract for equity bars (EQUITY_RAW_SCHEMA) |
| `core/contracts.py` | loader + row-routing validator (structural/semantic/PIT/symbology/distributional) |
| `core/quarantine.py` | holding pen writer + per-dimension breakdown |
| `run_pipeline.py` | bronze gate wired after ingestion, before adapter |
| `tests/test_core/test_contracts.py` | 12 tests (unit + integration) |

**Behavior:** raw data is validated against its versioned contract. Failing rows are
**diverted to `quarantine/<run_id>/bronze.{parquet,csv}`** with a `_quarantine_reason`;
clean rows continue. Quarantine rate + reasons surface in `summary.json`
(`contract_gate`, `quarantine`, `guard_status.contract_gate`).

Reason taxonomy: `structural:<col>`, `semantic:<reason>`, `pit:available_before_as_of`,
`symbology:orphan`.

---

## 2. Deviations from the design doc (decisions + rationale)

### 2.1 PIT moved out of `semantic:` into a dedicated `pit:` block
The doc listed `"available_at >= as_of_date"` as a semantic rule. **Problem:**
`available_at` is `datetime64[ns, UTC]` (tz-aware) and `as_of_date` is `datetime64[ns]`
(naive). A direct pandas `>=` **raises** `Cannot compare tz-aware and tz-naive`. Generic
semantic eval would silently skip it (caught as "unevaluable"), losing the check.
**Fix:** a code-level `_pit_violation` that normalizes both sides to UTC. Contract now has
a `pit:` block instead. **Recommend keeping this** — PIT is too important to leave to a
string rule that crashes.

### 2.2 `enforcement` defaults to `warn`, not `block`
§13.12 acceptance wants contract validation to **block** structural/PIT failures. But:
- Per-row failures **already** divert to quarantine regardless of enforcement — bad data
  never poisons the backtest. "Block" only governs **frame-level** breaks (missing required
  column, distributional drift), which *halt* the run.
- Defaulting to `block` now would hard-crash the first real run that has any drift, before
  the desk has triaged baselines.

**Decision:** default `warn` for P0 rollout; per-row quarantine is always on. Flip to
`block` per-contract once real data is observed clean. This is the one **open item against
§13.12** — flagged here so it's a conscious choice, not an oversight.

### 2.3 Hand-rolled validator, not Pandera (§14 open decision #3)
Chose in-house. Rationale: the codebase is uniformly hand-rolled asset-agnostic pandas
(`core/validators.py`, `ingestion/base.py`); adding Pandera is a new heavy dep for marginal
gain at POC scale. The doc itself says "in-house for POC; Pandera when it grows." Revisit at
P2+ if rule complexity climbs.

### 2.4 Distributional PSI = `not_evaluated`
`null_rate` checks run now. **PSI needs a reference vintage** (trailing_60d), which only
exists once the **P1 bitemporal store** lands. PSI clauses are parsed and reported as
`not_evaluated: needs reference vintage (P1)` rather than faked. Honest stub.

### 2.5 Semantic rule syntax constrained to `| & == <= >= < >` + `(...)`
Avoided `and`/`or`/`in [...]` (engine/`numexpr` string pitfalls under `engine='python'`).
Option `right` check written as `((right == 'C') | (right == 'P'))` not `right in ['C','P']`.
Rules eval with `engine='python'`; contracts are trusted local YAML (not user input).

---

## 3. Could not verify against real `bz` data
`python run_pipeline.py --instrument bz` **crashes before the gate** at
`provider.fetch` (`settlement_loader.py:57`, `FileNotFoundError: Settlement file not found`)
— **no settlement data file is committed to the repo**. This is a pre-existing condition,
unrelated to P0. The gate path (`cfg → contract → validate → pass`) is instead proven by an
integration test using the `bz_config` + `sample_raw_df` fixtures
(`test_validate_for_cfg_resolves_from_bz_config_and_passes_clean`).

> Risk note for later: the pipeline has no committed/sample raw dataset, so no end-to-end run
> is reproducible from a clean clone. Worth a small fixture settlement file (P1 manifest work
> touches this anyway).

---

## 4. §13.12 acceptance — status after P0

| Criterion | Status |
|-----------|--------|
| contract validation blocks structural/PIT failures | **partial** — per-row diverted always; frame-level block gated behind `enforcement=block` (§2.2) |
| quarantine rate appears in every run summary | ✅ `summary.contract_gate.quarantine_rate` + `summary.quarantine` |
| raw writes append-only + manifest-pinned | ⏳ P1 (bitemporal store) |
| replay uses a manifest, not live fetch | ⏳ P1 |
| report comparison warns on contract/version mismatch | ⏳ P1/P2 |
| restatement replay golden fixture | ⏳ P1 |

P0 closes the **I2 + quarantine** items. The rest are P1 (bitemporal + manifest) by design.

---

## 5. Next (P1)
Bitemporal store (`§3`) + run manifest (`§8`) → unlocks PSI reference vintages (§2.4),
restatement diff, and reproducible replay. Evolve `ingestion/versioned_cache.py` with the
ACID write path from §13.4 (write-temp → validate → atomic-rename → manifest-append) and the
`hash_pandas_object(round(8))` canonical hash from §9 (replacing the `to_csv` hash at
`versioned_cache.py:47`).
