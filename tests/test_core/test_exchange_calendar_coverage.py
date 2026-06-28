"""Exchange-calendar coverage (issue 013)."""

import pandas as pd

from core import coverage as coverage_mod
from core.exchange_calendar import resolve_calendar


def _present(dates):
    return pd.DataFrame({"as_of_date": pd.to_datetime(list(dates))})


def test_resolve_calendar_generic_fallback_is_visible():
    out = resolve_calendar("UNKNOWN_EXCHANGE", "2024-01-01", "2024-01-31")
    # Unknown / uninstalled calendar must not silently masquerade as exchange truth.
    assert out["calendar_id"] == "generic"
    assert out["source"] == "generic"


def test_resolve_calendar_with_config_holidays():
    out = resolve_calendar(
        "NYMEX", "2024-01-01", "2024-01-31",
        holidays=["2024-01-01", "2024-01-15"],  # New Year, MLK day
    )
    assert out["calendar_id"] == "NYMEX"
    assert out["source"] == "config"
    days = out["trading_days"].normalize()
    assert pd.Timestamp("2024-01-01") not in days
    assert pd.Timestamp("2024-01-15") not in days


def test_coverage_records_calendar_id():
    report = coverage_mod.assess_coverage(
        _present(pd.bdate_range("2024-01-01", "2024-01-31")),
        "2024-01-01", "2024-01-31",
    )
    assert report["calendar_id"] == "generic"
    assert report["calendar_source"] == "generic"


def test_exchange_calendar_differs_from_generic_weekdays():
    """A holiday-aware calendar yields fewer expected days than generic weekdays."""
    start, end = "2024-01-01", "2024-01-31"
    holidays = ["2024-01-01", "2024-01-15"]  # both are weekdays

    generic = coverage_mod.assess_coverage(_present([]), start, end)
    exch = coverage_mod.assess_coverage(
        _present([]), start, end, calendar_id="NYMEX", holidays=holidays
    )

    assert exch["expected_trading_days"] == generic["expected_trading_days"] - 2
    assert exch["calendar_id"] == "NYMEX"
    assert exch["calendar_source"] == "config"
    assert "2024-01-15" in exch["calendar_holidays_in_window"]


def test_holiday_absence_does_not_count_against_coverage():
    """Present on every NYMEX trading day → full coverage despite holiday gaps."""
    start, end = "2024-01-01", "2024-01-31"
    holidays = ["2024-01-01", "2024-01-15"]
    trading = resolve_calendar("NYMEX", start, end, holidays=holidays)["trading_days"]

    report = coverage_mod.assess_coverage(
        _present(trading), start, end, calendar_id="NYMEX", holidays=holidays
    )
    assert report["coverage_ratio"] == 1.0
    assert report["status"] == "pass"
