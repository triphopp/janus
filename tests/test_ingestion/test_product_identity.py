import textwrap

import pandas as pd
import pytest

from ingestion.product_identity import ProductIdentityMaster, ProductIdentityResolver


def _raw(rows):
    return pd.DataFrame(rows)


def _wti(contract_type="C", strike=70.0, product_id=425, contract="T"):
    return {
        "TRADE DATE": pd.Timestamp("2024-09-25"),
        "HUB": "WTI",
        "PRODUCT": "WTI Crude Futures",
        "STRIP": pd.Timestamp("2024-11-01"),
        "CONTRACT": contract,
        "CONTRACT TYPE": contract_type,
        "STRIKE": strike,
        "SETTLEMENT PRICE": 2.10,
        "PRODUCT_ID": product_id,
    }


def test_wti_option_on_futures_identity_is_row_level():
    out = ProductIdentityResolver().resolve_frame(_raw([_wti("C", 70.0)]))
    row = out.iloc[0]

    assert row["instrument_type"] == "option"
    assert row["right"] == "C"
    assert row["option_right"] == "call"
    assert row["source_contract"] == "T"
    assert row["source_contract_type"] == "C"
    assert row["product_family"] == "futures_options"
    assert row["option_underlying_type"] == "future"
    assert row["exercise_style"] == "american"
    assert row["source_option_root"] == "T"
    assert row["equivalent_option_root_cme"] == "LO"
    assert row["product_identity_status"] == "resolved"


def test_wti_future_support_row_is_not_an_option():
    out = ProductIdentityResolver().resolve_frame(_raw([_wti("F", None)]))
    row = out.iloc[0]

    assert row["instrument_type"] == "future"
    assert pd.isna(row["right"])
    assert row["product_family"] == "futures_options"
    assert row["product_identity_status"] == "resolved"


@pytest.mark.parametrize(
    ("contract_type", "strike", "status", "instrument_type"),
    [
        ("C", None, "unknown", "unknown"),
        ("P", None, "unknown", "unknown"),
        ("F", 70.0, "conflict", "unknown"),
        ("I", None, "resolved", "index"),
        ("CASH", None, "resolved", "cash"),
        ("CS", None, "resolved", "cash"),
        ("X", None, "unknown", "unknown"),
    ],
)
def test_contract_type_conflicts_do_not_fall_through_to_future(
    contract_type, strike, status, instrument_type
):
    out = ProductIdentityResolver().resolve_frame(_raw([_wti(contract_type, strike)]))

    assert out.loc[0, "product_identity_status"] == status
    assert out.loc[0, "instrument_type"] == instrument_type


def test_unknown_product_tuple_is_unknown_not_generic_future():
    out = ProductIdentityResolver().resolve_frame(_raw([_wti("C", 70.0, product_id=999)]))

    assert out.loc[0, "product_identity_status"] == "unknown"
    assert out.loc[0, "product_identity_reason"] == "product_master_no_match"


def test_duplicate_product_identity_mapping_raises(tmp_path):
    path = tmp_path / "product_identity.yaml"
    path.write_text(
        textwrap.dedent(
            """
            schema_version: 1
            products:
              - provider: ice_settlement_file
                source_product_id: 1
                hub: H
                source_product_name: P
                source_contract: X
                product_family: futures_options
              - provider: ice_settlement_file
                source_product_id: 1
                hub: H
                source_product_name: P
                source_contract: X
                product_family: equity_options
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate/conflicting"):
        ProductIdentityMaster(path)
