"""P0 tests — data contracts + quarantine routing."""

import pandas as pd
import pytest

from core import contracts as cmod
from core.quarantine import write_quarantine


CONTRACTS_DIR = "contracts"


def _settlement_contract():
    return cmod.load_contract("settlement_options", 1, CONTRACTS_DIR)


def _clean_settlement_df():
    """Two valid futures rows in RAW_SCHEMA shape."""
    dates = pd.to_datetime(["2024-03-15", "2024-03-16"])
    return pd.DataFrame({
        "as_of_date":      dates,
        "available_at":    pd.to_datetime(dates, utc=True) + pd.Timedelta(hours=3),
        "ingested_at":     pd.Timestamp("2024-06-01T00:00:00Z"),
        "timestamp":       [None, None],
        "product_id":      [254, 254],
        "contract_root":   ["B", "B"],
        "hub":             ["North Sea", "North Sea"],
        "instrument_type": ["future", "future"],
        "right":           [None, None],
        "strike":          [float("nan"), float("nan")],
        "delivery_month":  pd.to_datetime(["2024-05-01", "2024-05-01"]),
        "expiry":          pd.to_datetime(["2024-05-01", "2024-05-01"]),
        "price":           [85.0, 86.0],
        "net_change":      [0.1, 1.0],
        "iv_provided":     [float("nan"), float("nan")],
        "delta_provided":  [float("nan"), float("nan")],
        "provider":        ["settlement", "settlement"],
    })


def test_clean_rows_all_pass():
    df = _clean_settlement_df()
    res = cmod.validate(df, _settlement_contract())
    assert res.report["rows_quarantined"] == 0
    assert res.report["quarantine_rate"] == 0.0
    assert len(res.passed) == 2
    assert res.quarantined.empty


def test_negative_price_routes_to_quarantine():
    df = _clean_settlement_df()
    df.loc[0, "price"] = -5.0
    res = cmod.validate(df, _settlement_contract())
    assert res.report["rows_quarantined"] == 1
    assert len(res.passed) == 1
    assert "semantic:price_le_0" in res.report["quarantine_by_reason"]
    assert "price_le_0" in res.quarantined.iloc[0][cmod.QUARANTINE_REASON_COL]


def test_null_in_required_column_is_structural_quarantine():
    df = _clean_settlement_df()
    df.loc[1, "price"] = None
    res = cmod.validate(df, _settlement_contract())
    assert res.report["structural"]["failures"].get("price") == 1
    assert "structural:price" in res.report["quarantine_by_reason"]
    assert res.report["rows_quarantined"] == 1


def test_option_strike_and_right_rules_fire_only_for_options():
    df = _clean_settlement_df()
    # turn row 0 into a malformed option: option type, bad strike, bad right
    df.loc[0, "instrument_type"] = "option"
    df.loc[0, "right"] = "X"
    df.loc[0, "strike"] = -1.0
    res = cmod.validate(df, _settlement_contract())
    reasons = res.report["quarantine_by_reason"]
    assert "semantic:option_strike_le_0" in reasons
    assert "semantic:option_right_invalid" in reasons
    # the valid future row (row 1) still passes
    assert len(res.passed) == 1


def test_pit_violation_available_before_as_of():
    df = _clean_settlement_df()
    # available_at one day BEFORE as_of_date → PIT violation
    df.loc[0, "available_at"] = pd.Timestamp("2024-03-14T00:00:00Z")
    res = cmod.validate(df, _settlement_contract())
    assert res.report["pit"]["violations"] == 1
    assert "pit:available_before_as_of" in res.report["quarantine_by_reason"]


def test_symbology_orphan_routes_to_quarantine(symbology):
    df = _clean_settlement_df()
    df.loc[0, "product_id"] = 999999  # not in product_map
    res = cmod.validate(df, _settlement_contract(), symbology=symbology)
    assert res.report["symbology"]["checked"] is True
    assert 999999 in res.report["symbology"]["orphans"]
    assert "symbology:orphan" in res.report["quarantine_by_reason"]


def test_float_key_snapping_rounds_strike():
    df = _clean_settlement_df()
    df.loc[0, "instrument_type"] = "option"
    df.loc[0, "right"] = "C"
    df.loc[0, "strike"] = 85.00000000001  # raw bit noise
    res = cmod.validate(df, _settlement_contract())
    snapped = res.passed[res.passed["instrument_type"] == "option"]["strike"]
    assert (snapped == 85.0).all()


def test_enforcement_block_raises_on_missing_required_column():
    df = _clean_settlement_df().drop(columns=["price"])
    contract = _settlement_contract()
    contract["enforcement"] = "block"
    with pytest.raises(cmod.ContractViolation):
        cmod.validate(df, contract)


def test_distributional_null_rate_break_recorded():
    df = _clean_settlement_df()
    contract = _settlement_contract()
    # net_change is nullable; force a null_rate break on it for the test
    contract["distributional"] = [{"col": "net_change", "check": "null_rate", "max": 0.0}]
    df.loc[0, "net_change"] = None
    res = cmod.validate(df, contract)
    assert res.report["distributional"]["net_change:null_rate"]["status"] == "break"
    assert any(b["type"] == "drift" for b in res.report["frame_breaks"])


def test_write_quarantine_summary_has_rate(tmp_path):
    df = _clean_settlement_df()
    df.loc[0, "price"] = -5.0
    res = cmod.validate(df, _settlement_contract())
    summary = write_quarantine(res.quarantined, "testrun", "bronze", res.report["rows_in"], out_dir=tmp_path)
    assert summary["rows_quarantined"] == 1
    assert summary["quarantine_rate"] == 0.5
    assert "semantic:price_le_0" in summary["by_reason"]
    assert (tmp_path / "testrun" / "bronze.csv").exists()


def test_resolve_contract_id_from_family():
    assert cmod.resolve_contract_id({"family": "futures_options"})[0] == "settlement_options"
    assert cmod.resolve_contract_id({"family": "equity"})[0] == "equity_price"
    assert cmod.resolve_contract_id({"family": "unknown_family"}) is None


def test_validate_for_cfg_resolves_from_bz_config_and_passes_clean(bz_config, sample_raw_df, symbology):
    """Integration: the bronze gate path the pipeline uses — cfg → contract → validate."""
    res = cmod.validate_for_cfg(sample_raw_df, bz_config, symbology=symbology)
    assert res is not None
    assert res.report["contract_id"] == "settlement_options"
    # sample_raw_df is 100 clean Brent futures rows → nothing quarantined
    assert res.report["rows_quarantined"] == 0
    assert len(res.passed) == len(sample_raw_df)
