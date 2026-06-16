"""v1.3 tests: stage 1 validators."""

import pandas as pd

from core.validators import logical_bounds_check, missing_completeness, outlier_cap


def test_logical_bounds_flags_bad_price_volume_iv_and_strike():
    df = pd.DataFrame({
        "price": [10.0, -1.0, 5.0, 6.0],
        "volume": [100, 100, -5, 100],
        "iv_provided": [0.2, 0.3, 0.4, 0.0],
        "strike": [50.0, 55.0, 60.0, -1.0],
    })

    out = logical_bounds_check(df, {"price_col": "price", "vol_col": "volume"})

    assert out["_bound_flag"].tolist() == [False, True, True, True]
    assert "price<=0" in out.loc[1, "_bound_reason"]
    assert "vol<0" in out.loc[2, "_bound_reason"]
    assert "iv<=0" in out.loc[3, "_bound_reason"]
    assert "strike<=0" in out.loc[3, "_bound_reason"]


def test_logical_bounds_flags_option_premium_even_when_price_std_is_underlying():
    df = pd.DataFrame({
        "instrument_type": ["option", "option"],
        "right": ["C", "P"],
        "strike": [100.0, 100.0],
        "option_price": [0.0, 2.0],
        "price_std": [105.0, 95.0],
        "F": [105.0, 95.0],
        "T": [30 / 365, 30 / 365],
        "r": [0.0, 0.0],
    })

    out = logical_bounds_check(df, {"price_col": "price_std"})

    assert out.loc[0, "_bound_flag"]
    assert "option_price<=0" in out.loc[0, "_bound_reason"]


def test_logical_bounds_flags_crossed_bid_ask():
    df = pd.DataFrame({
        "price": [1.0, 1.0],
        "bid": [1.2, 0.9],
        "ask": [1.0, 1.1],
    })

    out = logical_bounds_check(df, {"price_col": "price"})

    assert out.loc[0, "_bound_flag"]
    assert "bid>ask" in out.loc[0, "_bound_reason"]
    assert not out.loc[1, "_bound_flag"]


def test_missing_completeness_flags_date_gap_and_low_oi():
    df = pd.DataFrame({
        "product_id": [1, 1, 1],
        "as_of_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-10"]),
        "open_interest": [200, 50, 200],
    })

    out = missing_completeness(df, {"futures_oi_floor": 100})

    assert out.loc[1, "_missing_flag"]
    assert "OI<100" in out.loc[1, "_missing_reason"]
    assert out.loc[2, "_missing_flag"]
    assert "date_gap>5d" in out.loc[2, "_missing_reason"]


def test_missing_completeness_uses_symbol_identity_and_min_volume():
    df = pd.DataFrame({
        "as_of_date": pd.to_datetime(["2024-01-01", "2024-01-10", "2024-01-10"]),
        "symbol": ["AAPL", "AAPL", "MSFT"],
        "volume": [100_000, 10_000, 0],
    })

    out = missing_completeness(df, {
        "identity_cols": ["symbol"],
        "volume_col": "volume",
        "min_volume": 50_000,
    })

    assert out.loc[1, "_missing_flag"]
    assert "date_gap>5d" in out.loc[1, "_missing_reason"]
    assert "volume<50000" in out.loc[1, "_missing_reason"]
    assert out.loc[2, "_missing_flag"]
    assert "volume<50000" in out.loc[2, "_missing_reason"]


def test_outlier_cap_is_point_in_time_after_warmup():
    base = [100.0] * 45
    df = pd.DataFrame({
        "product_id": [1] * 46,
        "price": base + [1000.0],
    })

    out = outlier_cap(df, {"price_col": "price", "outlier_k": 3.0})

    assert out.loc[45, "_outlier_flag"]
    assert out.loc[45, "price"] < 1000.0


def test_missing_completeness_option_chain_no_false_duplicate():
    """Option chains must not be flagged duplicate_identity_date when identity_cols
    is the full contract key (not just product_id)."""
    dates = pd.to_datetime(["2024-01-02"] * 5)
    df = pd.DataFrame({
        "as_of_date": dates,
        "product_id": [254] * 5,
        "expiry": [pd.Timestamp("2024-03-01")] * 5,
        "right": ["C", "C", "C", "P", "P"],
        "strike": [75.0, 80.0, 85.0, 75.0, 80.0],
    })

    out = missing_completeness(df, {
        "identity_cols": ["product_id", "expiry", "right", "strike"],
    })

    assert not out["_missing_flag"].any(), (
        "Option rows with distinct (expiry, right, strike) should not be flagged duplicate"
    )


def test_outlier_cap_skips_option_rows():
    """outlier_cap must not include option rows in the price-series MAD.
    Option rows carry broadcast underlying prices, not per-contract series."""
    import numpy as np

    base = [100.0] * 30
    # 30 future rows (instrument_type=future) + 1 option row with a price that
    # would look like an outlier relative to the future series.
    future_rows = pd.DataFrame({
        "product_id": [1] * 30,
        "instrument_type": ["future"] * 30,
        "price": base,
    })
    option_row = pd.DataFrame({
        "product_id": [1],
        "instrument_type": ["option"],
        "price": [0.50],  # tiny premium — would be flagged if included in future MAD
    })
    df = pd.concat([future_rows, option_row], ignore_index=True)

    out = outlier_cap(df, {"price_col": "price", "outlier_k": 3.0})

    # The option row must NOT be flagged — it was excluded from the future MAD series
    assert not out.loc[30, "_outlier_flag"], (
        "Option row should be excluded from price-series outlier detection"
    )
