# Janus — Data Diff & Change-Tracking Design

> Scope: design (not implementation) for a GitHub-style diff over cleaned data, so an
> analyst can see **what changed, where, and why** across pipeline stages faster than
> eyeballing CSVs.
> Companions: `docs/design/audit_findings_pre_data_structure.md`, `docs/design/leakage_guard_design.md`.
> Date: 2026-06-16

---

## 1. Problem

The pipeline mutates data across stages:
`ingestion → adapter → validators(bounds/completeness/outlier_cap) → splitter → metrics`.

Today the only visibility is:
- `core/audit.py:snapshot` — per-stage `row_count`, `schema_hash`, `data_hash`,
  `key_stats`, `na_pattern`.
- `core/audit.py:diff_stages` — row delta, schema-changed bool, new NaN counts, aggregate
  stat deltas.
- Raw `outputs/data/<run_id>_prepared.csv`.

Gap: **aggregate-only**. It tells you "mean of price moved" but not *which contract on
which date got capped from X→Y and by which rule*. Finding one suspicious cell = scrolling
CSV. We want **row/cell-level, reason-attributed, GitHub-style** diff.

---

## 2. Core concepts

### 2.1 Row identity key (the hard part)
Cell diff needs to **align the same logical row** before/after a stage. Stages add rows
(none here), drop rows (date filter), and mutate values (cap, strike-adjust). Position
(`iloc`) is useless after a drop. Need a **stable business key** per family:

| Family | Identity key |
|--------|-------------|
| equity | `(as_of_date, symbol)` |
| futures | `(as_of_date, product_id, delivery_month)` |
| options (eq/fut) | `(as_of_date, product_id, contract_root, hub, delivery_month, expiry, right, strike)` |

Reuse the `identity_cols` adapters already build
(`adapters/futures_options_adapter.py:85`, `equity_options_adapter.py:75`). The diff engine
takes `identity_cols` from the prepared cfg — single source of truth.

> ⚠️ **Float keys must be normalized.** `strike` is float; raw bit differences split a key.
> Snap to tick / `round(6)` before using as key (see `data_diff_design` §6 and the precision
> finding). Datetime keys → normalize tz/precision first.

### 2.2 Change types
| Type | Meaning | Example |
|------|---------|---------|
| `schema_add` | new column | `_outlier_flag`, `iv`, `delta` |
| `schema_drop` | column removed | rare |
| `schema_dtype` | dtype changed | `timestamp` object→datetime |
| `row_add` | new key appears | (none expected; flag if it happens) |
| `row_drop` | key disappears | date-range filter, dedup |
| `cell_mod` | same key+col, value changed | `price` capped X→Y |

### 2.3 Change record (the atom)
One JSONL line per change — machine queryable, the substrate everything else renders from:

```jsonc
{
  "run_id": "20240101_120000",
  "stage_from": "adapter",
  "stage_to": "validators",
  "change_type": "cell_mod",
  "key": {"as_of_date":"2024-03-15","product_id":254,"strike":85.0,"right":"C","expiry":"2024-05-01"},
  "column": "price",
  "before": 88.40,
  "after": 85.10,
  "delta": -3.30,
  "pct": -0.0373,
  "reason": "outlier_cap",          // attributed — see §4
  "reason_flag_col": "_outlier_flag"
}
```

---

## 3. Two diff strategies (use both — hybrid)

### Strategy A — Instrumented (emit at the mutation site) ✅ primary
The cleaning op already **knows** what it changed and why. `outlier_cap`
(`core/validators.py:128-176`) computes the cap and sets `_outlier_flag` — it can emit a
`cell_mod` with `reason="outlier_cap"` for free. Same for strike-adjust, price_std
overwrite, IV solve.

- Pro: cheap (no realignment), carries the **reason** authoritatively, exact.
- Con: only covers changes we instrument.

### Strategy B — Post-hoc frame align (diff two snapshots) ✅ safety net
Key-align `df_before` vs `df_after`, compare cells with float tolerance. Catches
**unattributed / unexpected** changes (the scary ones — a value moved and no rule claims
responsibility).

- Pro: catches everything, including bugs.
- Con: O(rows × cols), alignment cost, no reason unless cross-referenced to flags.

**Hybrid rule:** Strategy A produces the attributed ledger; Strategy B runs per stage and
any `cell_mod` **not** explained by an A-record or a flag column is tagged
`reason="UNATTRIBUTED"` → loud warning. That unattributed bucket is the highest-value
output: it's where silent bugs live.

---

## 4. Reason attribution

Every existing flag column is a change-reason marker. Join value-change → flag:

| Flag column | Stage | Explains |
|-------------|-------|----------|
| `_bound_flag` / `_bound_reason` | validators | bound violation (not a mutation, a tag) |
| `_outlier_flag` | validators | `price` capped |
| `_missing_flag` / `_missing_reason` | validators | completeness gap |
| `net_change_flag` | ingestion | settlement net-change mismatch |
| `_pcp_flag`, `pcp_pair_missing` | adapter | PCP violation |
| `iv_flag` | adapter | provided-IV vs solved mismatch |
| strike-adjust (`strike_raw`→`strike`) | adapter | corp-action adjust |

A `cell_mod` on `price` co-located with `_outlier_flag==True` → `reason="outlier_cap"`.
If `_bound_reason` already strings the cause (`"price<=0;"`), surface it verbatim.

---

## 5. Output layers (the "better than CSV" part)

```
ChangeRecord JSONL  →  Rollup summary  →  HTML diff viewer
   (atom)               (counts)            (human, GitHub-style)
```

### 5.1 Ledger — `outputs/diff/<run_id>_changes.jsonl`
The atoms. Queryable: "show every cell_mod on `price` > 5% in validators",
"all UNATTRIBUTED changes". This alone beats CSV scrolling.

