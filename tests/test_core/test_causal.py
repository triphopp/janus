"""Causal helper tests: grain and PIT timing guards."""

import pandas as pd
import pytest

from core.causal import to_causal_series, validate_pit_timing


def test_to_causal_series_collapses_long_table_to_sorted_unique_dates():
    df = pd.DataFrame({
        "as_of_date": pd.to_datetime([
            "2024-01-02", "2024-01-01", "2024-01-02", "2024-01-01"
        ]),
        "value": [4.0, 1.0, 6.0, 3.0],
    })

    out = to_causal_series(df, "value", agg="mean")

    assert out.index.tolist() == [pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02")]
    assert out.tolist() == [2.0, 5.0]
    assert out.index.is_unique
    assert out.index.is_monotonic_increasing


def test_validate_pit_timing_rejects_unavailable_data_at_decision_time():
    df = pd.DataFrame({
        "as_of_date": pd.to_datetime(["2024-01-01"]),
        "available_at": pd.to_datetime(["2024-01-02T12:00:00Z"]),
        "decision_time": pd.to_datetime(["2024-01-02T09:00:00Z"]),
    })

    with pytest.raises(ValueError, match="available_at_after_decision_time"):
        validate_pit_timing(df)


def test_validate_pit_timing_accepts_ordered_decision_flow():
    df = pd.DataFrame({
        "as_of_date": pd.to_datetime(["2024-01-01"]),
        "available_at": pd.to_datetime(["2024-01-02T03:00:00Z"]),
        "decision_time": pd.to_datetime(["2024-01-02T09:00:00Z"]),
        "execution_time": pd.to_datetime(["2024-01-02T09:01:00Z"]),
        "label_end_time": pd.to_datetime(["2024-01-03T00:00:00Z"]),
    })

    assert validate_pit_timing(df, execution_col="execution_time", label_end_col="label_end_time")

