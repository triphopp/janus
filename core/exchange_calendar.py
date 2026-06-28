"""Exchange/provider trading-calendar resolution for coverage (issue 013).

Coverage expectations should use an instrument's real exchange calendar where one is
available, not a generic Mon-Fri business-day count that ignores exchange holidays and
special closes. When no calendar can be resolved, we fall back to generic business days
but make that fallback *visible* (``calendar_id == "generic"``) so it is never mistaken
for exchange truth.

Resolution order for a requested ``calendar_id``:

1. explicit ``holidays`` passed by the caller (config-provided list),
2. ``pandas_market_calendars`` if installed and the id is known,
3. generic business days (source ``generic``).

Pure/optional-dependency aware: importing this module never requires
``pandas_market_calendars``.
"""

from __future__ import annotations

from typing import Iterable, Optional

import pandas as pd

# Map our instrument calendar ids to pandas_market_calendars names, when available.
_PMC_ALIASES = {
    "NYMEX": "CME_Energy",
    "CME_Energy": "CME_Energy",
    "NYSE": "NYSE",
    "NASDAQ": "NASDAQ",
    "CME": "CME_Equity",
}


def _generic_trading_days(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    return pd.bdate_range(start, end)


def resolve_calendar(
    calendar_id: Optional[str],
    start,
    end,
    holidays: Optional[Iterable] = None,
) -> dict:
    """Resolve trading days for a window.

    Args:
        calendar_id: requested exchange/provider calendar id (e.g. ``"NYMEX"``), or
            None/``"generic"`` to force generic business days.
        start, end: window bounds (inclusive).
        holidays: optional explicit holiday dates that override calendar lookup.

    Returns:
        dict with ``calendar_id`` (resolved label), ``source``
        (``"config"`` | ``"pandas_market_calendars"`` | ``"generic"``),
        ``trading_days`` (DatetimeIndex), and ``holidays`` (sorted list of dates
        removed relative to generic business days).
    """
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    generic = _generic_trading_days(start, end)

    # 1) Explicit config-provided holidays.
    if holidays is not None:
        holiday_set = {pd.Timestamp(h).normalize() for h in holidays}
        trading = generic[~generic.normalize().isin(holiday_set)]
        return {
            "calendar_id": calendar_id or "config",
            "source": "config",
            "trading_days": trading,
            "holidays": sorted(str(h.date()) for h in holiday_set if start <= h <= end),
        }

    # 2) pandas_market_calendars, if installed and the id is known.
    if calendar_id and str(calendar_id).lower() != "generic":
        pmc_name = _PMC_ALIASES.get(calendar_id, calendar_id)
        try:
            import pandas_market_calendars as mcal  # type: ignore

            cal = mcal.get_calendar(pmc_name)
            sched = cal.schedule(start_date=start, end_date=end)
            trading = pd.DatetimeIndex(pd.to_datetime(sched.index)).normalize()
            removed = generic.normalize().difference(trading)
            return {
                "calendar_id": calendar_id,
                "source": "pandas_market_calendars",
                "trading_days": trading,
                "holidays": sorted(str(h.date()) for h in removed),
            }
        except Exception:
            # Not installed or unknown id → fall through to visible generic fallback.
            pass

    # 3) Generic business-day fallback — explicitly labelled so it is not mistaken
    #    for exchange truth.
    return {
        "calendar_id": "generic",
        "source": "generic",
        "trading_days": generic,
        "holidays": [],
    }
