"""Executable contract tests for planned equity-options loader provenance."""

import pandas as pd
import pytest


def test_equity_options_leg_frame_records_price_source_and_spread():
    from ingestion.equity_options_loader_yf import EquityOptionsLoaderYF

    leg = pd.DataFrame({
        "strike": [100, 105, 110],
        "bid": [1.0, 0.0, float("nan")],
        "ask": [1.2, 0.0, float("nan")],
        "lastPrice": [1.5, 2.0, float("nan")],
        "impliedVolatility": [0.2, 0.25, 0.3],
        "volume": [10, 0, 0],
        "openInterest": [100, 0, 0],
    })

    out = EquityOptionsLoaderYF._leg_frame(leg, "C", "2024-03-15")

    assert out["price"].tolist()[:2] == [1.1, 2.0]
    assert pd.isna(out["price"].iloc[2])
    assert out["price_source"].tolist() == ["mid", "last", "missing"]
    assert out["bid_ask_spread"].iloc[0] == pytest.approx(0.2)
    assert out["relative_spread"].iloc[0] == pytest.approx(0.2 / 1.1)


def test_equity_options_leg_frame_marks_wide_spread_rows():
    from ingestion.equity_options_loader_yf import EquityOptionsLoaderYF

    leg = pd.DataFrame({
        "strike": [100, 105],
        "bid": [1.0, 1.0],
        "ask": [1.1, 4.0],
        "lastPrice": [1.2, 2.0],
        "impliedVolatility": [0.2, 0.25],
        "volume": [10, 0],
        "openInterest": [100, 0],
    })

    out = EquityOptionsLoaderYF._leg_frame(leg, "P", "2024-03-15")

    assert out["_wide_spread_flag"].tolist() == [False, True]
