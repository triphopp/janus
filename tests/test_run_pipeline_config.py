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


def test_runtime_overrides_set_nested_and_flat_cli_controls():
    cfg = {
        "family": "futures_options",
        "pricing": {"compute_greeks": False},
        "cv": {"n_folds": 8, "event_embargo_bars": 2},
        "option_universe": {"max_dte_days": 730},
    }

    out = apply_runtime_overrides(
        cfg,
        compute_greeks=True,
        pricing_model="auto",
        allow_model_approximation=True,
        compare_models=["black76_european"],
        metrics_mode="diagnostic",
        min_dte=3,
        max_dte=90,
        min_option_price=0.05,
        iv_cap=2.0,
        min_abs_delta=0.15,
        max_abs_delta=0.65,
        n_folds=4,
        embargo_bars=1,
        progress="plain",
    )

    assert out["pricing"]["compute_greeks"] is True
    assert out["compute_greeks"] is True
    assert out["pricing"]["model"] == "auto"
    assert out["pricing_model"] == "auto"
    assert out["allow_model_approximation"] is True
    assert out["compare_models"] == ["black76_european"]
    assert out["metrics_mode"] == "diagnostic"
    assert out["option_universe"]["min_dte_days"] == 3
    assert out["option_universe"]["max_dte_days"] == 90
    assert out["option_universe"]["min_option_price"] == 0.05
    assert out["option_universe"]["max_iv"] == 2.0
    assert out["option_universe"]["delta_band"] == {
        "min_abs_delta": 0.15,
        "max_abs_delta": 0.65,
    }
    assert out["cv"]["n_folds"] == 4
    assert out["n_folds"] == 4
    assert out["cv"]["event_embargo_bars"] == 1
    assert out["event_embargo_bars"] == 1
    assert out["progress_mode"] == "plain"
    assert cfg["pricing"]["compute_greeks"] is False
    assert cfg["cv"]["n_folds"] == 8


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


def test_cache_guard_accepts_hash_pinned_local_file(tmp_path):
    import hashlib

    data_file = tmp_path / "settle.csv"
    data_file.write_text("fixed input\n", encoding="utf-8")
    digest = hashlib.sha256(data_file.read_bytes()).hexdigest()

    ok = _cache_guard_status({
        "data_file": str(data_file),
        "data_file_sha256": digest,
        "data_version": f"sha256:{digest}",
    }, "provider_fetch")
    bad = _cache_guard_status({
        "data_file": str(data_file),
        "data_file_sha256": "0" * 64,
    }, "provider_fetch")

    assert ok["status"] == "pass"
    assert ok["source"] == "pinned_local_file"
    assert bad["status"] == "fail"


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
