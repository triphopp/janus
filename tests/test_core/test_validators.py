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


def test_outlier_cap_is_point_in_time_after_warmup():
    base = [100.0] * 45
    df = pd.DataFrame({
        "product_id": [1] * 46,
        "price": base + [1000.0],
    })

    out = outlier_cap(df, {"price_col": "price", "outlier_k": 3.0})

    assert out.loc[45, "_outlier_flag"]
    assert out.loc[45, "price"] < 1000.0
