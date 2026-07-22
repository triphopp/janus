import numpy as np
import pandas as pd
import pytest

from core.rates import (
    DEFAULT_RISK_FREE_RATE,
    resolve_rate,
    simple_act360_to_cc_act365,
    stamp_rate,
)


def test_existing_row_r_wins_over_config_and_default():
    df = pd.DataFrame({"r": [0.031, np.nan]})
    out, summary = resolve_rate(df, {"rf_rate": 0.044}, default=0.055)

    assert out.iloc[0] == pytest.approx(0.031)
    assert out.iloc[1] == pytest.approx(0.044)
    assert summary["existing_r_rows"] == 1
    assert summary["configured_rows"] == 1
    assert summary["fallback_rows"] == 0


def test_fallback_is_central_and_loud():
    df = pd.DataFrame({"x": [1, 2]})
    out, summary = resolve_rate(df)

    assert out.tolist() == pytest.approx([DEFAULT_RISK_FREE_RATE, DEFAULT_RISK_FREE_RATE])
    assert summary["status"] == "warn"
    assert summary["fallback_rows"] == 2
    assert summary["coverage_pct"] == pytest.approx(0.0)
    assert summary["resolved_coverage_pct"] == pytest.approx(1.0)
    assert summary["warnings"]


def test_stamp_rate_returns_copy_with_r_and_summary():
    df = pd.DataFrame({"x": [1]})
    stamped, summary = stamp_rate(df, {"rf_rate": 0.042})

    assert "r" not in df.columns
    assert stamped["r"].iloc[0] == pytest.approx(0.042)
    assert summary["source_used"] == "configured_rf_rate"


def test_simple_act360_to_cc_act365_conversion():
    rate = 0.05
    expected = 365.0 * np.log1p(rate / 360.0)
    assert simple_act360_to_cc_act365(rate) == pytest.approx(expected)


def test_sofr_pit_join_rejects_future_available_rate():
    df = pd.DataFrame({
        "as_of_date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "available_at": pd.to_datetime([
            "2024-01-02T15:00:00Z",
            "2024-01-03T15:00:00Z",
        ]),
    })
    rates = pd.DataFrame({
        "date": ["2024-01-01", "2024-01-02"],
        "available_at": [
            "2024-01-01T16:00:00Z",
            "2024-01-02T16:00:00Z",
        ],
        "rate": [0.036, 0.072],
    })

    out, summary = resolve_rate(df, {"rf_rate_source": "sofr", "rates": rates})

    assert out.iloc[0] == pytest.approx(simple_act360_to_cc_act365(0.036))
    assert out.iloc[1] == pytest.approx(simple_act360_to_cc_act365(0.072))
    assert summary["status"] == "pass"
    assert summary["sourced_rows"] == 2
    assert summary["source_coverage_pct"] == pytest.approx(1.0)
    assert summary["fallback_rows"] == 0


def test_configured_sofr_without_table_is_visible_failure_with_fallback():
    df = pd.DataFrame({"as_of_date": pd.to_datetime(["2024-01-02"])})

    out, summary = resolve_rate(df, {"rf_rate_source": "sofr"})

    assert out.iloc[0] == pytest.approx(DEFAULT_RISK_FREE_RATE)
    assert summary["status"] == "fail"
    assert summary["source_status"] == "missing_source"
    assert summary["fallback_rows"] == 1


def test_public_safe_sofr_fixture_path_covers_all_rows():
    df = pd.DataFrame({
        "as_of_date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "available_at": pd.to_datetime([
            "2024-01-02T15:00:00Z",
            "2024-01-03T15:00:00Z",
        ]),
    })

    out, summary = resolve_rate(df, {
        "rf_rate_source": "sofr",
        "sofr_path": "tests/fixtures/rates/sofr_public_safe.csv",
    })

    assert out.iloc[0] == pytest.approx(simple_act360_to_cc_act365(0.036))
    assert out.iloc[1] == pytest.approx(simple_act360_to_cc_act365(0.072))
    assert summary["status"] == "pass"
    assert summary["source_coverage_pct"] == pytest.approx(1.0)
    assert summary["fallback_rows"] == 0
