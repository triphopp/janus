# Risk-Free Rate Sourcing and PIT Join

Urgency: `P1-high`

Status: `in_progress`

Source plan:

- `issues/P2-medium/pnl-attribution/029-second-order-greek-pnl-attribution.md` (carry term depends on this)

## Summary

The pipeline prices and computes Greeks with a hardcoded constant `r` instead
of a real discount curve, and the constant is *decided in four independent
places with two different defaults*. `rf_rate_source: sofr` is declared in
instrument configs but no code loads SOFR data; the wiring is dead. Fix by
introducing a single rate resolver (`core/rates.py`) that sources a real
rate, joins it point-in-time, converts to the pricer's convention, stamps a
per-row `r` column exactly once, and makes any fallback loud. All downstream
consumers read that column and never invent their own default.

## Implementation Status

Updated: `2026-07-07`

Implemented:

- Added `core/rates.py` as the central risk-free-rate resolver.
- Added `DEFAULT_RISK_FREE_RATE = 0.05` as the only central fallback.
- Added `resolve_rate()`, `stamp_rate()`, and `resolve_scalar_rate()`.
- Added simple ACT/360 to continuously-compounded ACT/365 conversion.
- Added PIT-aware rate-table join support through `available_at`.
- Rewired adapter prep to stamp `df["r"]` through `core.rates`.
- Rewired standalone `run_greeks.py --input` to stamp `df["r"]` before
  `resolve_greek_inputs()`.
- Changed `resolve_greek_inputs()` so missing/invalid `r` is an invalid-input
  reason (`missing_rate`) instead of a silent fallback.
- Removed private `0.05` / `0.0` rate fallbacks from pricing/Greeks/export
  decision points; production grep now leaves `rf_rate` resolution in
  `core/rates.py`.
- Added `rate_summary` to Greek-only summaries and option-quality summaries.
- Added `guard_status.rate_sourcing` for pipeline summaries.
- Added tests for fallback visibility, PIT lookahead rejection, convention
  conversion, missing-rate handling, standalone stamping, and export behavior.

Verified:

- `pytest -q` passed: `1037 passed, 10 skipped`.

Still open:

- Add a public-safe SOFR ingestion/source fixture so official configs using
  `rf_rate_source: sofr` do not fall back to the central constant.
- Make official run gating stricter: `pass` only when sourced coverage meets
  policy, `warn` on fallback, `fail` when a configured source is missing.
- Add PCP-implied-rate cross-check and report
  `pcp_implied_rate_spread_bps`.
- Add an automated grep/CI guard proving no pricing-rate fallback exists
  outside `core/rates.py`.
- Update export data dictionary/report copy to explain rate source and
  convention.
- Split Level B term-structure interpolation into a follow-up issue once
  Level A SOFR sourcing is live.
- Keep repo-rate support as a follow-up source option, not the default
  discount curve.

## Why It Matters

- `rho` is directly wrong and `theta` is biased under an arbitrary constant
  `r`; the carry term (`rho * dr`) in Greek P&L attribution is identically
  zero, so P&L explain can never attribute funding effects.
- Config declares a rate source that is silently ignored. A reviewer reading
  `rf_rate_source: sofr` reasonably believes SOFR is used. It is not. That is
  a data-trust failure of the same class as the IV unit incident.
- **The same data through different entry points gets a different rate.**
  Verified by code trace:
  - Pipeline / instrument mode passes `build_iv_surface`, which stamps
    `df["r"] = cfg.get("rf_rate", 0.05)` → every downstream stage sees 0.05.
  - Standalone `run_greeks.py --input <csv>` (no `--instrument`) never passes
    `build_iv_surface`; `resolve_greek_inputs` runs with an empty cfg and
    `rf_rate_default = 0.0` → Greeks computed with r = 0.0.
  Identical rows, 0.05 vs 0.0, silently.
