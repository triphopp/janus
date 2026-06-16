"""v1.3 tests: stage 2 stability and feature-quality checks."""

import numpy as np
import pandas as pd

from core.stability import (
    adf_kpss_check,
    distribution_shift,
    fold_distribution_shift,
    hurst_exponent,
    information_coefficient,
    jarque_bera,
    variance_ratio_test,
    vif_condition_number,
)


def test_variance_ratio_reports_numeric_stat():
    series = pd.Series(np.cumsum(np.arange(80, dtype=float)))
    result = variance_ratio_test(series, q=2)

    assert result["vr_stat"] is not None
    assert result["interpretation"] in {"mean_reverting", "trending", "random_walk"}


def test_variance_ratio_return_input_uses_return_aggregation():
    rng = np.random.default_rng(1)
    returns = pd.Series(rng.normal(0, 0.01, 500))
    result = variance_ratio_test(returns, q=2, input_kind="return_series")

    assert result["input_kind"] == "return_series"
    assert 0.8 < result["vr_stat"] < 1.2
    assert result["interpretation"] == "random_walk"


def test_adf_kpss_constant_series_returns_unknown():
    result = adf_kpss_check(pd.Series([0.0] * 50))

    assert result["is_stationary"] is None
    assert result["status"] == "constant_series"


def test_hurst_insufficient_data_not_random_walk():
    result = hurst_exponent(pd.Series([0.01, -0.02, 0.0, 0.01]))

    assert result["hurst"] is None
    assert result["status"] == "insufficient_data"


def test_jarque_bera_returns_shape_and_normality_fields():
    rng = np.random.default_rng(42)
    series = pd.Series(rng.normal(size=200))
    result = jarque_bera(series)

    assert result["jb_stat"] >= 0
    assert 0 <= result["jb_pval"] <= 1
    assert "skew" in result
    assert "kurtosis" in result


def test_distribution_shift_flags_large_mean_shift():
    train = pd.Series(np.zeros(100))
    val = pd.Series(np.ones(100) * 10)
    result = distribution_shift(train, val, {"psi_threshold": 0.25})

    assert result["psi"] >= 0
    assert result["has_shift"]


def test_distribution_shift_uses_config_threshold():
    train = pd.Series(np.r_[np.zeros(80), np.ones(20)])
    val = pd.Series(np.r_[np.zeros(60), np.ones(40)])

    loose = distribution_shift(train, val, {"psi_threshold": 10.0})
    strict = distribution_shift(train, val, {"psi_threshold": 0.0001})

    assert not loose["has_shift"]
    assert strict["has_shift"]


def test_fold_distribution_shift_uses_actual_folds():
    series = pd.Series([0.0] * 20 + [10.0] * 20)
    folds = [(np.arange(20), np.arange(20, 40))]

    result = fold_distribution_shift(series, folds, {"psi_threshold": 0.25})

    assert result["worst"]["fold"] == 0
    assert result["worst"]["has_shift"]
    assert result["folds"][0]["has_shift"]


def test_information_coefficient_detects_monotonic_relationship():
    pred = pd.Series(np.arange(50, dtype=float))
    fwd = pred * 2
    result = information_coefficient(pred, fwd)

    assert result["ic_pearson"] > 0.99
    assert result["ic_spearman"] > 0.99


def test_vif_condition_number_returns_columns():
    df = pd.DataFrame({
        "x": np.arange(1, 60, dtype=float),
        "y": np.arange(1, 60, dtype=float) * 2,
        "z": np.sin(np.arange(1, 60, dtype=float)),
    })
    result = vif_condition_number(df)

    assert set(result["vif_per_column"]).issuperset({"x", "y", "z"})
    assert result["condition_number"] is not None
