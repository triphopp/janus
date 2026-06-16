# Janus — P5 Implementation Report (HTML Diff Viewer)

> Phase P5 of `Memory/plans/data_ops_architecture.md` §12. The analyst drill-down surface
> behind the CDC ledger + break ledger.
> Date: 2026-06-16 · Status: **implemented, 187/187 tests pass** (+3 over P3)

---

## 1. What shipped

| File | Role |
|------|------|
| `core/diff_report.py` | **new** — `write_diff_html(records, breaks, run_id)` → self-contained HTML |
| `run_pipeline.py` | writes `outputs/diff/<run_id>_diff.html` each run; `summary.cdc.diff_html` |
| `tests/test_core/test_diff_report.py` | 3 tests (render, XSS escaping, breaks panel) |

---

## 2. What it does

A single self-contained HTML file (no server, no dependencies) — the ChangeRecords + breaks
are embedded as JSON, rendered client-side with vanilla JS. GitHub-style:

- **Stage tabs / filters**: stage transition, column, reason, `|Δ| ≥` threshold.
- **Changed-row view**: each row shows the business key, `before → after`, signed `Δ` + `%`,
  and a reason chip. Colour-coded: 🟨 cell_mod · 🟥 row_drop · 🟩 row_add · schema.
- **UNATTRIBUTED-first toggle**: jump straight to the silent-bug bucket (red chip).
- **Breaks panel**: severity-coloured break table (id, severity, type, stage, field,
  before→after, status).

The analyst opens it, filters `UNATTRIBUTED` or `|Δ| ≥ 5`, and sees a handful of suspect
rows instead of scrolling a 40k-row CSV — the original "better than CSV" goal.

---

## 3. Security

The audit (L1) flagged the existing `reporting.py` HTML path as XSS-ish (`innerHTML` +
string replace). This viewer hardens that: data is JSON-encoded and `<`/`>` are escaped to
`<`/`>` before embedding, so a data value containing `</script>` cannot break out
of the embedded block. Proven by `test_script_injection_is_escaped`. Built via placeholder
replacement (not f-string/`.format`) so the JS/CSS braces stay literal — no template
injection surface.

---

## 4. Status — institutional roadmap

| Phase | Status |
|-------|--------|
| P0 Contracts + quarantine | ✅ |
| P1 Bitemporal + manifest | ✅ |
| P2 CDC + breaks | ✅ |
| P3 Lineage + leakage guards | ✅ |
| **P4 Cross-source recon + observability** | ⛔ **blocked** — needs a 2nd data feed |
| P5 HTML diff viewer | ✅ |

**P4 is the only roadmap phase not built**, and only because reconciliation is meaningless
with a single feed (yfinance/settlement). `core/reconcile.py` (generalize
`validate_provided_iv`) + observability dashboards activate the day a second source lands —
no restructure needed (the architecture was designed for it).

Remaining audit bugs outside this roadmap: **C1** (no strategy P&L layer — large redesign),
**C3** (equity_options needs an option-chain provider — blocked on data), **H4** (option
premium validation — now largely covered by `logical_bounds_check` premium/intrinsic checks).