### 5.2 Rollup — `outputs/diff/<run_id>_diff_summary.json`
Per stage-transition × column:
```jsonc
{"adapter→validators": {
   "price":  {"cell_mod": 142, "max_abs_delta": 12.4, "unattributed": 0},
   "rows":   {"dropped": 38, "added": 0}}}
```
Extends the current `diff_stages` from aggregate stats to per-column change counts.

### 5.3 HTML viewer — `outputs/diff/<run_id>_diff.html` ⭐
GitHub-style, self-contained (matches existing `reporting.write_html_report` pattern —
JSON embedded + JS render):

- **Stage selector**: `ingestion → adapter → validators → …` tabs (reuse pipeline-flow UI).
- **Color legend**: 🟩 col added · 🟥 row dropped · 🟨 cell modified · 🟧 UNATTRIBUTED.
- **Row view**: only changed rows (not the whole frame). Each shows key + changed cells
  `before → after (Δ)`, reason chip.
- **Filters**: by column, by reason, by |Δ| threshold, by date.
- **Unattributed-first toggle**: jump straight to silent changes.
- **Drill**: click a contract key → its full before/after across all stages (provenance trail).

This is the speed win: analyst opens HTML, filters "UNATTRIBUTED" or "price Δ>5%", sees 12
rows instead of scanning 40k-row CSV.

---

## 6. Float & determinism handling (ties to precision finding)

- **Key normalization**: round/snap float keys (`strike`) before alignment, else spurious
  `row_drop`+`row_add` pairs.
- **Cell compare tolerance**: `cell_mod` only when `abs(after-before) > atol + rtol*abs(before)`
  (defaults `atol=1e-9`, `rtol=0`). Configurable per column (price vs IV vs greeks scale
  differently).
- **Canonical hashing**: dedup/identity hashing on `round(8)`, not `to_csv` text — avoids
  cross-platform float-repr drift (current `audit.hash_subset` / `versioned_cache._data_hash`
  weakness).

---

## 7. Integration with existing code

Extend, don't replace:
- `core/audit.py:snapshot` already runs at every stage (`run_pipeline.py:146-292`). Add an
  optional `keep_frame=True` path or store a content-addressed parquet per stage so
  Strategy B has both sides to align.
- `core/audit.py:diff_stages` → upgrade to emit ChangeRecords + rollup, keep the aggregate
  output for backward compat.
- New module `core/datadiff.py` (engine) + `core/diff_report.py` (HTML), mirroring the
  `reporting.py` split.

Pipeline wiring (sketch, not final):
```python
snaps = {}                         # stage -> frame ref (parquet path or in-mem)
for stage, frame in pipeline_stages:
    snaps[stage] = persist_or_ref(frame)
ledger = datadiff.diff_run(snaps, identity_cols, tol_cfg)   # hybrid A+B
diff_report.write_html(ledger, run_id, outputs_dir)
```

---

## 8. Build vs buy — evaluated

| Option | What | Fit | Verdict |
|--------|------|-----|---------|
| `pandas.DataFrame.compare()` | built-in cell diff | needs identical index/cols → must key-align first; no reason, no add/drop | **use as inner engine** for Strategy B after alignment |
| `daff` (Coopy highlighter) | tabular GitHub-style HTML diff | literally the target visual; but row-key + reason attribution still ours | **borrow render idea**, maybe vendor for HTML |
| `deepdiff` | nested object diff | row-oriented, slow on big frames, no domain reason | skip |
| `data-diff` (Datafold) | cross-DB row diff at scale | DB-to-DB, overkill for local parquet, infra heavy | **over-eng** here |
| DVC / lakeFS / Delta Lake | dataset version control | versions *files*, not stage-level cell reasons; heavy | defer; orthogonal to this |
| great_expectations | validation suite | validates, doesn't diff/track mutations | wrong tool |

**Recommendation:** build a thin engine — stable-key align + `pandas.compare` for cells +
flag-join for reason + a small embedded-JSON HTML viewer (reuse `reporting.py` style).
Borrow `daff`'s highlight UX. Don't adopt data-diff/DVC for this — that's infra for a
problem we don't have yet.

---

## 9. Scope cut (avoid over-eng)

POC / research context — phase it:

| Phase | Deliverable | Effort | Why |
|-------|-------------|--------|-----|
| **MVP** | Strategy A ledger (JSONL) for `outlier_cap` + strike-adjust + price_std overwrite; rollup JSON | low | covers the known mutators, reason for free; 80% of value |
| **P2** | Strategy B post-hoc align + UNATTRIBUTED detection | medium | catches silent bugs — the real safety net |
| **P3** | HTML viewer with filters | medium | analyst speed; build once data model stable |
| **Skip now** | DVC/lakeFS versioning, cross-DB, real-time diff | — | no current need; revisit at scale |

Start MVP: instrument the 3 mutators to emit ChangeRecords. That alone replaces "scroll the
CSV" for the common case and costs little, because the ops already compute the change.

---

## 10. Open questions
1. Persist every stage frame (storage cost) vs only diffs (can't re-derive)? → lean: store
   content-addressed parquet per stage, GC by `audit.retain_days` (already in config).
2. Per-column tolerance defaults — who owns them? → instrument config `diff_tol:` block.
3. Should `row_drop` from the date-range filter be noise-suppressed (expected) vs flagged?
   → tag drops with cause (`date_filter` vs `dedup` vs `validator`) so expected ≠ alarming.
4. Diff across **runs** (today vs yesterday's cleaned data, data-version drift) vs across
   **stages** (within one run)? This design covers stage-diff; run-diff reuses the same
   engine with two `_prepared.parquet` inputs — keep the engine input-agnostic.
