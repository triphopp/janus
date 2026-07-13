import pandas as pd
import pytest


_CFG = {
    "exchange_tz": "America/New_York",
    "settlement_release_time": "14:30",
    "available_at_lag": {"settlement": "0h"},
    "iv_raw_unit": "percent",
}


def _write_wti(path, extra_rows=None):
    rows = [
        "9/25/2024|WTI|WTI Crude Futures|11/1/2024|T|F||70.00000|0.00000|11/1/2024|425||",
        "9/25/2024|WTI|WTI Crude Futures|11/1/2024|T|C|70.0000|2.10000|0.05000|10/17/2024|425|30.00000|0.50000",
        "9/25/2024|WTI|WTI Crude Futures|11/1/2024|T|P|70.0000|1.90000|0.03000|10/17/2024|425|31.00000|-0.50000",
    ]
    rows.extend(extra_rows or [])
    path.write_text(
        "TRADE DATE|HUB|PRODUCT|STRIP|CONTRACT|CONTRACT TYPE|STRIKE|SETTLEMENT PRICE|NET CHANGE|EXPIRATION DATE|PRODUCT_ID|OPTION_VOLATILITY|DELTA_FACTOR\n"
        + "\n".join(rows),
        encoding="utf-8",
    )
    return path


def test_settlement_loader_stamps_product_identity_fields(tmp_path):
    from ingestion.settlement_loader import SettlementLoader
    from ingestion.symbology import Symbology

    path = _write_wti(tmp_path / "wti.csv")
    out = SettlementLoader(Symbology(), cfg=_CFG).fetch(
        str(path), "2024-09-25", "2024-09-26"
    )

    assert set(out["instrument_type"]) == {"future", "option"}
    options = out[out["instrument_type"] == "option"]
    assert (options["product_family"] == "futures_options").all()
    assert (options["option_underlying_type"] == "future").all()
    assert (options["exercise_style"] == "american").all()
    assert (options["source_contract"] == "T").all()
    assert set(options["source_contract_type"]) == {"C", "P"}
    assert set(options["equivalent_option_root_cme"]) == {"LO"}

    future = out[out["instrument_type"] == "future"].iloc[0]
    assert pd.isna(future["strike"])
    assert pd.isna(future["iv_provided"])
    assert pd.isna(future["delta_provided"])


def test_settlement_loader_can_fail_closed_on_unknown_identity(tmp_path):
    from ingestion.settlement_loader import SettlementLoader
    from ingestion.symbology import Symbology

    bad = "9/25/2024|WTI|WTI Crude Futures|11/1/2024|T|C|70.0000|2.10000|0.05000|10/17/2024|425|30.00000|0.50000"
    path = _write_wti(
        tmp_path / "wti.csv",
        extra_rows=[
            bad.replace("|WTI Crude Futures|", "|Unexpected Product|"),
        ],
    )

    with pytest.raises(ValueError, match="Unresolved product identity"):
        SettlementLoader(
            Symbology(),
            cfg={**_CFG, "product_identity_policy": "fail"},
        ).fetch(str(path), "2024-09-25", "2024-09-26")
