"""Downstream option-chain Greeks export + data dictionary (issues 023, 024)."""

import json

import numpy as np
import pandas as pd
import pytest

from core import option_chain_export as oce


def _prepared_frame():
    """Post-adapter shape: one future + option rows, one of them flagged dirty."""
    base = dict(
        product_id=425, contract_root="T", hub="WTI",
        delivery_month=pd.Timestamp("2024-11-01"),
        as_of_date=pd.Timestamp("2024-09-25"), T=0.06, r=0.05,
    )
    rows = [
        {**base, "instrument_type": "future", "right": None, "strike": np.nan,
         "expiry": pd.Timestamp("2024-11-01"), "price": 70.0, "underlying_price": np.nan,
         "iv": np.nan, "dte_days": np.nan, "_iv_quality_flag": False, "_pcp_flag": False},
    ]
    def _row(**kw):
        return {**base, "instrument_type": "option", "expiry": pd.Timestamp("2024-10-17"),
                "dte_days": 22.0, "_iv_quality_flag": False, "_premium_quality_flag": False,
                "_pcp_flag": False, **kw}

    # clean option rows
    for strike, right, iv in [(65.0, "C", 0.30), (70.0, "C", 0.31), (70.0, "P", 0.31)]:
        rows.append(_row(right=right, strike=strike, price=2.10, option_price=2.10,
                         underlying_price=70.0, iv=iv))
    # IV-disagreement row (exchange IV vs price-inversion) — under issue 025 this is
    # NOT excluded; it keeps its exchange IV and is exported.
    rows.append(_row(right="C", strike=75.0, price=0.50, option_price=0.50,
                     underlying_price=70.0, iv=2.5, _iv_quality_flag=True))
    # genuinely corrupt row (premium below intrinsic) — still excluded.
    rows.append(_row(right="P", strike=60.0, price=0.01, option_price=0.01,
                     underlying_price=70.0, iv=0.30, _premium_quality_flag=True))
    return pd.DataFrame(rows)


_CFG = {
    "family": "futures_options", "pricing_model": "black76", "rf_rate": 0.05,
    "exchange_tz": "America/New_York",
    "settlement_release_time": "14:30",
    "exchange_calendar": "NYMEX",
    "settlement_timing": {
        "time_kind": "settlement_period_end",
        "source_reference": "https://example.test/settlement-policy",
    },
    "symbol": {"product_id": 425, "contract_root": "T", "hub": "WTI"},
    "export": {"product": "WTI", "underlying_root": "CL", "option_root": "LO",
               "exchange": "NYMEX", "currency": "USD",
               "price_unit": "USD_per_barrel", "contract_unit": "1000_barrels",
               "price_tick": 0.01},
}


def _built():
    return oce.build_option_chain_greeks(_prepared_frame(), _CFG)


# ── CSV contract ──────────────────────────────────────────────────────────────

def test_has_expected_columns_in_order():
    frame = _built()["frame"]
    assert list(frame.columns) == oce.EXPORT_COLUMNS
    expected = ("trade_date,product,underlying_symbol,option_symbol,contract_month,"
                "expiration_date,option_type,strike_price,option_settlement_price,"
                "underlying_settlement_price,implied_volatility,delta,gamma,vega,theta,"
                "rho,dte_days,pricing_model").split(",")
    assert list(frame.columns) == expected


def test_has_no_review_or_raw_vendor_columns():
    frame = _built()["frame"]
    for col in oce.FORBIDDEN_COLUMNS:
        assert col not in frame.columns


def test_rows_failing_release_gate_are_absent_from_downstream_csv():
    built = _built()
    # 3 clean + 1 IV-disagreement (kept under issue 025) = 4; genuine corruption excluded.
    assert built["n_exported"] == 4
    assert built["n_excluded"] == 1
    syms = "".join(built["frame"]["option_symbol"])
    assert "C75" in syms          # IV-disagreement row IS exported (exchange IV)
    assert "P60" not in syms      # premium-below-intrinsic row excluded


def test_iv_disagreement_row_exported_with_exchange_iv():
    """Issue 025: a row flagged only for provider/model IV disagreement is kept,
    carrying the exchange IV (2.5), not dropped."""
    frame = _built()["frame"]
    c75 = frame[frame["option_symbol"].str.contains("C75")].iloc[0]
    assert c75["implied_volatility"] == "2.500000"


