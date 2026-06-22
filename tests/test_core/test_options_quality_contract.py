"""Executable contract tests for planned core.options_quality summary."""

import pandas as pd
import pytest


def test_options_quality_summary_handles_missing_optional_columns():
    from core import options_quality

    df = pd.DataFrame({
        "instrument_type": ["option", "option", "future"],
        "iv": [0.2, float("nan"), float("nan")],
    })

    out = options_quality.summarize(df, {}, {
        "universe_drop_rows": 2,
        "universe_drop_by_reason": {"dte_above_max": 2},
    })

    assert out["option_rows"] == 2
    assert out["support_future_rows"] == 1
    assert out["iv"]["null_rate"] == 0.5
    assert out["universe"]["drop_rows"] == 2
    assert out["universe"]["drop_by_reason"] == {"dte_above_max": 2}


def test_options_quality_summary_reports_iv_delta_and_pcp_rates():
    from core import options_quality

    df = pd.DataFrame({
        "instrument_type": ["option", "option", "option", "future"],
        "right": ["C", "P", "C", None],
        "iv": [0.2, float("nan"), 0.4, float("nan")],
        "iv_solved": [0.21, float("nan"), 0.39, float("nan")],
        "iv_flag": [False, False, True, False],
        "delta": [0.5, -0.4, -0.1, float("nan")],
        "_pcp_flag": [False, True, False, False],
        "pcp_pair_missing": [False, True, True, False],
        "pcp_duplicate_pair": [False, False, False, False],
    })

    out = options_quality.summarize(df, {}, None)

    assert out["option_rows"] == 3
    assert out["iv"]["null_rate"] == pytest.approx(1 / 3)
    assert out["iv"]["solve_fail_rate"] == pytest.approx(1 / 3)
    assert out["iv"]["flag_rate"] == pytest.approx(1 / 3)
    assert out["delta"]["coverage_rate"] == 1.0
    assert out["delta"]["bad_sign_count"] == 1
    assert out["pcp"]["flag_rate"] == pytest.approx(1 / 3)
    assert out["pcp"]["pair_missing_rate"] == pytest.approx(2 / 3)
