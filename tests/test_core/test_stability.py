"""v1.3 tests: stage 2 stability and feature-quality checks."""

import numpy as np
import pandas as pd

from core.stability import (
    distribution_shift,
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