def test_exports_full_greeks():
    frame = _built()["frame"]
    for g in ("delta", "gamma", "vega", "theta", "rho"):
        assert (frame[g].astype(str).str.len() > 0).all()
        assert frame[g].astype(float).notna().all()


def test_uses_black76_for_wti():
    assert (_built()["frame"]["pricing_model"] == "black76").all()


def test_formats_dates_as_iso():
    frame = _built()["frame"]
    assert (frame["trade_date"] == "2024-09-25").all()
    assert (frame["expiration_date"] == "2024-10-17").all()


def test_formats_contract_month_as_yyyy_mm_01():
    assert (_built()["frame"]["contract_month"] == "2024-11-01").all()


def test_formats_wti_prices_to_2_decimals():
    frame = _built()["frame"]
    for col in ("strike_price", "option_settlement_price", "underlying_settlement_price"):
        assert frame[col].str.match(r"^\d+\.\d{2}$").all(), col


def test_formats_iv_to_6_decimals():
    assert _built()["frame"]["implied_volatility"].str.match(r"^\d+\.\d{6}$").all()


def test_formats_greeks_to_8_decimals():
    frame = _built()["frame"]
    for g in ("delta", "gamma", "vega", "theta", "rho"):
        assert frame[g].str.match(r"^-?\d+\.\d{8}$").all(), g


def test_option_type_is_lowercase_enum():
    assert set(_built()["frame"]["option_type"]) <= {"call", "put"}


def test_dte_days_is_integer():
    assert _built()["frame"]["dte_days"].str.match(r"^\d+$").all()


# ── Writer + artifacts + run-level gate ───────────────────────────────────────

def test_write_creates_all_artifacts_and_summary_paths(tmp_path):
    readiness = {"status": "needs_review", "reasons": []}
    result = oce.write_option_chain_export(_prepared_frame(), _CFG, readiness, tmp_path)

    for key in ("option_chain_greeks_csv", "option_chain_greeks_manifest",
                "option_chain_greeks_schema", "option_chain_greeks_data_dictionary"):
        assert key in result
        from pathlib import Path
        assert Path(result[key]).exists()
    assert result["status"] == "needs_review"
    assert result["n_exported"] == 4


def test_blocked_readiness_withholds_export(tmp_path):
    blocked = {"status": "blocked", "reasons": ["pcp_mismatch_rate=0.40>=block"]}
    result = oce.write_option_chain_export(_prepared_frame(), _CFG, blocked, tmp_path)
    assert result["status"] == "blocked"
    assert not (tmp_path / "data" / "option_chain_greeks" / "option_chain_greeks.csv").exists()


def test_trade_date_is_not_treated_as_tradable_time(tmp_path):
    """The manifest must carry the policy that derives tradable time from trade_date."""
    manifest = oce.build_export_manifest(_CFG, {"status": "ready"})
    assert manifest["trade_date_meaning"] == "market_session_date"
    assert manifest["tradable_time_policy"] == "next_trading_session_after_trade_date"
    assert manifest["availability_policy"]


def test_manifest_declares_product_precision_and_iv_unit():
    manifest = oce.build_export_manifest(_CFG, {"status": "ready"})
    assert manifest["exchange"] == "NYMEX"
    assert manifest["iv_unit"] == "decimal"
    assert manifest["iv_decimal_places"] == 6
    assert manifest["greek_decimal_places"] == 8
    assert manifest["pricing_model"] == "black76"
    assert manifest["exchange_calendar"]
    assert manifest["quality_gate"] == "ready"


def test_manifest_declares_settlement_timing_policy():
    manifest = oce.build_export_manifest(_CFG, {"status": "ready"})
    timing = manifest["settlement_timing"]
    assert timing["time_kind"] == "settlement_period_end"
    assert timing["local_time"] == "14:30:00"
    assert timing["timezone"] == "America/New_York"
    assert timing["same_day_file_availability_assumption"] == "not_assumed"
    assert timing["source_reference"] == "https://example.test/settlement-policy"


