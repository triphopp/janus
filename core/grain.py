"""Grain separation — date-grain market series vs contract-grain option chains.

Issue 012. An option chain has many rows per trade date (one per strike/expiry). A
rolling / expanding / pct_change / diff calculation over that long frame silently
mixes strikes and expiries, producing regimes, VRP, and fold metrics that are not
well-defined for a decision date. Date-level features must be computed on a frame
with exactly one row per decision date.

This module makes the grain explicit and enforceable:

- ``infer_grain`` / ``is_mixed_grain`` classify a frame.
- ``require_date_grain`` rejects a time-series op on a mixed-grain frame.
- ``to_date_grain`` is the *only* sanctioned reduction from contract-grain to
  date-grain, and it is order-independent and future-truncation stable by
  construction (groupby on the decision date).
- ``declare_feature_grain`` records the grain + selection rule a feature relies on.

Pure pandas; no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import pandas as pd

GRAIN_DATE = "date"          # one row per decision date
GRAIN_CONTRACT = "contract"  # one row per option contract per date
GRAIN_MIXED = "mixed"        # multiple rows per date (an unreduced option chain)


class MixedGrainError(ValueError):
    """Raised when a date-grain time-series op is attempted on a mixed-grain frame."""


def rows_per_date(df: pd.DataFrame, date_col: str = "as_of_date") -> int:
    """Max number of rows sharing a single decision date (0 if no date column)."""
    if date_col not in df.columns or df.empty:
        return 0
    return int(df.groupby(pd.to_datetime(df[date_col])).size().max())


def infer_grain(df: pd.DataFrame, date_col: str = "as_of_date") -> str:
    """Classify a frame's grain by how many rows share a decision date."""
    n = rows_per_date(df, date_col)
    if n == 0:
        return GRAIN_DATE
    return GRAIN_DATE if n <= 1 else GRAIN_MIXED


def is_mixed_grain(df: pd.DataFrame, date_col: str = "as_of_date") -> bool:
    return infer_grain(df, date_col) == GRAIN_MIXED


def require_date_grain(
    df: pd.DataFrame, op_name: str, date_col: str = "as_of_date"
) -> None:
    """Raise if a date-level time-series op would run on a mixed-grain frame.

    Rolling / expanding / regime / fold code must call this before operating, so a
    contract-grain option chain cannot be fed to a date-indexed calculation.
    """
    if is_mixed_grain(df, date_col):
        n = rows_per_date(df, date_col)
        raise MixedGrainError(
            f"{op_name!r} requires date-grain input (one row per {date_col}); got a "
            f"mixed-grain frame with up to {n} rows per date. Reduce with "
            "core.grain.to_date_grain() and declare the selection rule first."
        )


def to_date_grain(
    df: pd.DataFrame,
    value_cols: Iterable[str],
    *,
    date_col: str = "as_of_date",
    agg: str = "mean",
) -> pd.DataFrame:
    """Reduce a contract-grain frame to one row per decision date.

    This is the sanctioned bridge from contract-grain to date-grain. Because it
    groups by the decision date, the result is independent of row order (same-date
    shuffle invariant) and unaffected by rows on later dates (future-truncation
    invariant).
    """
    cols = [c for c in value_cols if c in df.columns]
    if date_col not in df.columns:
        raise ValueError(f"frame missing date column {date_col!r}")
    tmp = pd.DataFrame({date_col: pd.to_datetime(df[date_col])})
    for c in cols:
        tmp[c] = pd.to_numeric(df[c], errors="coerce")
    grouped = tmp.groupby(date_col)[cols]
    reduced = grouped.median() if agg == "median" else grouped.mean()
    return reduced.sort_index().reset_index()


@dataclass(frozen=True)
class FeatureGrain:
    """A feature's declared grain + how it selects rows within a date.

    VRP, skew, and term-structure features must declare a selection rule (e.g.
    "atm", "front_month") so a reviewer can see the contract a date-level value
    came from rather than an implicit, order-dependent pick.
    """
    feature: str
    grain: str
    selection_rule: Optional[str] = None

    def __post_init__(self):
        if self.grain not in (GRAIN_DATE, GRAIN_CONTRACT):
            raise ValueError(f"unknown grain {self.grain!r}")
        if self.grain == GRAIN_DATE and not self.selection_rule:
            raise ValueError(
                f"date-grain feature {self.feature!r} must declare a selection_rule "
                "(e.g. 'atm', 'front_month') describing how it reduces the chain"
            )

    def as_dict(self) -> dict:
        return {
            "feature": self.feature,
            "grain": self.grain,
            "selection_rule": self.selection_rule,
        }


def declare_feature_grain(
    feature: str, grain: str, selection_rule: Optional[str] = None
) -> dict:
    """Build a feature grain declaration (validates the date-grain selection rule)."""
    return FeatureGrain(feature, grain, selection_rule).as_dict()
