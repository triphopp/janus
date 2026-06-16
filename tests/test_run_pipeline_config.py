"""Runtime config override tests for the CLI entry point."""

import pytest

from run_pipeline import apply_runtime_overrides
from run_pipeline import _assert_validation_folds
from run_pipeline import _cache_guard_status
from run_pipeline import _enforce_cache_guard
from run_pipeline import _price_adjustment_summary


def test_ticker_override_reuses_equity_yaml_as_template():
    cfg = {
        "family": "equity",
        "provider": "yfinance",
        "symbol": {"ticker": "AAPL"},
    }

    out = apply_runtime_overrides(cfg, ticker="msft")

    assert out["symbol"]["ticker"] == "MSFT"
    assert out["runtime_overrides"]["ticker"] == "MSFT"
    assert cfg["symbol"]["ticker"] == "AAPL"


def test_ticker_override_rejects_non_equity_config():
    cfg = {
        "family": "futures_options",
        "provider": "settlement",
        "symbol": {"product_id": 254},
    }

    with pytest.raises(ValueError, match="only supported for equity"):
        apply_runtime_overrides(cfg, ticker="MSFT")


def test_price_adjustment_summary_flags_blocked_retro_adjustments():
    import pandas as pd

    df = pd.DataFrame({
        "adj_factor": [1.0, 0.5, 0.5],
        "adj_factor_is_pit": ["False", "False", "False"],
        "price_adjustment_warning": ["False", "True", "True"],
        "adjusted_price_provider": [100.0, 51.0, 52.0],
        "price_std": [100.0, 102.0, 104.0],
    })

    out = _price_adjustment_summary(df, {})

    assert out["status"] == "warning"
    assert out["policy"] == "retro_adjustment_blocked"
    assert out["factor_rows"] == 2
    assert out["warning_rows"] == 2
    assert out["max_abs_price_std_vs_provider_adjusted"] == 52.0


def test_cache_guard_fails_unversioned_or_latest_data():
    assert _cache_guard_status({"data_version": "2024-01-01"}, "provider_fetch")["status"] == "fail"
    assert _cache_guard_status({"data_version": "latest"}, "versioned_cache")["status"] == "fail"
    assert _cache_guard_status({"data_version": "as_of_backtest_start"}, "versioned_cache")["status"] == "pass"


def test_cache_guard_is_fail_closed_by_default():
    guard = _cache_guard_status({"data_version": "2024-01-01"}, "provider_fetch")

    with pytest.raises(ValueError, match="Fixed versioned raw data is required"):
        _enforce_cache_guard(guard, {})

    _enforce_cache_guard(guard, {"require_fixed_data_version": False})


def test_zero_validation_folds_fail_closed():
    import pandas as pd

    df = pd.DataFrame({"as_of_date": pd.date_range("2024-01-01", periods=3, freq="B")})

    with pytest.raises(ValueError, match="No validation folds"):
        _assert_validation_folds([], df, {"purge_bars": 10, "event_embargo_bars": 2})