def test_export_config_requires_instrument_policy_in_config():
    incomplete = {"family": "futures_options", "export": {"product": "Example"}}
    with pytest.raises(ValueError, match="underlying_root"):
        oce.export_config(incomplete)


def test_schema_units_come_from_export_config():
    cfg = {
        **_CFG,
        "exchange_calendar": "EXCHANGE",
        "exchange_tz": "UTC",
        "export": {
            **_CFG["export"],
            "product": "Example",
            "underlying_root": "EX",
            "option_root": "EO",
            "exchange": "EXCHANGE",
            "price_unit": "points",
            "contract_unit": "1_contract",
        },
    }
    schema = oce.build_export_schema(cfg)
    units = {c["name"]: c["unit"] for c in schema["columns"]}
    assert units["strike_price"] == "points"
    assert units["option_settlement_price"] == "points"
    assert units["underlying_settlement_price"] == "points"


# ── Data dictionary + schema (issue 024) ──────────────────────────────────────

def test_data_dictionary_exists_and_covers_every_column():
    text = oce.build_data_dictionary(_CFG)
    for col in oce.EXPORT_COLUMNS:
        assert f"`{col}`" in text


def test_schema_covers_every_exported_column():
    schema = oce.build_export_schema()
    names = [c["name"] for c in schema["columns"]]
    assert names == oce.EXPORT_COLUMNS


def test_dictionary_documents_source_mapping_and_domain_labels():
    text = oce.build_data_dictionary(_CFG)
    assert "STRIP" in text                      # contract_month raw source
    assert "OPTION_VOLATILITY" in text          # iv raw source
    assert "Implied Volatility" in text         # domain label
    assert "market session date" in text        # trade_date timing semantics


def test_dictionary_documents_timing_and_availability_policy():
    text = oce.build_data_dictionary(_CFG)
    assert "Timing and Availability" in text
    assert "available_at" in text
    assert "decision_time" in text
    assert "tradable_time" in text
    assert "14:30:00 America/New_York" in text
    assert "https://example.test/settlement-policy" in text


def test_dictionary_documents_contract_month_raw_to_canonical():
    text = oce.build_data_dictionary(_CFG)
    assert "2024-11-01" in text
    assert "YYYY-MM-01" in text


def test_schema_is_valid_json_roundtrip():
    schema = oce.build_export_schema()
    assert json.loads(json.dumps(schema))["name"] == "option_chain_greeks"


# ── End-to-end through the real adapter ───────────────────────────────────────

def test_export_excludes_corrupted_rows_end_to_end():
    """Through the full adapter, the injected IV/PCP-corrupted contracts must not
    reach the downstream CSV while the clean contracts do."""
    from adapters.futures_options_adapter import FuturesOptionsAdapter
    from tests.fixtures.wti_incident_fixture import (
        build_wti_incident_frame, incident_pipeline_cfg,
    )

    cfg = {**incident_pipeline_cfg(),
           "exchange_tz": "America/New_York",
           "settlement_release_time": "14:30",
           "exchange_calendar": "NYMEX",
           "export": {"product": "WTI", "underlying_root": "CL", "option_root": "LO",
                      "exchange": "NYMEX", "currency": "USD",
                      "price_unit": "USD_per_barrel", "contract_unit": "1000_barrels",
                      "price_tick": 0.01}}
    df, prepared_cfg = FuturesOptionsAdapter(cfg).prepare(build_wti_incident_frame())

    built = oce.build_option_chain_greeks(df, prepared_cfg)
    frame = built["frame"]

    # Under issue 025: the IV-mismatch 75C is KEPT (exchange IV authoritative); only
    # the PCP-break 65 pair is excluded (genuine market-consistency failure).
    assert built["n_exported"] > 0
    calls_75 = frame[(frame["option_type"] == "call")
                     & (frame["strike_price"] == "75.00")]
    calls_65 = frame[(frame["option_type"] == "call")
                     & (frame["strike_price"] == "65.00")]
    assert not calls_75.empty   # IV-mismatch contract now exported with exchange IV
    assert calls_65.empty       # PCP-break contract still excluded
    # full Greeks present on every exported row
    for g in ("delta", "gamma", "vega", "theta", "rho"):
        assert frame[g].astype(float).notna().all()
