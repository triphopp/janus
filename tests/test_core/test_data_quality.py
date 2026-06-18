import pandas as pd
import pytest

from core import data_quality


def test_scorecard_combines_validator_contract_and_coverage_rates():
    df = pd.DataFrame({
        "_return_outlier_flag": [False, True, False, False],
        "_outlier_flag": [False, False, False, False],
        "_bound_flag": [False, False, True, False],
        "_missing_flag": [False, False, False, False],
    })

    out = data_quality.build_scorecard(
        df,
        {},
        contract_gate={"quarantine_rate": 0.02, "rows_quarantined": 2, "rows_in": 100},
        coverage_gate={"coverage_ratio": 0.90, "expected_trading_days": 100, "present_trading_days": 90},
    )

    assert out["status"] == "fail"
    assert out["worst_dimension"] in {"return_outlier_rate", "bound_violation_rate"}
    dims = {d["name"]: d for d in out["dimensions"]}
    assert dims["coverage_shortfall"]["rate"] == 0.1
    assert dims["quarantine_rate"]["n_defect"] == 2


def test_scorecard_fail_enforcement_raises():
    scorecard = {"status": "fail", "enforcement": "fail", "worst_dimension": "missing_rate"}

    with pytest.raises(data_quality.DataQualityViolation):
        data_quality.enforce_scorecard(scorecard)
