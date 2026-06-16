# Janus — P2 Implementation Report (CDC Diff Engine + Break Ledger)

> Phase P2 of `Memory/plans/data_ops_architecture.md` §12. Delivers invariant **I5**
> (break-managed). The "what changed + who owns it" core.
> Date: 2026-06-16 · Status: **implemented, 174/174 tests pass** (+17 over P1)

---

## 1. What shipped

| File | Role |
|------|------|
| `core/cdc.py` | **new** — key-aligned stage diff → `ChangeRecord` JSONL; reason attribution via flag columns; `diff_run`, `write_ledger`, `rollup` |
| `core/breaks.py` | **new** — break lifecycle (DETECTED→…→CLOSED), severity routing, signed transitions, SoD enforcement, `verify_chain` |
| `run_pipeline.py` | diffs adapter→validators every run; writes change ledger + break ledger; `summary.cdc` |
| `tests/test_core/test_cdc.py`, `test_breaks.py` | 17 new tests |

Outputs per run: `outputs/diff/<run_id>_changes.jsonl`, `outputs/breaks/<run_id>.jsonl`.

---

## 2. How it works

**CDC (§6, Strategy B + flag attribution):**
- Aligns two stage frames on the business key (`identity_cols`), **float keys snapped to
  6 dp** so bit-noise doesn't fake a row_drop+row_add pair (§9).
- Emits `schema_add/drop`, `row_add/drop`, `cell_mod` with `before/after/delta/pct`.
- **Reason attribution without editing the mutators**: a `cell_mod` on `price` co-located
  with `_outlier_flag==True` → `reason="outlier_cap"`. Uses the flag columns validators
  already emit (§4). Anything else → **`UNATTRIBUTED`** — the silent-bug bucket.
- Per-column tolerance (`atol`/`rtol`) suppresses float noise from real changes.

**Breaks (§7):**
- `UNATTRIBUTED` cell change → **high**; unexpected `row_add` / unexplained `row_drop` →
  **medium**; attributed mutations (outlier_cap, etc.) → **not a break** (expected op).
  Schema additions of flag columns → not a break.
- Lifecycle is a real state machine; illegal transitions raise.
- **Signed transitions (§13.7)**: each carries `actor_id`, `actor_role`, timestamps,
  `prev_hash` of the prior entry → `verify_chain` detects tampering.
- **Segregation of duties**: `system` actor cannot `ACKNOWLEDGE`/`CLOSE`; high-severity
  closure requires a `reason_code`.

---

## 3. Scope decisions / honest limits

### 3.1 Diff covers adapter→validators only (for now)
That's the stage with the real mutation (`outlier_cap`). The engine is stage-agnostic
(`diff_run` takes any list of stage frames) — wiring more transitions (ingestion→adapter,
validators→splitter) is a one-line addition once those stages mutate values worth tracking.

### 3.2 Attribution is flag-based (Strategy A-lite), not instrumented-at-source
The doc's full Strategy A emits a ChangeRecord *inside* `outlier_cap` at the mutation site.
We instead join to the `_outlier_flag` the op already sets — same attribution, **zero edits
to the validators**, lower regression risk. Trade-off: a mutation that sets no flag shows as
`UNATTRIBUTED` (which is correct — it *should* be flagged as a silent change).

### 3.3 Breaks are raised but not auto-triaged
`raise_breaks` creates DETECTED breaks. Transitions (TRIAGE/ACK/CLOSE) are an analyst action
via `transition()` — no auto-resolver yet. The vintage-diff and cross-source-recon break
sources (§6 axes 2–3) need P1's store as default read path + a 2nd feed (P4) respectively.

### 3.4 CDC never breaks the run
The whole block is wrapped — a CDC failure logs `summary.cdc.status="error"` and the
pipeline continues. Observability must not take down the pipeline it observes.

---

## 4. §13.12 acceptance — status after P2

| Criterion | Status |
|-----------|--------|
| contract validation blocks structural/PIT | partial (enforcement=warn default) |
| quarantine rate in every run summary | ✅ |
| raw writes append-only + manifest-pinned | ✅ (P1) |
| replay uses a manifest | ✅ (P1) |
| **change tracking with reason + owner** | ✅ **(P2)** ledger + break lifecycle |
| report comparison warns on contract/version mismatch | partial (`compare_manifests` not yet in reporting) |
| restatement replay golden fixture | ✅ (P1) |

---

## 5. Next (P3)
Column-level lineage graph (§5) + auto-purge. Each derived column declares inputs+transform;
`purge_bars = max(lookback)` walks the graph → closes leakage **L5** and audit **C2**. Pairs
with the leakage-guard **L3** future-perturbation test, which will immediately fail on
`regime.assign_regime_labels` (vol_regime rolling over row order) — proving C2.
