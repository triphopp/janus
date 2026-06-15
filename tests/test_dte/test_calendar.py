"""DTE convention tests — single source of truth (section 12).

Silent killer: using wrong DTE convention silently corrupts Greeks.
T in Black-76 shifts ~1.4x (252 vs 365) → IV wrong ~20%.
"""

import numpy as np
import pytest
import pandas as pd
from core.dte import compute_dte, compute_dte_series


class TestDTECalendarBasis:
    """Calendar-day convention (act_365)."""

    def test_calendar_simple(self):
        """Sep 25 → Nov 1 = 37 calendar days."""
        cfg = {"basis": "calendar", "day_count": "act_365", "exclude_expiry_date": False}
        dte = compute_dte("2024-09-25", "2024-11-01", cfg)
        assert dte == pytest.approx(37 / 365, rel=1e-6)  # 37 calendar days

    def test_calendar_exclude_expiry(self):
        """Exclude expiry date: DTE of expiry day = 0."""
        cfg = {"basis": "calendar", "day_count": "act_365", "exclude_expiry_date": True}
        dte = compute_dte("2024-11-01", "2024-11-01", cfg)
        assert dte == 0.0

    def test_calendar_include_expiry(self):
        """Include expiry date: DTE of expiry day > 0."""
        cfg = {"basis": "calendar", "day_count": "act_365", "exclude_expiry_date": False}
        dte = compute_dte("2024-11-01", "2024-11-01", cfg)
        assert dte == 0.0  # 0 days difference


class TestDTETradingBasis:
    """Trading-day convention (bus_252)."""

    def test_trading_simple(self):
        """Sep 25 → Nov 1 ≈ 26 trading days (no holidays)."""
        cfg = {"basis": "trading", "day_count": "bus_252", "exclude_expiry_date": False}
        dte = compute_dte("2024-09-25", "2024-11-01", cfg)
        # ~26 business days / 252
        assert dte > 0.08 and dte < 0.12  # rough range


class TestDTEEdgeCases:
    """Edge cases that silently corrupt results."""

    @pytest.mark.parametrize("asof,expiry,basis,expected_check", [
        # T=0 edge: expiry day
        ("2024-11-01", "2024-11-01", "calendar", lambda v: v == 0.0),
        # T<0 edge: post-expiry → NaN
        ("2024-11-02", "2024-11-01", "calendar", lambda v: np.isnan(v)),
        # Leap year
        ("2024-02-28", "2024-03-01", "calendar", lambda v: v == 2/365),
    ])
    def test_dte_edge(self, asof, expiry, basis, expected_check):
        cfg = {"basis": basis, "day_count": "act_365", "exclude_expiry_date": False}
        result = compute_dte(asof, expiry, cfg)
        assert expected_check(result), f"Expected check failed for {asof}→{expiry}, got {result}"

    def test_post_expiry_is_nan(self):
        """Post-expiry DTE must be NaN, not 0 or negative."""
        cfg = {"basis": "calendar", "day_count": "act_365", "exclude_expiry_date": False}
        dte = compute_dte("2024-12-01", "2024-11-15", cfg)
        assert np.isnan(dte)


class TestDTESeries:
    """Vectorized DTE calculation."""

    def test_series_output(self):
        """compute_dte_series returns same-length output."""
        df = pd.DataFrame({
            "as_of_date": pd.date_range("2024-06-01", periods=30, freq="B"),
            "expiry":     pd.Timestamp("2024-09-30"),
        })
        cfg = {"basis": "calendar", "day_count": "act_365", "exclude_expiry_date": False}
        result = compute_dte_series(df, cfg)
        assert len(result) == 30
        assert result.notna().all()

    def test_post_expiry_nan_in_series(self):
        """Rows with as_of_date > expiry must be NaN."""
        df = pd.DataFrame({
            "as_of_date": pd.date_range("2024-10-01", periods=10, freq="B"),
            "expiry":     pd.Timestamp("2024-09-30"),
        })
        cfg = {"basis": "calendar", "day_count": "act_365", "exclude_expiry_date": False}
        result = compute_dte_series(df, cfg)
        assert result.isna().all()
