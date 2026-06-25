# Janus — P3 Implementation Report (Lineage + Leakage Guards + Auto-Purge)

> Phase P3 of `Memory/plans/data_ops_architecture.md` §12. Delivers invariant **I4**
> (lineage) + the leakage guard L3/L4/L5 from `leakage_guard_design.md`.
> Date: 2026-06-16 · Status: **implemented, 184/184 tests pass** (+10 over P2)

---

## 1. Key finding: audit C2 was already fixed

The audit flagged `regime.py:42-43` as computing `vol_regime` rolling over **row order**
(mixing strikes → meaningless window). On reading the current code, **this is already
fixed**: `core/causal.py` exists with `to_causal_series` (grain gate, leakage L2),
`causal_vol`, `causal_rank` (L1), and `regime.assign_regime_labels` now routes every axis
through a **date-grain** series + `broadcast_by_date`. The audit was written against an
earlier revision. P3 therefore **proves** the fix rather than re-implementing it.

---

## 2. What shipped

| File | Role |
|------|------|
| `core/lineage.py` | **new** — graph: `impact_of`, `upstream_inputs`, `max_lookback`, `validate_coverage` |
| `lineage/futures_options.json` | **new** — declared lineage for the futures-options features |
| `core/leakage.py` | **new** — `assert_no_lookahead` (L3) + `scan_lookahead_patterns` (L4) |
| `run_pipeline.py` | opt-in lineage auto-purge (L5); `summary.lineage_purge` |
| `tests/test_core/test_lineage.py`, `test_no_lookahead.py` | 10 new tests |

---

## 3. What it does

### Lineage graph (I4, §5)
- **Impact analysis**: `impact_of(graph, "price")` → every feature transitively derived
  from settlement price. "This input is suspect — what's contaminated?" without grepping.
- **Auto-purge (L5)**: `max_lookback(graph)` = the correct purge window by definition (a
  feature's lookback IS how far its window reaches back). Closes the "guess `max_dte`/5"
  problem from audit C2's neighborhood.
- **Coverage gate (§13.6)**: `validate_coverage` flags any derived column with no lineage
  record (underscore audit flags + declared roots exempt) — declarations can't silently
  drift from code.

### Leakage L3 — future-perturbation test (the real guard)
`assert_no_lookahead(build_features, df)` perturbs every numeric column **after** a cut
date, rebuilds features, and requires rows **at/before** the cut to be bit-identical.
Catches bfill / full-sample mean-std / center=True / negative shift / reversal — without
reading the implementation. Proven both ways:
- `assign_regime_labels` **passes** (confirms the C2 fix is causal).
- a deliberately leaky full-sample z-score **fails** (confirms the guard has teeth).

### Leakage L4 — static lint
`scan_lookahead_patterns(["core/*.py","adapters/*.py"])` finds banned constructs
(`shift(-…)`, `bfill`, `center=True`, `iloc[::-1]`). Current source is **clean**. The guard
module excludes itself (it names the patterns in docstrings).

---

## 4. Scope decisions

### 4.1 Auto-purge is opt-in (`cfg.use_lineage_purge`, default off)
Changing `purge_bars` changes which training bars are dropped → changes fold composition →
changes results. Defaulting it on would silently move every existing run's numbers. So it's
**off by default**; enabling it derives `purge_bars = max_lookback(graph)`. Capability +
test delivered; flip per-instrument when ready (careful-with-bugs posture).

### 4.2 Lineage is hand-declared JSON, not auto-extracted from code
The §13.6 hardening wants tests comparing declared inputs to columns actually read. That's a
static-analysis effort deferred; for now `validate_coverage` catches the cheaper failure
(a derived column with *no* record at all). Declarations live in `lineage/<family>.json`.

---

## 5. §13.12 acceptance — unchanged from P2 (P3 adds I4/leakage, orthogonal to the gate list)

P0–P3 now cover invariants **I1, I2, I4, I5, I6** + leakage guards L1–L5. Remaining: I3 is
partially audited (PIT contract clause + perturbation gate exist; a full PIT release-gate
wiring is light follow-up).

---

## 6. Remaining roadmap
- **P4** — cross-source reconciliation + observability. **Blocked**: needs a 2nd data feed
  (only yfinance/settlement today). Architecture is ready to activate when one lands.
- **P5** — HTML drill-down viewer over the CDC ledger + breaks (self-contained, doable now).
- **Audit bugs still open**: **C1** (no strategy P&L layer — the big redesign), **C3**
  (equity_options needs an option-chain provider — blocked), **H4** (option-premium
  validation — partially covered now by `logical_bounds_check` intrinsic/premium checks).
