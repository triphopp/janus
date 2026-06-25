"""Tests for core/greek_inputs.py — Phase 1 of greek_only_engine plan."""

import numpy as np
import pandas as pd
import pytest

from core.greek_inputs import resolve_greek_inputs


def _base_row(**kwargs):
    row = {
        "underlying_price": 80.0,
        "K": 80.0,
        "T": 0.5,
        "r": 0.05,
        "iv": 0.3,
        "right": "C",
    }
    row.update(kwargs)
    return pd.DataFrame([row])


class TestUnderlyingPrecedence:
    def test_uses_underlying_price_first(self):
        df = pd.DataFrame([{"underlying_price": 80.0, "S": 90.0, "K": 80.0, "T": 0.5, "iv": 0.3, "right": "C"}])
        out, _ = resolve_greek_inputs(df)
        assert out["S_or_F"].iloc[0] == pytest.approx(80.0)

    def test_falls_back_to_S(self):
        df = pd.DataFrame([{"S": 90.0, "K": 80.0, "T": 0.5, "iv": 0.3, "right": "C"}])
        out, _ = resolve_greek_inputs(df)
        assert out["S_or_F"].iloc[0] == pytest.approx(90.0)

    def test_falls_back_to_F(self):
        df = pd.DataFrame([{"F": 100.0, "K": 80.0, "T": 0.5, "iv": 0.3, "right": "C"}])
        out, _ = resolve_greek_inputs(df)
        assert out["S_or_F"].iloc[0] == pytest.approx(100.0)

    def test_falls_back_to_price_std(self):
        df = pd.DataFrame([{"price_std": 75.0, "K": 80.0, "T": 0.5, "iv": 0.3, "right": "C"}])
        out, _ = resolve_greek_inputs(df)
        assert out["S_or_F"].iloc[0] == pytest.approx(75.0)

    def test_missing_underlying_flagged(self):
        df = pd.DataFrame([{"K": 80.0, "T": 0.5, "iv": 0.3, "right": "C"}])
        out, summary = resolve_greek_inputs(df)
        assert summary["invalid_by_reason"]["missing_underlying"] == 1
        assert not out["greek_input_valid"].iloc[0]

    def test_zero_underlying_flagged(self):
        df = _base_row(underlying_price=0.0)
        out, summary = resolve_greek_inputs(df)
        assert summary["invalid_by_reason"]["missing_underlying"] == 1


class TestIVResolution:
    def test_uses_iv_column_by_default(self):
        df = _base_row(iv=0.3)
        out, summary = resolve_greek_inputs(df)
        assert out["sigma"].iloc[0] == pytest.approx(0.3)
        assert summary["invalid_by_reason"]["missing_iv"] == 0

    def test_iv_source_provided_prefers_iv_provided(self):
        df = pd.DataFrame([{"underlying_price": 80.0, "K": 80.0, "T": 0.5, "iv": 0.3, "iv_provided": 0.25, "right": "C"}])
        out, _ = resolve_greek_inputs(df, iv_source="provided")
        assert out["sigma"].iloc[0] == pytest.approx(0.25)

    def test_iv_source_provided_falls_back_to_iv(self):
        df = _base_row(iv=0.3)
        out, _ = resolve_greek_inputs(df, iv_source="provided")
        assert out["sigma"].iloc[0] == pytest.approx(0.3)

    def test_missing_iv_flagged(self):
        df = pd.DataFrame([{"underlying_price": 80.0, "K": 80.0, "T": 0.5, "right": "C"}])
        out, summary = resolve_greek_inputs(df)
        assert summary["invalid_by_reason"]["missing_iv"] == 1
        assert not out["greek_input_valid"].iloc[0]


class TestTResolution:
    def test_uses_T_column_when_present(self):
        df = _base_row(T=0.5)
        out, summary = resolve_greek_inputs(df)
        assert out["T"].iloc[0] == pytest.approx(0.5)
        assert summary["invalid_by_reason"]["missing_or_expired_T"] == 0

    def test_computes_T_from_dates_when_missing(self):
        df = pd.DataFrame([{
            "underlying_price": 80.0, "K": 80.0, "iv": 0.3, "right": "C",
            "as_of_date": "2024-01-01", "expiry": "2024-07-01",
        }])
        out, summary = resolve_greek_inputs(df, dte_cfg={"basis": "calendar", "day_count": "act_365", "exclude_expiry_date": False})
        assert out["T"].iloc[0] > 0
        assert summary["invalid_by_reason"]["missing_or_expired_T"] == 0

    def test_expired_T_flagged(self):
        df = _base_row(T=-0.1)
        out, summary = resolve_greek_inputs(df)
        assert summary["invalid_by_reason"]["missing_or_expired_T"] == 1
        assert not out["greek_input_valid"].iloc[0]

    def test_zero_T_flagged(self):
        df = _base_row(T=0.0)
        out, summary = resolve_greek_inputs(df)
        assert summary["invalid_by_reason"]["missing_or_expired_T"] == 1


class TestRateResolution:
    def test_uses_row_level_r(self):
        df = _base_row(r=0.05)
        out, _ = resolve_greek_inputs(df, rf_rate_default=0.02)
        assert out["r"].iloc[0] == pytest.approx(0.05)

    def test_falls_back_to_cfg_rf_rate(self):
        df = pd.DataFrame([{"underlying_price": 80.0, "K": 80.0, "T": 0.5, "iv": 0.3, "right": "C"}])
        out, _ = resolve_greek_inputs(df, cfg={"rf_rate": 0.04})
        assert out["r"].iloc[0] == pytest.approx(0.04)

    def test_falls_back_to_rf_rate_default(self):
        df = pd.DataFrame([{"underlying_price": 80.0, "K": 80.0, "T": 0.5, "iv": 0.3, "right": "C"}])
        out, _ = resolve_greek_inputs(df, rf_rate_default=0.03)
        assert out["r"].iloc[0] == pytest.approx(0.03)

    def test_nan_r_replaced_by_cfg(self):
        df = _base_row(r=float("nan"))
        out, _ = resolve_greek_inputs(df, cfg={"rf_rate": 0.04})
        assert out["r"].iloc[0] == pytest.approx(0.04)


