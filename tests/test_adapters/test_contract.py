"""Adapter contract tests — prepare() must return (df, cfg) with correct shape."""

import numpy as np
import pandas as pd


class TestAdapterContract:
    """Every adapter.prepare() must return (DataFrame, dict) with required cfg keys."""

    REQUIRED_CFG_KEYS = [
        "price_col", "vol_col", "return_col", "vol_window", "trend_window",
    ]

    def test_equity_adapter_contract(self):
        """Equity adapter returns valid (df, cfg)."""
        from adapters.equity_adapter import EquityAdapter

        cfg = {"vol_window": 21, "trend_window": 126, "regime_axes": ["vol_regime"]}
        adapter = EquityAdapter(cfg)
        raw = pd.DataFrame({
            "as_of_date": pd.date_range("2024-01-01", periods=50, freq="B"),
            "symbol": "TEST",
            "raw_close": 100 + pd.Series(range(50)) * 0.1,
            "adj_factor": 1.0,
            "volume": 1000000,
            "is_delisted": False,
        })
        df, out_cfg = adapter.prepare(raw)
        assert isinstance(df, pd.DataFrame)
        assert isinstance(out_cfg, dict)
        assert len(df) == 50
        for key in self.REQUIRED_CFG_KEYS:
            assert key in out_cfg, f"Missing cfg key: {key}"

    def test_futures_adapter_contract(self):
        """Futures adapter returns continuous price and term-structure fields."""
        from adapters.futures_adapter import FuturesAdapter

        dates = pd.date_range("2024-01-01", periods=30, freq="B")
        raw = pd.DataFrame({
            "as_of_date": list(dates) * 2,
            "product_id": [1] * 60,
            "delivery_month": list(dates + pd.DateOffset(months=1)) + list(dates + pd.DateOffset(months=2)),
            "expiry": list(dates + pd.DateOffset(months=1)) + list(dates + pd.DateOffset(months=2)),
            "price": np.r_[np.linspace(80, 85, 30), np.linspace(81, 86, 30)],
        })
        df, out_cfg = FuturesAdapter({"vol_window": 5, "trend_window": 20}).prepare(raw)

        assert {"price_std", "return_std", "vol_std", "term_structure_slope"}.issubset(df.columns)
        for key in self.REQUIRED_CFG_KEYS:
            assert key in out_cfg, f"Missing cfg key: {key}"

    def test_equity_options_adapter_contract(self):
        """Equity options adapter returns IV/Greek columns and core cfg."""
        from adapters.equity_options_adapter import EquityOptionsAdapter

        dates = pd.date_range("2024-01-01", periods=30, freq="B")
        raw = pd.DataFrame({
            "as_of_date": dates,
            "product_id": 1,
            "raw_close": np.linspace(100, 105, 30),
            "adj_factor": 1.0,
            "price": np.linspace(5, 6, 30),
            "strike": 100.0,
            "expiry": pd.Timestamp("2024-06-30"),
            "right": "C",
            "iv_provided": 0.25,
        })
        cfg = {
            "pricing_model": "bsm",
            "iv_source": "provided",
            "dte": {"basis": "calendar", "day_count": "act_365"},
            "vol_window": 5,
            "trend_window": 20,
        }
        df, out_cfg = EquityOptionsAdapter(cfg).prepare(raw)

        assert {"iv", "delta", "gamma", "vega", "theta", "vrp_sign"}.issubset(df.columns)
        for key in self.REQUIRED_CFG_KEYS:
            assert key in out_cfg, f"Missing cfg key: {key}"

    def test_futures_options_adapter_contract(self):
        """Futures options adapter uses futures-specific fields with shared options logic."""
        from adapters.futures_options_adapter import FuturesOptionsAdapter

        dates = pd.date_range("2024-01-01", periods=30, freq="B")
        delivery = dates + pd.DateOffset(months=1)
        raw = pd.DataFrame({
            "as_of_date": list(dates) + list(dates),
            "product_id": 1,
            "instrument_type": ["future"] * 30 + ["option"] * 30,
            "delivery_month": list(delivery) + list(delivery),
            "expiry": list(delivery) + [pd.Timestamp("2024-06-30")] * 30,
            "price": list(np.linspace(80, 85, 30)) + list(np.linspace(5, 6, 30)),
            "strike": [np.nan] * 30 + [80.0] * 30,
            "right": [None] * 30 + ["C"] * 30,
            "iv_provided": [np.nan] * 30 + [0.25] * 30,
        })
        cfg = {
            "pricing_model": "black76",
            "iv_source": "provided",
            "dte": {"basis": "calendar", "day_count": "act_365"},
            "vol_window": 5,
            "trend_window": 20,
        }
        df, out_cfg = FuturesOptionsAdapter(cfg).prepare(raw)

        assert {"F", "iv", "delta", "gamma", "vega", "theta", "term_structure_slope"}.issubset(df.columns)
        for key in self.REQUIRED_CFG_KEYS:
            assert key in out_cfg, f"Missing cfg key: {key}"