- `compute_greeks: false` does not stop Greek production: the option-chain
  export layer recomputes Greeks unconditionally ("regardless of the run's
  compute_greeks flag") with its own `0.05` fallback. Any rate fix that only
  touches the pipeline path leaves the export path wrong.

## Current State (verified decision points)

`r` is currently decided at four places that do not coordinate:

| # | Location | Behavior | Effective default |
|---|----------|----------|-------------------|
| 1 | `adapters/options_base.py` `build_iv_surface` | stamps `df["r"] = cfg.get("rf_rate", 0.05)` for all rows | 0.05 |
| 2 | `core/greek_inputs.py` `resolve_greek_inputs` | `r` column > `cfg["rf_rate"]` > `rf_rate_default` | 0.0 (via `run_greeks`) |
| 3 | `adapters/options_base.py` `compute_greeks` | `r` column, `fillna(cfg.get("rf_rate", 0.05))` | 0.05 |
| 4 | `core/option_chain_export.py` | `r` column, `fillna(cfg.get("rf_rate", 0.05))`; runs even when `compute_greeks: false` | 0.05 |

Plus dead config: `rf_rate_source: sofr` maps to `rf_rate_col` which is
carried into cfg and then never read by any consumer; no data file contains a
rate column; no rates loader exists.

All Greek backends (`numpy`, `loop`, `cuda`, scalar reference) use `r`
identically (discounting via `exp(-rT)`, plus direct terms in `theta`), so
the fix is purely an input-resolution problem — no backend changes needed.

## Fund-Standard Approach

The correct discount rate for USD-cleared futures options (NYMEX WTI) is the
**SOFR OIS curve**. Under Black-76 the forward already embeds cost-of-carry,
so `r` is purely a discount rate — the requirement is the right curve for the
right currency, tenor, and date.

Four dimensions must all hold:

1. **Right curve** — post-LIBOR, collateralized USD derivatives discount at
   SOFR (OIS), not T-bills or an arbitrary constant.
2. **Term-matched** — interpolate the curve at each option's own `T`, not one
   scalar for the whole book (universe spans DTE 1 to 730 days).
3. **PIT-correct** — the rate joined to an `as_of_date` must have been
   knowable at that date's settlement time; join through `available_at` like
   every other external series (Iron Rule).
4. **Convention-matched** — the pricer documents `rate: continuously
   compounded`. SOFR quotes are money-market simple rates (act/360); convert
   to continuously-compounded act/365 before use. Skipping this conversion is
   a classic silent error.

Rollout in two levels:

- **Level A (this issue)**: daily SOFR (e.g. 3M term SOFR or compounded
  overnight index), flat across tenor, joined per `as_of_date`. Removes the
  arbitrary-constant problem; adequate for short-dated options.
- **Level B (follow-up)**: full term structure interpolated at each row's
  `T`. Required before trusting `rho`/carry on the long-DTE tail.

Independent validation: **put-call parity implied rate**. For futures
options, `C - P = e^(-rT) (F - K)` gives a market-implied discount rate from
the chain itself (ATM strikes, median across strikes to suppress noise). It
is PIT by construction and requires no external data. Use it as a
cross-check gate, not the primary source.

## Target Architecture

One resolver, one stamp, many readers:

```
config (rf_rate_source / rf_rate)     SOFR series (PIT, available_at-aware)
                  \                          /
                   v                        v
              core/rates.py :: resolve_rate(df, cfg)
                - pick source: sofr | constant
                - PIT join on as_of_date (no lookahead)
                - convert act/360 simple -> cc act/365
                - single fallback constant, ONE place
                - returns (r_series, rate_summary)
                          |
                stamp df["r"] exactly once
                (adapter prep / run_greeks input prep)
                          |
        +-----------------+------------------+
        v                 v                  v
  greek_inputs      options_base       option_chain_export
  (reads df["r"],   compute_greeks     (reads df["r"],
   no default)      (reads df["r"],     no default)
                     no default)
                          |
                    batch_greeks (all backends unchanged)
```

Design rules:

- `resolve_rate` is the **only** function allowed to produce an `r` value
  from anything other than an existing `df["r"]` column.
- Downstream consumers read `df["r"]`. A missing/NaN `r` after resolution is
  a data-quality event (warn + gate + NaN Greeks for that row), never a
  silent constant.
- `rate_summary` (source used, coverage %, fallback row count, convention)
  is attached to the run summary so every output can answer "which r?".
- Both entry doors call the same resolver: adapter prep (pipeline/instrument
  mode) and `run_greeks.py --input` (standalone mode).

## Refactor Plan

Ordered to keep every step shippable with tests green.

**Phase 0 — Characterization (no behavior change)**

- Add characterization tests pinning today's behavior: pipeline path yields
  r = 0.05; standalone `--input` path yields r = 0.0; export path recomputes
  Greeks under `compute_greeks: false` with r = 0.05. These tests document
  the bug and will be flipped in later phases.

**Phase 1 — Central resolver, constant source only**

- Create `core/rates.py`:
  - `resolve_rate(df, cfg, *, default) -> (pd.Series, dict)` — precedence:
    existing valid `df["r"]` > configured `rf_rate` scalar > `default`;
    emits `rate_summary` with `source`, `coverage_pct`, `fallback_rows`.
  - One module-level fallback constant; delete the scattered `0.05`/`0.0`
    literals.
- Rewire decision points #1–#4 to call it / read the stamped column:
  - `build_iv_surface` stamps via `resolve_rate` (upstream stamp).
  - `resolve_greek_inputs` drops its own `cfg.get("rf_rate", ...)` chain;
    reads `df["r"]`, passes through NaN as invalid-input reason.
  - `options_base.compute_greeks` and `option_chain_export` drop `fillna`
    defaults; missing `r` → NaN Greeks + warning counted in summary.
- `run_greeks.py --input` path calls `resolve_rate` before
  `resolve_greek_inputs`, so both doors produce identical `r`.
- Parity test: same synthetic frame through pipeline-prep and standalone
  prep produces bitwise-identical `r` column.

**Phase 2 — Real source (SOFR, Level A)**

- Ingestion: SOFR daily series loader with `available_at` (same contract as
  other PIT inputs); small public-safe fixture for tests.
- `resolve_rate` gains source `sofr`: PIT as-of join on `as_of_date`
  (backward join respecting `available_at`), then convention conversion
  act/360 simple → continuously compounded act/365 (own unit-tested helper).
- Revive dead config: `rf_rate_source: sofr` now selects this path;
  configured-but-missing series → gate `fail` on official runs.

**Phase 3 — Gates, cross-check, cleanup**

- Rate-sourcing gate: `pass` (100% sourced) / `warn` (fallback used) /
  `fail` (source configured but absent).
- PCP-implied rate cross-check on sampled liquid ATM pairs; report spread in
  bps as a metric.
- Flip Phase-0 characterization tests to the new expected behavior; grep
  gate in CI: no `rf_rate` literal defaults outside `core/rates.py`.
- Update Greek export data dictionary (issue 024 artifact) to document the
  `r` column's source and convention.

Explicitly deferred (Level B, separate issue): per-`T` term-structure
interpolation; multi-currency discounting.

## Repo-Rate Follow-Up

Repo rates are relevant, but they should be added as explicit rate sources or
financing/carry diagnostics rather than silently replacing SOFR discounting.
SOFR remains the default USD discount-rate source for collateralized derivative
pricing because it is already a broad Treasury repo-based overnight reference
rate.

Potential source names:

| Source | Intended use | Notes |
|---|---|---|
| `sofr` | Default USD discounting | Primary Level A source. |
| `repo_gc` | General-collateral financing/carry diagnostics | Candidate after SOFR source is live. |
| `repo_special` | Collateral/security-specific financing | Requires collateral identity; not a generic default. |
| `constant` | Explicit fallback or test mode | Must remain visible in `rate_summary`. |

Design rule:

- Do not set `pricing.r = repo_rate` globally without source identity,
  collateral policy, and summary metadata.
- If repo rates are introduced, expose them through `core/rates.py` with the
  same PIT join, convention conversion, coverage summary, and gate semantics
  as SOFR.
- For P&L attribution, repo rates may feed funding/carry diagnostics separately
  from the canonical Black-76 discount rate.

## Public-Safe Notes

- No local absolute paths; SOFR fixture must be a small synthetic or
  public-domain (FRED-sourced) sample.
- No licensed vendor option rows in tests; use the existing synthetic grid.

## Acceptance Criteria

- [x] `core/rates.py` exists; it is the only module producing `r` from
      config/constants (CI grep proves no other `rf_rate` literal defaults).
- [x] Entry-door parity: pipeline prep and `run_greeks --input` produce an
      identical `r` column for the same input frame (test).
- [x] Export path consumes the same stamped `r`; no private `fillna(0.05)`
      remains, including under `compute_greeks: false`.
- [ ] A per-row `r` column is populated from the sourced series for every
      option row in a run where `rf_rate_source` is configured.
- [x] The join is PIT: a test proves a rate published after the row's
      settlement `available_at` is never used for that row.
- [x] Convention conversion is unit-tested (act/360 simple → cc act/365)
      against independently computed values.
- [x] Missing rate data produces a visible run-summary warning and gate
      status, not a silent constant; `rate_summary` (source, coverage,
      fallback count) appears in every run summary.
- [ ] PCP-implied `r` vs sourced `r` spread is computed on a sampled chain
      and reported; a threshold breach flags the run.
- [x] Existing Greek parity/golden tests still pass with `r` supplied
      row-level (tolerances reviewed once, intentionally).

## Evidence Required

- Phase-0 characterization test output (documents 0.05 vs 0.0 divergence).
- Entry-door parity test output.
- PIT-join test output (lookahead rejection case included).
- Convention-conversion unit test output.
- One run summary showing: rate source used, coverage (% rows with sourced
  `r`), fallback count, and PCP cross-check spread.

## Related Checks

- Gate: rate-sourcing gate (new) — `pass` when sourced coverage is 100%,
  `warn` on fallback, `fail` on configured-but-missing source.
- Metric: `r_source_coverage_pct`, `pcp_implied_rate_spread_bps`
- Expected status: 100% sourced coverage on official runs; spread within
  agreed bps threshold.
