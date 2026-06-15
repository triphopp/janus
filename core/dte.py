"""DTE (Days-To-Expiry) — single source of truth for calendar conventions.

v1.3: Every adapter must use this module for DTE calculation.
No adapter may compute DTE independently.

Supports:
- basis: calendar | trading
- day_count: act_365 | act_360 | bus_252
- expiry inclusive/exclusive
- expiry cutoff: settlement (EOD) | open_next_day
"""

from datetime import datetime, timedelta
from typing import Optional, Union

import numpy as np
import pandas as pd


def compute_dte(
    asof: Union[str, datetime, pd.Timestamp],
    expiry: Union[str, datetime, pd.Timestamp],
    cfg: dict,
    holidays: Optional[list] = None,
) -> float:
    """Compute DTE between asof date and expiry date.

    Args:
        asof: as-of date (the date we are standing at)
        expiry: option expiration date
        cfg: DTE config dict with keys:
            - basis: 'calendar' | 'trading'
            - day_count: 'act_365' | 'act_360' | 'bus_252'
            - exclude_expiry_date: bool (True = expiry day counts as DTE 0)
            - expiry_cutoff: 'settlement' | 'open_next_day' (not yet implemented)
        holidays: list of holiday dates for trading-day basis

    Returns:
        DTE as float (in years, for option pricing).
        NaN if asof > expiry (post-expiry).
        0 if asof == expiry and exclude_expiry_date is True.

    Examples:
        compute_dte("2024-09-25", "2024-11-01", {"basis":"calendar","day_count":"act_365",
                      "exclude_expiry_date":False})  → 37/365
        compute_dte("2024-09-25", "2024-11-01", {"basis":"trading","day_count":"bus_252",
                      "exclude_expiry_date":False})  → ~26/252
    """
    asof = pd.Timestamp(asof)
    expiry = pd.Timestamp(expiry)

    if asof > expiry:
        return np.nan  # post-expiry — no DTE

    basis = cfg.get("basis", "calendar")
    day_count = cfg.get("day_count", "act_365")
    exclude_expiry = cfg.get("exclude_expiry_date", False)

    if basis == "calendar":
        delta = (expiry - asof).days
        if exclude_expiry:
            delta = max(0, delta)
        else:
            delta = delta  # include expiry day → DTE includes expiry

    elif basis == "trading":
        # Count business days between asof and expiry
        delta = _count_business_days(asof, expiry, holidays or [])
        if not exclude_expiry:
            # expiry is a trading day → include it unless excluded
            if _is_business_day(expiry, holidays or []):
                delta += 1

    else:
        raise ValueError(f"Unknown DTE basis: {basis}")

    # Convert days → years
    days_per_year = {
        "act_365": 365.0,
        "act_360": 360.0,
        "bus_252": 252.0,
    }
    days = days_per_year.get(day_count, 365.0)

    return delta / days


def _count_business_days(start: pd.Timestamp, end: pd.Timestamp, holidays: list) -> int:
    """Count business days in [start, end)."""
    if start >= end:
        return 0
    holiday_set = {pd.Timestamp(h).date() for h in holidays}
    count = 0
    current = start.date()
    end_date = end.date()
    while current < end_date:
        if current.weekday() < 5 and current not in holiday_set:
            count += 1
        current += timedelta(days=1)
    return count


def _is_business_day(date: pd.Timestamp, holidays: list) -> bool:
    d = date.date()
    if d.weekday() >= 5:
        return False
    if d in {pd.Timestamp(h).date() for h in holidays}:
        return False
    return True


def compute_dte_series(
    df: pd.DataFrame,
    cfg: dict,
    asof_col: str = "as_of_date",
    expiry_col: str = "expiry",
    holidays: Optional[list] = None,
) -> pd.Series:
    """Vectorized DTE calculation for a DataFrame.

    Args:
        df: DataFrame with asof_col and expiry_col
        cfg: DTE config (same as compute_dte)
        asof_col: column name for as-of dates
        expiry_col: column name for expiry dates
        holidays: list of holidays for trading-day basis

    Returns:
        Series of DTE values (years)
    """
    basis = cfg.get("basis", "calendar")

    if basis == "calendar":
        delta = (df[expiry_col] - df[asof_col]).dt.days
        if cfg.get("exclude_expiry_date", False):
            delta = delta.clip(lower=0)

        days_per_year = {"act_365": 365.0, "act_360": 360.0, "bus_252": 252.0}
        days = days_per_year.get(cfg.get("day_count", "act_365"), 365.0)
        result = delta / days
        result[df[asof_col] > df[expiry_col]] = np.nan
        return result

    else:
        # Trading-day basis — row-wise (slower but correct)
        return df.apply(
            lambda row: compute_dte(row[asof_col], row[expiry_col], cfg, holidays),
            axis=1,
        )
