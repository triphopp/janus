"""Contract tests for D6 silver-level option quality flags."""

import pandas as pd
import pytest

from adapters.futures_options_adapter import FuturesOptionsAdapter
from core import options_quality


def _base_df():
    return pd.DataFrame({
        "as_of_date": pd.to_datetime(["2024-01-01"] * 3),
        "instrument_type": ["option", "option", "option"],
        "right": ["C", "P", "C"],
        "strike": [100.0, 100.0, 100.0],
        "price": [5.0, 5.0, 0.01],
        "option_price": [5.0, 5.0, 0.01],
        "underlying_price": [100.0, 100.0, 120.0],
        "F": [100.0, 100.0, 120.0],
        "T": [0.5, 0.5, 0.5],
        "r": [0.0, 0.0, 0.0],
        "iv": [0.0, 6.0, float("nan")],
        "delta": [-0.1, 0.2, 1.2],
    })


def test_option_quality_flags_do_not_drop_rows():
    adapter = FuturesOptionsAdapter({
        "pricing_model": "black76",
        "option_quality": {"hard_iv_cap": 5.0},
    })
    out = adapter._flag_option_quality(_base_df())

    assert len(out) == 3
    assert out["_iv_quality_flag"].tolist() == [True, True, True]
    assert out["_delta_quality_flag"].tolist() == [True, True, True]
    assert "iv_non_positive" in out.loc[0, "_iv_quality_reason"]
    assert "iv_above_hard_cap" in out.loc[1, "_iv_quality_reason"]
    assert "iv_unsolved" in out.loc[2, "_iv_quality_reason"]


def test_option_quality_flags_call_delta_negative():
    adapter = FuturesOptionsAdapter({"pricing_model": "black76"})
    df = _base_df()
    out = adapter._flag_option_quality(df)

    assert out.loc[0, "_delta_quality_flag"] is True or out.loc[0, "_delta_quality_flag"] == True
    assert "call_delta_negative" in out.loc[0, "_delta_quality_reason"]


def test_option_quality_flags_put_delta_positive():
    adapter = FuturesOptionsAdapter({"pricing_model": "black76"})
    out = adapter._flag_option_quality(_base_df())

    assert "put_delta_positive" in out.loc[1, "_delta_quality_reason"]


def test_option_quality_flags_abs_delta_gt_one():
    adapter = FuturesOptionsAdapter({"pricing_model": "black76"})
    out = adapter._flag_option_quality(_base_df())

    assert "abs_delta_gt_one" in out.loc[2, "_delta_quality_reason"]


def test_option_quality_flags_clean_row_has_no_flags():
    adapter = FuturesOptionsAdapter({
        "pricing_model": "black76",
        "option_quality": {"hard_iv_cap": 5.0},
    })
    df = pd.DataFrame({
        "as_of_date": [pd.Timestamp("2024-01-01")],
        "instrument_type": ["option"],
        "right": ["C"],
        "strike": [100.0],
        "price": [5.0],
        "F": [100.0],
        "iv": [0.3],
        "delta": [0.5],
    })
    out = adapter._flag_option_quality(df)

    assert out.loc[0, "_iv_quality_flag"] == False
    assert out.loc[0, "_delta_quality_flag"] == False
    assert out.loc[0, "_premium_quality_flag"] == False


def test_option_quality_flags_future_rows_are_not_flagged():
    adapter = FuturesOptionsAdapter({"pricing_model": "black76"})
    df = pd.DataFrame({
        "instrument_type": ["future"],
        "right": [None],
        "strike": [None],
        "price": [80.0],
        "iv": [float("nan")],
        "delta": [float("nan")],
    })
    out = adapter._flag_option_quality(df)

    assert out.loc[0, "_iv_quality_flag"] == False
    assert out.loc[0, "_delta_quality_flag"] == False


def test_options_quality_summary_counts_silver_flag_reasons():
    df = pd.DataFrame({
        "instrument_type": ["option", "option"],
        "_iv_quality_flag": [True, False],
        "_iv_quality_reason": ["iv_above_hard_cap;", ""],
        "_delta_quality_flag": [False, True],
        "_delta_quality_reason": ["", "call_delta_negative;"],
        "_premium_quality_flag": [True, False],
        "_premium_quality_reason": ["premium_below_intrinsic;", ""],
    })

    out = options_quality.summarize(df, {}, None)

    assert out["silver_flags"]["iv_quality_flag_rate"] == pytest.approx(0.5)
    assert out["silver_flags"]["delta_quality_flag_rate"] == pytest.approx(0.5)
    assert out["silver_flags"]["premium_quality_flag_rate"] == pytest.approx(0.5)
    assert out["silver_flags"]["by_reason"]["iv_above_hard_cap"] == 1
    assert out["silver_flags"]["by_reason"]["call_delta_negative"] == 1
    assert out["silver_flags"]["by_reason"]["premium_below_intrinsic"] == 1
