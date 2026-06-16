"""Causal time-series helpers and PIT timing guards.

Feature code should route cross-row computations through this module so grain
and point-in-time assumptions are explicit at the call site.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def causal_vol(r: pd.Series, window: int, min_periods: int = 5) -> pd.Series:
    """Rolling volatility anchored at each observation."""
    return _require_ordered_series(r).rolling(window, min_periods=min_periods).std()


def causal_zscore(x: pd.Series, min_periods: int = 20) -> pd.Series:
    """Expanding z-score using only observations available at each row."""
    x = _require_ordered_series(x)
    mu = x.expanding(min_periods=min_periods).mean()
    sd = x.expanding(min_periods=min_periods).std()
    return (x - mu) / sd


def causal_rank(x: pd.Series, min_periods: int = 20) -> pd.Series:
    """Expanding percentile rank of the current value within the past window."""
    x = _require_ordered_series(x)
    return x.expanding(min_periods=min_periods).apply(
        lambda w: np.nan if len(w) == 0 else np.mean(w <= w[-1]),
        raw=True,
    )


def to_causal_series(
    df: pd.DataFrame,
    col: str,
    date_col: str = "as_of_date",
    agg: str = "mean",
) -> pd.Series:
    """Return a date-sorted, date-unique series for time-series transforms.

    Long option-chain tables are collapsed to one value per date before any
    rolling/expanding computation. The aggregation method is intentionally
    explicit because date-level features should not silently depend on row order.
    """
    if col not in df.columns:
        raise KeyError(f"missing value column: {col}")
    if date_col not in df.columns:
        values = pd.to_numeric(df[col], errors="coerce").dropna()
        return _require_ordered_series(values)

    tmp = pd.DataFrame({
        date_col: pd.to_datetime(df[date_col], errors="coerce"),
        col: pd.to_numeric(df[col], errors="coerce"),
    }).dropna()
    if tmp.empty:
        return pd.Series(dtype=float, name=col)

    grouped = tmp.groupby(date_col, sort=True)[col]
    if agg == "median":
        out = grouped.median()
    elif agg == "first":
        out = grouped.first()
    elif agg == "last":
        out = grouped.last()
    elif agg == "max":
        out = grouped.max()
    elif agg == "min":
        out = grouped.min()
    elif agg == "any":
        out = grouped.max()
    else:
        out = grouped.mean()

    out = out.sort_index()
    if not out.index.is_unique:
        raise ValueError(f"{date_col} aggregation did not produce unique dates")
    if not out.index.is_monotonic_increasing:
        raise ValueError(f"{date_col} series is not sorted")
    return out.rename(col)


def broadcast_by_date(
    df: pd.DataFrame,
    values: pd.Series,
    date_col: str = "as_of_date",
) -> pd.Series:
    """Broadcast a date-indexed series back to the original DataFrame rows."""
    if date_col not in df.columns:
        return values.reindex(df.index)
    dates = pd.to_datetime(df[date_col], errors="coerce")
    return dates.map(values)


def validate_pit_timing(
    df: pd.DataFrame,
    *,
    as_of_col: str = "as_of_date",
    available_col: str = "available_at",
    decision_col: str = "decision_time",
    execution_col: str | None = None,
    label_end_col: str | None = None,
) -> bool:
    """Validate point-in-time ordering for decision data.

    Raises ValueError when data would be unknowable at decision time or when
    downstream execution/label timing runs backward.
    """
    required = [as_of_col, available_col, decision_col]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"PIT timing requires columns: {', '.join(missing)}")

    as_of = _to_utc(df[as_of_col])
    available = _to_utc(df[available_col])
    decision = _to_utc(df[decision_col])

    _raise_if((as_of > available).fillna(False), "as_of_date_after_available_at")
    _raise_if((available > decision).fillna(False), "available_at_after_decision_time")

    if execution_col and execution_col in df.columns:
        execution = _to_utc(df[execution_col])
        _raise_if((decision > execution).fillna(False), "decision_time_after_execution_time")

        if label_end_col and label_end_col in df.columns:
            label_end = _to_utc(df[label_end_col])
            _raise_if((execution > label_end).fillna(False), "execution_time_after_label_end_time")
    elif label_end_col and label_end_col in df.columns:
        label_end = _to_utc(df[label_end_col])
        _raise_if((decision > label_end).fillna(False), "decision_time_after_label_end_time")

    return True


def _require_ordered_series(series: pd.Series) -> pd.Series:
    out = pd.to_numeric(series, errors="coerce")
    if isinstance(out.index, pd.DatetimeIndex) and not out.index.is_monotonic_increasing:
        raise ValueError("causal series index must be monotonic increasing")
    return out


def _to_utc(values) -> pd.Series:
    return pd.Series(pd.to_datetime(values, errors="coerce", utc=True))


def _raise_if(mask: pd.Series, reason: str) -> None:
    if mask.any():
        examples = mask[mask].index[:5].tolist()
        raise ValueError(f"PIT timing violation: {reason}; example rows={examples}")

