import pandas as pd
import pytest

from run_pipeline import _assert_family_schema


def test_family_schema_guard_rejects_equity_without_price_column():
    df = pd.DataFrame({"as_of_date": pd.date_range("2024-01-01", periods=2)})

    with pytest.raises(ValueError, match="equity input missing required price"):
        _assert_family_schema({"family": "equity"}, df)


def test_family_schema_guard_rejects_options_without_chain_columns():
    df = pd.DataFrame({"as_of_date": [pd.Timestamp("2024-01-01")], "price": [1.0]})

    with pytest.raises(ValueError, match="not an option chain"):
        _assert_family_schema({"family": "futures_options"}, df)


def test_family_schema_guard_accepts_minimal_futures_frame():
    df = pd.DataFrame({"as_of_date": [pd.Timestamp("2024-01-01")], "price": [80.0]})

    _assert_family_schema({"family": "futures"}, df)
