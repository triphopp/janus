# Janus — P1 Implementation Report (Bitemporal Store + Run Manifest)

> Phase P1 of `Memory/plans/data_ops_architecture.md` §12. Delivers invariants **I1**
> (immutable raw) + **I6** (reproducible). Builds the foundation P2+ depends on.
> Also closes audit **H5** (fake DSR trial count) partially and the float-repr hash drift.
> Date: 2026-06-16 · Status: **implemented, 157/157 tests pass** (+14 over P0)

---

## 1. What shipped

| File | Change |
|------|--------|
| `core/audit.py` | new `canonical_frame_hash` — sha256 over `hash_pandas_object`, cols sorted, floats round(8). `hash_subset` now delegates to it (was `to_csv` text) |
| `ingestion/versioned_cache.py` | `_data_hash` → canonical hash; ACID write (temp→`os.replace`); manifest `prev_hash`/`chain_hash`/`writer`; `as_of_knowledge` time-travel read; `verify_partition` + `verify_on_read` |
| `core/manifest.py` | **new** — `build_manifest`, `write_manifest`, `compare_manifests`, `git_commit`, `config_hash`, `env_info` |
| `run_pipeline.py` | builds + writes a run manifest every run (`outputs/manifest/<run_id>.json` + run dir) |
| `tests/test_core/test_manifest.py`, `tests/test_ingestion/test_bitemporal.py` | 14 new tests |

---

## 2. Invariants delivered

### I1 — Immutable raw (bitemporal)
- **Restatement never overwrites**: a corrected value for `as_of_date=Mar15` arriving later
  is a new `ingested_at=Mar20` partition; the original `Mar15` partition stays.
- **Time-travel read**: `data_version: as_of_knowledge` + `knowledge_time: T` returns the
  latest partition with `knowledge_time <= T` — "what we KNEW as of T", immune to future
  vendor restatement (the yfinance `Adj Close` trap). Proven by
  `test_time_travel_reads_value_as_known_at_T`.
- **Tamper-evidence**: manifest now chains `prev_hash → chain_hash` per symbol; rewriting
  history breaks the chain. `writer` (host/pid/user) recorded (§13.3).
- **ACID write** (§13.4): serialize to `*.tmp`, then `os.replace` (atomic same-FS). A crash
  mid-write leaves an orphan `.tmp`, never a half-written partition.
- **Integrity check**: `verify_partition` recomputes the canonical hash vs manifest;
  `verify_on_read=True` raises on mismatch. Parquet/pickle only — CSV loses dtypes on
  round-trip, so it's skipped (manifest write-time hash stays authoritative).

### I6 — Reproducible (run manifest)
Every run pins: `code_version` (git sha), `config_hash`, `contract_versions`,
`input_data_hashes` (pre-gate provider input), `output_data_hashes` (prepared),
`knowledge_time_cutoff` (max `available_at`), `env` (python/numpy/pandas/scipy/pyarrow),
`n_trials`. `compare_manifests` implements **bit_replay** (hash equality); env diff is
reported separately, not silently failed (§13.10).

### Canonical hashing (precision fix)
`to_csv`-text hashing drifted on float repr across platforms. Replaced with value-based
`hash_pandas_object` on column-sorted, 8-dp-rounded frames. Proven: ignores sub-8dp noise
(`test_canonical_hash_ignores_sub_8dp_float_noise`), detects real change, column-order
invariant.

---

## 3. Deviations / honest limits

### 3.1 H5 (DSR n_trials) only partially closed
Manifest records the **actual** `n_trials` used + `n_trials_source: "config"` — no longer a
silent hardcoded `40`. But a *true* campaign-wide trial count needs a campaign registry
**above** the run level (open decision §14.4). Flagged in the manifest, not faked.

### 3.2 input_data_hash = pre-gate provider frame, not a bronze partition pointer
The pipeline doesn't yet read from the bitemporal store by default
(`read_versioned_cache` is opt-in via cfg). So the manifest hashes the in-memory ingested
frame. Once the store is the default read path, switch `input_data_hashes` to reference the
exact partition hash from `_versions.jsonl` (cheap follow-up).

### 3.3 Date-granular partitions
`ingested_at` partitions are date-keyed. Two restatements **on the same calendar day**
collide (immutability raises `FileExistsError` — correct, but blocks intraday re-ingest).
Finer (timestamp) granularity deferred until intraday restatement is real.

### 3.4 Still no committed sample dataset
`run_pipeline.py --instrument bz` still can't run end-to-end (no settlement file in repo,
pre-existing). P0+P1 wiring is proven by unit/integration tests, not a full CLI run. A small
non-vendor synthetic fixture would unblock a true end-to-end smoke — recommended next.

---

## 4. §13.12 acceptance — status after P1

| Criterion | P0 | P1 |
|-----------|----|----|
| contract validation blocks structural/PIT | partial | partial |
| quarantine rate in every run summary | ✅ | ✅ |
| raw writes append-only + manifest-pinned | ⏳ | ✅ (immutable + chain) |
| replay uses a manifest, not live fetch | ⏳ | ✅ manifest written; replay-compare available |
| report comparison warns on contract/version mismatch | ⏳ | partial (`compare_manifests` exists; not yet wired into reporting) |
| restatement replay golden fixture | ⏳ | ✅ `test_time_travel_reads_value_as_known_at_T` |

---

## 5. Next (P2)
CDC diff engine (§6) + break lifecycle (§7). Stage→stage cell diff with reason attribution
(instrument `outlier_cap` / strike-adjust / `price_std` overwrite), UNATTRIBUTED detection
via post-hoc align, ChangeRecord JSONL → break ledger with owner/SLA. The canonical hash and
manifest from P1 are the substrate the diff/break records reference.
