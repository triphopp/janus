"""Regression tests for equity data-prep invariants."""

import numpy as np
import pandas as pd


def test_equity_adapter_preserves_raw_return_and_flags_clip():
    """Return capping must be explicit and preserve the raw observation."""
    from adapters.equity_adapter import EquityAdapter

    raw = pd.DataFrame({
        "as_of_date": pd.date_range("2024-01-01", periods=30, freq="B"),
        "symbol": "TEST",
        "raw_close": [100.0 + i * 0.1 for i in range(29)] + [200.0],
        "adj_factor": 1.0,
        "volume": 1_000_000,
        "is_delisted": False,
    })
    df, _ = EquityAdapter({
        "vol_window": 5,
        "outlier_k": 3.0,
        "outlier_min_periods": 10,
    }).prepare(raw)

    assert "return_raw" in df.columns
    assert "_return_outlier_flag" in df.columns
    assert df.loc[df.index[-1], "_return_outlier_flag"]
    assert df.loc[df.index[-1], "return_raw"] > df.loc[df.index[-1], "return_std"]


def test_equity_adj_factor_not_treated_as_pit_truth_by_default():
    """Retro-adjusted provider factors are preserved but not used as PIT price truth."""
    from adapters.equity_adapter import EquityAdapter

    raw = pd.DataFrame({
        "as_of_date": pd.date_range("2024-01-01", periods=3, freq="B"),
        "symbol": "TEST",
        "raw_close": [100.0, 102.0, 104.0],
        "adj_factor": [0.5, 0.5, 0.5],
        "adj_factor_source": "yfinance_adj_close_retroactive",
        "volume": 1_000_000,
        "is_delisted": False,
    })

    df, _ = EquityAdapter({"vol_window": 2}).prepare(raw)

    assert df["price_std"].tolist() == [100.0, 102.0, 104.0]
    assert "price_adjustment_warning" in df.columns
    assert df["price_adjustment_warning"].all()


def test_equity_nested_config_reaches_core_cfg():
    """Nested validation/cv/performance/stability config should be available flat."""
    from adapters.equity_adapter import EquityAdapter

    raw = pd.DataFrame({
        "as_of_date": pd.date_range("2024-01-01", periods=10, freq="B"),
        "symbol": "TEST",
        "raw_close": np.linspace(100, 101, 10),
        "volume": 1_000_000,
        "is_delisted": False,
    })
    _, cfg = EquityAdapter({
        "validation": {"min_volume": 50_000, "outlier_k": 7.0},
        "cv": {"n_folds": 3, "purge_bars": 2},
        "performance": {"n_trials": 17},
        "stability": {"psi_threshold": 0.42},
    }).prepare(raw)

    assert cfg["min_volume"] == 50_000
    assert cfg["outlier_k"] == 7.0
    assert cfg["n_folds"] == 3
    assert cfg["purge_bars"] == 2
    assert cfg["n_trials"] == 17
    assert cfg["psi_threshold"] == 0.42
