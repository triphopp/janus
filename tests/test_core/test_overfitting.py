"""v1.3 tests: overfitting controls."""

import numpy as np
import pandas as pd

from core.overfitting import (
    deflated_sharpe_ratio,
    min_track_record_length,
    prob_backtest_overfitting,
)


def test_deflated_sharpe_penalizes_more_trials():
    few = deflated_sharpe_ratio(sr=1.5, n_trials=1, T=252)
    many = deflated_sharpe_ratio(sr=1.5, n_trials=100, T=252)

    assert few["expected_max_sr"] < many["expected_max_sr"]
    assert few["dsr"] > many["dsr"]


def test_deflated_sharpe_insufficient_data_returns_error():
    result = deflated_sharpe_ratio(sr=1.0, n_trials=10, T=3)
    assert result["dsr"] is None
    assert "insufficient data" in result["error"]


def test_prob_backtest_overfitting_is_bounded():
    ret_matrix = pd.DataFrame([
        [0.01, 0.02, -0.01, -0.02],
        [0.00, 0.01, 0.00, 0.01],
        [-0.01, -0.02, 0.02, 0.03],
    ])

    result = prob_backtest_overfitting(ret_matrix)

    assert 0.0 <= result["pbo"] <= 1.0
    assert result["n_combinations"] > 0
    assert result["interpretation"] in {"likely_overfit", "likely_robust"}


def test_min_track_record_length_edge_cases():
    assert np.isinf(min_track_record_length(sr=0.0, target_sr=0.0))
    assert min_track_record_length(sr=1.0, target_sr=0.0) > 0
