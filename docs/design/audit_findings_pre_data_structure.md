# Janus — Audit Findings (Pre Data-Structure Redesign)

> Scope: full repo read of `ingestion/`, `adapters/`, `core/`, `run_pipeline.py`, `configs/`.
> Purpose: surface vulnerabilities + weak spots before the next phase — analyzing and
> redesigning the pipeline data structure.
> Date: 2026-06-16

---

## Severity legend

| Tag | Meaning |
|-----|---------|
| 🔴 CRITICAL | Breaks correctness of results or a whole instrument family |
| 🟠 HIGH | Silent data-integrity loss or dead safety machinery |
| 🟡 MEDIUM | Wrong-but-survivable behavior, perf, or trust issue |
| ⚪ LOW | Local-only / cosmetic / doc |

---

## 🔴 Conceptual holes (these shape the data model)

### C1 — No strategy layer. Metrics validate the *market*, not a strategy.
- `return_std` = underlying daily return — `adapters/options_base.py:109-155`.
- Stage 4 feeds `return_std` straight into `metrics.risk_adjusted(...)` —
  `run_pipeline.py:284-289`. Reported Sharpe / Sortino / DSR are the **underlying's**
  numbers, not a strategy's.
- `fold_returns[i]` = per-date mean of `return_std` — `run_pipeline.py:268-278`.
  Every fold scores the same market return.
- Attribution waterfall only fires when `pnl` / `pnl_gross` columns exist
  (`run_pipeline.py:300`). **No adapter ever produces them** → dead branch.
- **Impact:** Stage 4 is placeholder until a strategy P&L exists.
- **Design need:** add `signal → position → pnl` layer. This is the central gap.

### C2 — Grain confusion: long option-chain table treated as per-date time series.
Options frame has many rows per `(as_of_date, strike, expiry)`. But:
- `core/regime.py:42-43` — `vol_regime` does
  `df[return_col].rolling(vol_window).std()` and `.expanding().rank()` over **row order**
  (mixes strikes/expiries), not per date. Window is meaningless.
- `core/regime.py:60` — `vrp_sign` uses per-row `df["vrp"]` (iv − vol per contract) →
  **many different regime labels inside one date**.
- Consequence: regime label is not well-defined per decision date;
  `splitter.regime_diversity_gate` (`core/splitter.py:162-181`) measures noise.
- **Design need:** explicit two-grain model — date-grain series vs contract-grain chain.

### C3 — `equity_options` family broken end-to-end.
- `configs/instruments/spx.yaml`: `family: equity_options` + `provider: yfinance`.
- `EquityLoaderA.fetch` returns spot bars only — no `expiry`/`right`/`strike`
  (`ingestion/equity_loader_a.py:72-82`).
- `EquityOptionsAdapter` calls `_require_option_chain_schema` → **raises**
  "input is not an option chain" (`adapters/options_base.py:72-90`).
- → `python run_pipeline.py --instrument spx` **crashes**. No provider produces equity
  option chains. README mislabels SPX as "equity". Only `bz` (settlement CSV) path runs.

---

## 🟠 Data-integrity weaknesses

### H1 — Caching dead + inconsistent.
- Legacy `ingestion/cache.py` was removed after this audit; `VersionedCache` is the
  remaining PIT-correct, immutable, manifest-backed cache implementation.
- `run_pipeline` still needs deeper versioned-cache wiring for provider reads.

### H2 — Symbology validation never enforced.
- `SettlementLoader` accepts a `Symbology` (`ingestion/settlement_loader.py:45-46`) but
  `fetch()` **never calls** `resolve` / `validate_no_orphans` / `validate_uniqueness`.
- Orphan `product_id` flows in silently. Map has only 3 ids.

### H3 — `validate_schema` checks columns, not dtypes.
- `ingestion/base.py:66-72` only checks presence. RAW_SCHEMA tz-aware/int declarations
  unenforced. `timestamp` set to scalar `None` (`ingestion/settlement_loader.py:88`),
  not the declared `datetime64[ns, UTC]`. Silent type drift into adapters.

### H4 — Option premium never validated.
- `_attach_underlying_futures` overwrites `df["price_std"] = underlying` for all rows
  (`adapters/futures_options_adapter.py:164`).
- `logical_bounds_check` uses `price_col=price_std`=underlying (`core/validators.py:37`)
  → checks underlying > 0. **Option premium `<= 0` never flagged.**
- `outlier_cap` skips option rows (`core/validators.py:152-153`). Bad premiums reach the
  IV solver unchecked.

### H5 — DSR trial count is fake.
- `n_trials = cfg.get("n_trials", 40)` constant (`run_pipeline.py:286`) — not the number
  of strategies actually tried. Deflated Sharpe is untrustworthy.

---

## 🟡 Medium

- **M1 — `net_change_flag` not initialized False.** Column absent unless some row is bad —
  `ingestion/settlement_loader.py:95-105`. Schema drift.
- **M2 — Underlying return picks arbitrary contract.** `groupby(as_of_date).first()` on
  mixed delivery months — `adapters/options_base.py:119-133`.
- **M3 — `outlier_cap` per-row `.apply(axis=1)` loop** — `core/validators.py:172-174`.
  Slow on large chains.
- **M4 — `compute_skew` returns placeholder 0.0** — `adapters/options_base.py:354-365`.
  `skew_direction` axis is dead; adapters drop it but `bz.yaml` / `spx.yaml` still list it.

---

## ⚪ Low

- **L1 — HTML report XSS-ish (local only).** JSON injected via
  `.replace("__DATA_JSON__", ...)` + `innerHTML` — `core/reporting.py:466,622`.
  Breaks/injects if a data string contains `</script>`. Mostly numeric data → low.
- **L2 — `run_id` unsanitized** → `outputs/{run_id}_...` path write
  (`run_pipeline.py:350`). Local CLI, low.
- **L3 — Doc mismatch.** README says html `<run_id>_stability.html`; code writes
  `<run_id>_report.html` (`core/reporting.py:467`). README lists SPX as "equity"; config
  is `equity_options`.

---

## Priorities for the data-structure redesign

1. **Add the missing layer:** `signal_df` (per date/contract) → `position_df` → `pnl_df`.
   Without it, Stage 4 metrics are theater. (C1)
2. **Split grains explicitly:** `daily_df` (date-indexed: return, vol, regime, vrp) vs
   `chain_df` (contract-indexed). Stop computing rolling/regime over the long table. (C2)
3. **Single PIT cache contract:** keep `VersionedCache`, drop `Cache`, wire it in. (H1)
4. **Enforce schema dtypes + symbology** at the ingestion boundary, not just column
   names. (H2, H3)
5. **Move option-premium validation** onto the actual premium column, decoupled from the
   underlying overwrite. (H4)
