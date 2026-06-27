"""Settlement availability anchor + Phase-1 data-integrity gates (issues 022, 002)."""

import pandas as pd
import pytest

from ingestion.versioned_cache import infer_available_at


_SETTLEMENT_CFG = {
    "exchange_tz": "America/New_York",
    "settlement_release_time": "14:30",
    "available_at_lag": {"settlement": "0h"},
}


def test_settlement_available_at_not_anchored_to_midnight():
    as_of = pd.Timestamp("2024-09-25")
    avail = infer_available_at(as_of, "settlement", _SETTLEMENT_CFG)

    # Old bug: midnight + 3h = 2024-09-25T03:00Z (prior US evening). The fix anchors
    # to 14:30 ET = 18:30Z (EDT). Must be well after the buggy early timestamp.
    buggy = pd.Timestamp("2024-09-25T03:00:00Z")
    assert avail > buggy
    assert avail == pd.Timestamp("2024-09-25T18:30:00Z")


def test_us_settlement_not_available_before_local_session_close():
    as_of = pd.Timestamp("2024-09-25")
    avail = infer_available_at(as_of, "settlement", _SETTLEMENT_CFG)
    # 14:30 America/New_York for the same session date.
    local = avail.tz_convert("America/New_York")
    assert local.date() == as_of.date()
    assert (local.hour, local.minute) == (14, 30)


def test_settlement_utc_boundary_conversion():
    """A late evening release in EST rolls the UTC date forward."""
    cfg = {
        "exchange_tz": "America/New_York",
        "settlement_release_time": "22:00",   # 10pm EST = 03:00Z next day
        "available_at_lag": {"settlement": "0h"},
    }
    avail = infer_available_at(pd.Timestamp("2024-01-15"), "settlement", cfg)
    assert avail == pd.Timestamp("2024-01-16T03:00:00Z")


def test_settlement_series_input_returns_series():
    s = pd.Series(pd.to_datetime(["2024-09-24", "2024-09-25"]))
    out = infer_available_at(s, "settlement", _SETTLEMENT_CFG)
    assert len(out) == 2
    assert str(out.dt.tz) == "UTC"


# ── Gate enforcement ──────────────────────────────────────────────────────────

def test_missing_settlement_availability_policy_blocks_official_run():
    from run_pipeline import _settlement_availability_status, _enforce_data_integrity_gates

    status = _settlement_availability_status({"family": "futures_options"}, "settlement")
    assert status["status"] == "fail"
    with pytest.raises(ValueError, match="Settlement availability policy"):
        _enforce_data_integrity_gates(
            {"require_fixed_data_version": True}, status, {"status": "not_applicable"}
        )


def test_settlement_policy_present_passes():
    from run_pipeline import _settlement_availability_status

    cfg = {**_SETTLEMENT_CFG, "family": "futures_options"}
    status = _settlement_availability_status(cfg, "settlement")
    assert status["status"] == "pass"
    assert status["exchange_tz"] == "America/New_York"


def test_unknown_iv_unit_blocks_official_export():
    from run_pipeline import _unit_assumptions_status, _enforce_data_integrity_gates

    bad = {"iv": {"raw_unit": "furlongs", "smoke": {"status": "not_checked"}}}
    status = _unit_assumptions_status(bad, is_options=True)
    assert status["status"] == "fail"
    with pytest.raises(ValueError, match="IV unit assumption failed"):
        _enforce_data_integrity_gates(
            {"require_fixed_data_version": True}, {"status": "not_applicable"}, status
        )


def test_iv_smoke_fail_blocks_official_export():
    from run_pipeline import _unit_assumptions_status

    bad = {"iv": {"raw_unit": "decimal", "smoke": {"status": "fail", "reason": "percent-as-decimal"}}}
    status = _unit_assumptions_status(bad, is_options=True)
    assert status["status"] == "fail"


def test_exploratory_run_does_not_block():
    from run_pipeline import _enforce_data_integrity_gates

    # require_fixed_data_version=False → exploratory; gates do not raise.
    _enforce_data_integrity_gates(
        {"require_fixed_data_version": False},
        {"status": "fail", "reason": "x"},
        {"status": "fail", "reason": "y"},
    )


# ── Loader integration: raw IV preserved + availability anchored ───────────────

def _write_settlement_file(path):
    path.write_text(
        "\n".join([
            "TRADE DATE|HUB|PRODUCT|STRIP|CONTRACT|CONTRACT TYPE|STRIKE|SETTLEMENT PRICE|NET CHANGE|EXPIRATION DATE|PRODUCT_ID|OPTION_VOLATILITY|DELTA_FACTOR",
            "9/25/2024|North Sea|Brent Crude Futures|11/1/2024|B|F||63.46000|0.00000|11/1/2024|254||",
            "9/25/2024|North Sea|Brent Crude Futures|11/1/2024|B|C|65.0000|2.50000|0.00000|10/17/2024|254|25.00000|0.40000",
        ]),
        encoding="utf-8",
    )
    return path


def test_settlement_loader_preserves_raw_iv_and_records_assumption(tmp_path):
    from ingestion.settlement_loader import SettlementLoader
    from ingestion.symbology import Symbology

    path = _write_settlement_file(tmp_path / "settle.csv")
    loader = SettlementLoader(Symbology(), cfg={**_SETTLEMENT_CFG, "iv_raw_unit": "percent"})
    out = loader.fetch(str(path), "2024-09-25", "2024-09-26")

    opt = out[out["instrument_type"] == "option"].iloc[0]
    assert opt["iv_provided_raw"] == 25.0          # raw percent preserved
    assert abs(opt["iv_provided"] - 0.25) < 1e-9   # canonical decimal
    assert opt["iv_raw_unit"] == "percent"

    iv_assumption = loader.unit_assumptions["iv"]
    assert iv_assumption["scale_factor"] == 0.01
    assert iv_assumption["canonical_unit"] == "decimal"
    assert iv_assumption["smoke"]["status"] == "pass"


def test_settlement_loader_availability_is_not_midnight(tmp_path):
    from ingestion.settlement_loader import SettlementLoader
    from ingestion.symbology import Symbology

    path = _write_settlement_file(tmp_path / "settle.csv")
    loader = SettlementLoader(Symbology(), cfg=_SETTLEMENT_CFG)
    out = loader.fetch(str(path), "2024-09-25", "2024-09-26")

    # 14:30 ET = 18:30Z, far from the old midnight+3h = 03:00Z anchor.
    assert (out["available_at"] == pd.Timestamp("2024-09-25T18:30:00Z")).all()
