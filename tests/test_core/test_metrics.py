"""Metrics tests — numeric stability, edge cases."""

import numpy as np
import pandas as pd
from core.metrics import (
    return_metrics, risk_adjusted, drawdown_metrics,
    distribution_metrics, tail_metrics, hit_metrics,
    stability_score, per_fold_breakdown, per_regime_breakdown,
)


class TestNumericStability:
    """Edge cases that can produce NaN or inf."""

    def test_sharpe_of_constant_return(self, sample_returns):
        """Constant returns → std=0 → Sharpe should be 0, not inf."""
        const = pd.Series([0.001] * 100)
        result = risk_adjusted(const)
        assert result["sharpe"] == 0.0  # zero std → zero sharpe (not inf)

    def test_max_dd_of_monotonic_up(self):
        """Monotonically increasing equity → max DD = 0."""
        equity = pd.Series(np.linspace(100, 200, 100))
        result = drawdown_metrics(equity)
        assert result["max_dd"] == 0.0

    def test_max_dd_of_monotonic_down(self):
        """Monotonically decreasing equity → max DD < 0."""
        equity = pd.Series(np.linspace(200, 100, 100))
        result = drawdown_metrics(equity)
        assert result["max_dd"] < 0

    def test_empty_input(self):
        """Empty series should not crash."""
        empty = pd.Series([], dtype=float)
        result = return_metrics(empty)
        assert result["total_return"] is None

    def test_single_observation(self):
        """Single observation should not crash."""
        single = pd.Series([0.01])
        result = risk_adjusted(single)
        assert result["sharpe"] is None


class TestPerFoldBreakdown:
    """Per-fold breakdown must show actual dispersion."""

    def test_breakdown_structure(self, sample_returns):
        """Per-fold breakdown must have required columns."""
        r = sample_returns
        fold_returns = {0: r[:100], 1: r[100:200], 2: r[200:300]}
        result = per_fold_breakdown(fold_returns)
        expected_cols = {"fold", "total_return", "sharpe", "sortino", "max_dd", "cvar_95", "hit_rate", "worst_day"}
        assert expected_cols.issubset(set(result.columns))
        assert len(result) == 3

    def test_stability_score_range(self, sample_returns):
        """Stability score on heterogeneous fold returns."""
        r = sample_returns
        fold_returns = {0: r[:100], 1: r[100:200], 2: r[200:300]}
        per_fold = per_fold_breakdown(fold_returns)
        score = stability_score(per_fold)
        assert "sharpe_mean" in score
        assert "pct_profitable_folds" in score
        assert 0 <= score["pct_profitable_folds"] <= 1


class TestPerRegimeBreakdown:
    """Per-regime breakdown for market condition analysis."""

    def test_breakdown_groups_by_regime_label_series(self):
        idx = pd.date_range("2024-01-01", periods=12)
        returns = pd.Series([0.01, -0.002, 0.004, 0.003, 0.002, 0.001, -0.01, -0.004, 0.002, 0.001, -0.003, 0.002], index=idx)
        regimes = pd.Series(["calm"] * 6 + ["stress"] * 6, index=idx)

        result = per_regime_breakdown(returns, regimes)

        assert set(result["regime"]) == {"calm", "stress"}
        assert set(result["n_obs"]) == {6}


class TestHitMetrics:
    """Hit/conistency metrics."""

    def test_all_wins(self):
        """All positive returns → win_rate = 1, no losing streak."""
        r = pd.Series([0.01] * 50)
        result = hit_metrics(r)
        assert result["win_rate"] == 1.0
        assert result["longest_losing_streak"] == 0

    def test_all_losses(self):
        """All negative returns → win_rate = 0."""
        r = pd.Series([-0.01] * 50)
        result = hit_metrics(r)
        assert result["win_rate"] == 0.0
        assert result["profit_factor"] == 0.0  # no gains
