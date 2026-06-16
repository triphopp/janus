"""Look-ahead leakage guards (P3 — leakage_guard_design.md L3 + L4).

Core principle: leakage occurs when an output at decision-time ``t`` changes if data at
time ``> t`` changes. Leakage is a property of *which inputs feed an output*, not of how
you iterate — so vectorizing a leak-free loop stays leak-free; the risk is silently
swapping a causal op for a full-sample one.

L3 — future-perturbation test (the real guard): perturb the future, the past must not move.
     Catches bfill, full-sample mean/std, center=True, negative shift, reversal — without
     reading the implementation.
L4 — static lint: scan source for known look-ahead call patterns (cheap pre-commit guard).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd


def assert_no_lookahead(
    build_features: Callable[[pd.DataFrame], pd.DataFrame],
    df: pd.DataFrame,
    *,
    date_col: str = "as_of_date",
    seed: int = 0,
    atol: float = 1e-9,
    cut=None,
) -> None:
    """Assert ``build_features`` uses no future information.

    Perturbs every numeric column strictly after a cut date, rebuilds features, and
    requires the features on rows at/before the cut to be bit-identical. Raises
    AssertionError on any change.

    build_features: df -> feature DataFrame aligned to df.index.
    """
    base = build_features(df)
    rng = np.random.default_rng(seed)
    dates = np.sort(pd.to_datetime(df[date_col]).unique())
    if len(dates) < 4:
        raise ValueError("need >= 4 distinct dates to test look-ahead")
    cut = pd.Timestamp(cut) if cut is not None else pd.Timestamp(dates[len(dates) // 2])

    poisoned = df.copy()
    future = pd.to_datetime(poisoned[date_col]) > cut
    n_future = int(future.sum())
    if n_future == 0:
        raise ValueError("cut leaves no future rows to perturb")
    for c in poisoned.select_dtypes("number").columns:
        poisoned[c] = poisoned[c].astype(float)  # avoid int→float dtype clash on assignment
        poisoned.loc[future, c] = (
            poisoned.loc[future, c] * rng.uniform(0.5, 1.5, n_future)
        )

    after = build_features(poisoned)
    past = pd.to_datetime(df[date_col]) <= cut

    base_past = base.loc[past.values]
    after_past = after.loc[past.values]

    # numeric columns compared with tolerance; non-numeric compared exactly
    for col in base_past.columns:
        b, a = base_past[col], after_past[col]
        if pd.api.types.is_numeric_dtype(b):
            if not np.allclose(b.fillna(0).to_numpy(), a.fillna(0).to_numpy(), atol=atol, equal_nan=True):
                raise AssertionError(f"look-ahead leak: past values of '{col}' moved when future changed")
        else:
            if not b.astype(str).equals(a.astype(str)):
                raise AssertionError(f"look-ahead leak: past labels of '{col}' moved when future changed")


# ── L4 static lint ────────────────────────────────────────────────────────────

BANNED_PATTERNS = [
    (r"\.shift\(\s*-", "future shift (negative)"),
    (r"\.bfill\(", "backward fill"),
    (r"method\s*=\s*['\"]bfill['\"]", "backward fill via method="),
    (r"method\s*=\s*['\"]backfill['\"]", "backfill via method="),
    (r"center\s*=\s*True", "centered window (sees future)"),
    (r"\.iloc\[::-1\]", "row reversal"),
]


def scan_lookahead_patterns(globs: list[str], exclude: tuple[str, ...] = ("leakage.py",)) -> list[dict]:
    """Scan source files for banned look-ahead patterns. Returns a list of hits.

    ``exclude`` skips files whose path contains any of the given substrings — by default
    this guard module itself, which legitimately names the patterns in its docstrings.
    """
    hits: list[dict] = []
    compiled = [(re.compile(pat), why) for pat, why in BANNED_PATTERNS]
    for g in globs:
        for path in Path().glob(g):
            if any(ex in str(path) for ex in exclude):
                continue
            try:
                src = path.read_text(encoding="utf-8")
            except Exception:
                continue
            for lineno, line in enumerate(src.splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                for rx, why in compiled:
                    if rx.search(line):
                        hits.append({"file": str(path), "line": lineno, "pattern": why, "text": line.strip()})
    return hits
