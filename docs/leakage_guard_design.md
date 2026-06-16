# Janus — Look-Ahead Leakage Guard Design

> Scope: defense-in-depth to prevent non-causal (look-ahead) window leakage when
> vectorizing feature computation and during the upcoming data-structure redesign.
> Companion to: `docs/audit_findings_pre_data_structure.md` (see C2 grain confusion).
> Date: 2026-06-16

---

## Core principle

> Leakage occurs when an output at decision-time `t` changes if data at time `> t` changes.

Leakage is a function of **which inputs feed an output**, not **how you iterate**.
Vectorization changes the iteration mechanism, not the data dependency — so vectorizing
a leak-free row loop stays leak-free (bit-identical). The real risk is accidentally
replacing a causal op (`expanding`/`rolling`) with a full-sample op during a rewrite.

Two transform classes:

| Class | Examples | Cross-row dependency | Leak risk |
|-------|----------|----------------------|-----------|
| **Pointwise** | greeks, IV solve, pricing, d1/d2, intrinsic | none (row `i` only) | zero — vectorize freely |
| **Time-series** | rolling vol, expanding rank/regime, PSI, `pct_change` | yes | only if window non-causal |

Greeks are pointwise → safe. The guard below protects the time-series class.

---

## Five defense layers

### Layer 1 — Causal transform library (prevent at source)
Create `core/causal.py`. Feature code calls only through it; full-sample ops disappear
from the surface area.

```python
# core/causal.py — every op causal by construction
def causal_vol(r, window, min_periods=5):
    return r.rolling(window, min_periods=min_periods).std()

def causal_zscore(x, min_periods=20):
    mu = x.expanding(min_periods=min_periods).mean()
    sd = x.expanding(min_periods=min_periods).std()
    return (x - mu) / sd

def causal_rank(x, min_periods=20):            # replaces expanding().rank(pct=True)
    return x.expanding(min_periods=min_periods).apply(
        lambda w: (w <= w[-1]).mean(), raw=True)

# BANNED in feature code: x.mean(), x.std(), x.quantile() over a full series
```

Refactor target: `core/regime.py:42-43` (`expanding().rank()` currently runs over row
order) → call `causal_rank` on a **date-grain** series.

### Layer 2 — Grain gate (also fixes C2)
Windows may only run on a **date-grain, date-sorted, unique** series. One helper enforces it.

```python
def to_causal_series(df, col, date_col="as_of_date", agg="mean"):
    s = (df[[date_col, col]].dropna()
           .groupby(date_col)[col].agg(agg).sort_index())
    assert s.index.is_monotonic_increasing
    assert s.index.is_unique          # blocks many-row-per-date into rolling
    return s
```

Generalize the existing `_stability_series` (`run_pipeline.py:91`). Route every
rolling/expanding through this → the option-chain long table physically cannot enter a
window, killing the C2 grain bug.

### Layer 3 — Future-perturbation test (the real guard) ⭐
Catches **any** look-ahead regardless of implementation. Rule: perturb the future, the
past must not move.

```python
# tests/test_core/test_no_lookahead.py
import numpy as np, pandas as pd

def assert_no_lookahead(build_features, df, date_col="as_of_date", seed=0):
    """build_features: df -> feature DataFrame (same index)."""
    base = build_features(df)
    rng = np.random.default_rng(seed)
    dates = np.sort(df[date_col].unique())
    cut = dates[len(dates) // 2]                 # decision time t

    poisoned = df.copy()
    future = poisoned[date_col] > cut
    for c in poisoned.select_dtypes("number").columns:   # destroy the future
        poisoned.loc[future, c] *= rng.uniform(0.5, 1.5, future.sum())

    after = build_features(poisoned)
    past = df[date_col] <= cut
    pd.testing.assert_frame_equal(           # past features must be identical
        base.loc[past], after.loc[past], check_exact=False, atol=1e-9)
```

Run against every feature builder (regime, vol, vrp, psi) in CI, with several `cut`
values. Any change = fail. Catches `bfill`, full-sample mean/std, `center=True`,
negative `shift`, reversal — all of them, without reading the implementation.

### Layer 4 — Static lint (fast, pre-commit)
Scan source for known look-ahead patterns.

```python
import re
from glob import glob
from pathlib import Path

def test_no_forbidden_lookahead_calls():
    BANNED = [
        r"\.shift\(\s*-",                  # future shift
        r'method\s*=\s*["\']bfill', r"\.bfill\(",
        r"center\s*=\s*True",              # centered window
        r"\.iloc\[::-1\]",                 # reversal
    ]
    for f in glob("core/*.py") + glob("adapters/*.py"):
        src = Path(f).read_text(encoding="utf-8")
        for pat in BANNED:
            assert not re.search(pat, src), f"{f}: {pat}"
```

Cheap regression guard before the heavier CI test runs.

### Layer 5 — Lookback registry → auto purge (close the splitter loop)
Each feature declares its lookback. The pipeline takes the max and feeds `purge_bars`
automatically instead of guessing `max_dte` / 5.

```python
from dataclasses import dataclass

@dataclass
class FeatureSpec:
    window: str | None
    lookback: int
    causal: bool = True

FEATURES = {
    "vol_regime": FeatureSpec(window="vol_window", lookback=21),
    "vrp_sign":   FeatureSpec(window=None, lookback=0),
}
purge_bars = max(f.lookback for f in FEATURES.values())   # -> splitter cfg
```

Wire into `splitter.purge_embargo` (`core/splitter.py:85`). A feature's lookback **is**
the correct purge window by definition → train/val windows cannot overlap.

---

## Build order

| # | Layer | Effort | Payoff |
|---|-------|--------|--------|
| 1 | **L3** perturbation test | medium | catches existing leaks immediately — regime row-order bug fails on first run |
| 2 | **L2** grain gate | low | fixes C2 + blocks the main leak class |
| 3 | **L1** causal lib | low | new features safe by default |
| 4 | **L4** lint | very low | pre-commit guard |
| 5 | **L5** registry | medium | correct purge automatically |

Start with **L3**. Once written it will **fail immediately** on
`regime.assign_regime_labels` (vol_regime rolling over row order), proving both the C2
bug and the guard's value in one shot.

---

## Notes for the data-structure redesign
- Causal ops are valid **only** on the date-grain series. The two-grain split (daily_df vs
  chain_df) from the audit makes Layer 2 structural rather than a runtime assert.
- A feature's lookback (Layer 5) belongs in the feature's schema metadata, so the splitter
  reads purge windows from the data model instead of config guesses.
- Cross-sectional same-date pooling (e.g. IV surface across strikes on one date) is
  **contemporaneous**, not temporal leak — allowed, as long as no future date is touched.