class TestRightResolution:
    def test_right_column_uppercased(self):
        df = _base_row(right="c")
        out, _ = resolve_greek_inputs(df)
        assert out["right"].iloc[0] == "C"

    def test_option_type_call_mapped(self):
        df = pd.DataFrame([{"underlying_price": 80.0, "K": 80.0, "T": 0.5, "iv": 0.3, "option_type": "CALL"}])
        out, _ = resolve_greek_inputs(df)
        assert out["right"].iloc[0] == "C"

    def test_bad_right_flagged(self):
        df = _base_row(right="X")
        out, summary = resolve_greek_inputs(df)
        assert summary["invalid_by_reason"]["bad_right"] == 1
        assert not out["greek_input_valid"].iloc[0]


class TestNoMutation:
    def test_does_not_mutate_input(self):
        df = _base_row()
        cols_before = list(df.columns)
        resolve_greek_inputs(df)
        assert list(df.columns) == cols_before

    def test_original_values_unchanged(self):
        df = _base_row(underlying_price=80.0)
        resolve_greek_inputs(df)
        assert df["underlying_price"].iloc[0] == pytest.approx(80.0)


class TestSummary:
    def test_valid_row_counted(self):
        df = _base_row()
        _, summary = resolve_greek_inputs(df)
        assert summary["total_rows"] == 1
        assert summary["valid_rows"] == 1
        assert summary["invalid_rows"] == 0

    def test_multiple_invalid_reasons_counted_independently(self):
        df = pd.DataFrame([
            {"K": 80.0, "T": 0.5, "right": "C"},  # missing under + missing iv
        ])
        _, summary = resolve_greek_inputs(df)
        assert summary["invalid_by_reason"]["missing_underlying"] == 1
        assert summary["invalid_by_reason"]["missing_iv"] == 1
        assert summary["invalid_rows"] == 1  # row counted once even with multiple reasons

    def test_mixed_valid_invalid(self):
        df = pd.DataFrame([
            {"underlying_price": 80.0, "K": 80.0, "T": 0.5, "iv": 0.3, "right": "C"},
            {"K": 80.0, "T": 0.5, "iv": 0.3, "right": "C"},  # missing underlying
        ])
        _, summary = resolve_greek_inputs(df)
        assert summary["valid_rows"] == 1
        assert summary["invalid_rows"] == 1


class TestNumericCoercion:
    """Bad string values must become invalid rows, never crash."""

    def test_bad_underlying_price_is_missing_underlying(self):
        df = _base_row(underlying_price="bad")
        out, summary = resolve_greek_inputs(df)
        assert summary["invalid_by_reason"]["missing_underlying"] == 1
        assert not out["greek_input_valid"].iloc[0]

    def test_bad_K_is_missing_strike(self):
        df = _base_row(K="bad")
        out, summary = resolve_greek_inputs(df)
        assert summary["invalid_by_reason"]["missing_strike"] == 1
        assert not out["greek_input_valid"].iloc[0]

    def test_bad_iv_is_missing_iv(self):
        df = _base_row(iv="bad")
        out, summary = resolve_greek_inputs(df)
        assert summary["invalid_by_reason"]["missing_iv"] == 1
        assert not out["greek_input_valid"].iloc[0]

    def test_bad_T_is_missing_or_expired_T(self):
        df = _base_row(T="bad")
        out, summary = resolve_greek_inputs(df)
        assert summary["invalid_by_reason"]["missing_or_expired_T"] == 1
        assert not out["greek_input_valid"].iloc[0]

    def test_bad_r_falls_back_to_default(self):
        df = _base_row(r="bad")
        out, _ = resolve_greek_inputs(df, rf_rate_default=0.03)
        assert out["r"].iloc[0] == pytest.approx(0.03)

    def test_bad_row_does_not_crash_valid_batch(self):
        df = pd.DataFrame([
            {"underlying_price": 80.0, "K": 80.0, "T": 0.5, "iv": 0.3, "right": "C"},
            {"underlying_price": "bad", "K": 80.0, "T": 0.5, "iv": 0.3, "right": "C"},
        ])
        out, summary = resolve_greek_inputs(df)
        assert out["greek_input_valid"].iloc[0]
        assert not out["greek_input_valid"].iloc[1]
        assert summary["valid_rows"] == 1


class TestInvalidReason:
    """greek_invalid_reason column carries human-readable cause."""

    def test_valid_row_has_empty_reason(self):
        df = _base_row()
        out, _ = resolve_greek_inputs(df)
        assert out["greek_invalid_reason"].iloc[0] == ""

    def test_missing_underlying_reason(self):
        df = pd.DataFrame([{"K": 80.0, "T": 0.5, "iv": 0.3, "right": "C"}])
        out, _ = resolve_greek_inputs(df)
        assert "missing_underlying" in out["greek_invalid_reason"].iloc[0]

    def test_multiple_reasons_joined_with_semicolon(self):
        df = pd.DataFrame([{"K": 80.0, "T": 0.5, "right": "C"}])  # missing underlying + iv
        out, _ = resolve_greek_inputs(df)
        reasons = out["greek_invalid_reason"].iloc[0]
        assert "missing_underlying" in reasons
        assert "missing_iv" in reasons
        assert ";" in reasons
