# Stage-Chain Diff Redesign — Data-Ops Dashboard

> Companion to `data_diff_design.md`. Closes the "dashboard shows nothing changed" gap.
> Date: 2026-06-17

## Problem (root cause)

`run_pipeline.py` ran CDC over a **single** stage transition:

```python
cdc.diff_run([("adapter", frame_adapter), ("validators", df.copy())], ...)
```

For clean provider data the validators stage only **appends flag columns**
(`_bound_flag`, `_missing_flag`, ...) and caps nothing. Result: the change ledger
holds ~5 `schema_add` records, **0 cell/row changes** → the dashboard renders an empty
before/after, looking as if the pipeline did nothing.

The pipeline IS working. The real transformation happens one hop earlier, at
**ingestion → adapter** (derive `price_std`, `adjusted_price_provider`, `return_std`,
`vol_std`, survivorship, PIT-MAD clip). That hop was never diffed, so it was invisible.

## Decision

**Stage-chain cell diff** (chosen over column-lineage). Diff every consecutive stage
where the prepared frame is actually mutated:

```
ingestion → adapter → validators
```

Splitter / metrics operate on folds and copies, not the prepared frame, so they are
**not** in the chain (would only emit empty hops).

### Why not lineage
`raw_close → price_std` is a *derive* (both columns coexist), not an in-place mutation,
so a pure cell diff shows it as `schema_add` of `price_std`, not a `cell_mod`. To keep
the chosen model honest we **enrich `schema_add`** with a sample value + non-null count,
so a derived column reads as "`price_std` appeared = 95.21 (6285 non-null)" instead of a
bare "column added". This is the bridge toward git-diff feel without a full lineage map.

## Changes

| File | Change |
|------|--------|
| `core/cdc.py` | `schema_add` records carry `after` (first non-null sample) + `sample_count`. New `_first_sample()` helper. `classify()`/breaks untouched → no false breaks. |
| `run_pipeline.py` | Capture `frame_ingestion` (post-bronze-gate provider input) and the post-validators frame; diff the 3-stage chain. `reason_maps` unchanged (adapter→validators attribution kept). |
| `web/diff_report.py` | Add **Stage** column to the change table (filter already existed). |
| `web/dashboard.py` | Run-detail modal: **stage pipeline strip** (per-hop change counts) + Stage column in the change sample. |

## Safety notes

- `breaks.classify()` returns `None` for `schema_add` → flooding derived-column adds
  raises **0 breaks**.
- Equity adapter never drops/reorders-out rows (sort + append + in-place clip), so the
  new hop emits no spurious `row_drop`/`row_add`.
- An *unexpected* `cell_mod` on a shared raw column (e.g. `raw_close` mutated between
  ingestion and adapter) now surfaces as `UNATTRIBUTED` high break — which is exactly
  the silent-mutation signal we want.
- Identity keys (`as_of_date`, `symbol`) exist in both ingestion and adapter frames;
  `core_cfg.identity_cols` is available at the diff site.
