"""Schema validation should enforce the ingestion boundary, not just columns."""

import pandas as pd
import pytest

from ingestion.base import validate_schema
from ingestion.settlement_loader import parse_pipe_row


def test_validate_schema_coerces_declared_dtypes():
    schema = {
        "as_of_date": "datetime64[ns]",
        "available_at": "datetime64[ns, UTC]",
        "product_id": "int",
        "price": "float",
    }
    df = pd.DataFrame({
        "as_of_date": ["2024-01-01"],
        "available_at": ["2024-01-01T03:00:00Z"],
        "product_id": ["254"],
        "price": ["63.46"],
    })

    out = validate_schema(df, schema)

    assert pd.api.types.is_datetime64_any_dtype(out["as_of_date"])
    assert str(out["available_at"].dtype) == "datetime64[ns, UTC]"
    assert str(out["product_id"].dtype) == "int64"
    assert str(out["price"].dtype) == "float64"


def test_validate_schema_raises_on_invalid_required_dtype():
    schema = {"product_id": "int", "price": "float"}
    df = pd.DataFrame({"product_id": ["not-an-id"], "price": [1.0]})

    with pytest.raises(ValueError, match="invalid int values"):
        validate_schema(df, schema)


def test_settlement_parse_row_stamps_decision_time():
    row = "9/25/2024|North Sea|Brent Crude Futures|11/1/2024|B|C|10.0000|63.46000|-1.71000|9/25/2024|254|0.01000|0.00000"

    out = parse_pipe_row(row)

    assert out["decision_time"] == out["available_at"]
