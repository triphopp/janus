"""Stage 4 — Overfitting detection (Lopez de Prado).

Prevents false discovery from testing many strategy variants.
Deflated Sharpe Ratio + Probability of Backtest Overfitting + Min Track Record.
"""

from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats


def deflated_sharpe_ratio(
    sr: float,
    n_trials: int,
    T: int,
    skew: float = 0.0,
    kurt: float = 3.0,
) -> dict:
    """Deflated Sharpe Ratio — adjusts for multiple testing.

    PSR = Prob[SR* > E[max(SR)]] where SR* is the true Sharpe
    and the max is over n_trials independent trials.

    Implementation follows Lopez de Prado & Bailey (2014).

    Args:
        sr: observed Sharpe ratio (annualized)
        n_trials: number of independent strategy trials
        T: number of observations
        skew: return skewness
        kurt: return kurtosis (excess kurtosis + 3)

    Returns:
        dict with dsr, p_value, is_significant
    """
    if T < 4:
        return {"dsr": None, "p_value": None, "is_significant": None, "error": "insufficient data"}

    # Expected maximum SR under null (no skill)
    # E[max] ≈ sqrt(2 * log(n_trials)) * (1 - gamma / (2 * log(n_trials)))
    # where gamma ≈ 0.5772 (Euler-Mascheroni)
    if n_trials <= 0:
        emax = 0.0
    else:
        log_n = max(np.log(n_trials), 0.0)
        emax = np.sqrt(2 * log_n) * (1 - 0.5772156649 / (2 * log_n)) if log_n > 0 else 0.0

    # Standard error of SR estimate (adjusted for non-normality)
    sr_se = np.sqrt(
        (1 + 0.5 * sr ** 2 - skew * sr + (kurt - 3) / 4 * sr ** 2) / T
    )

    # PSR
    if sr_se > 0:
        dsr = (sr - emax) / sr_se
        p_value = 1 - stats.norm.cdf(dsr)
    else:
        dsr = 0.0
        p_value = 0.5

    return {
        "dsr": dsr,
        "p_value": p_value,
        "is_significant": p_value < 0.05,
        "expected_max_sr": emax,
        "sr_standard_error": sr_se,
    }


def prob_backtest_overfitting(
    ret_matrix: pd.DataFrame,
    n_splits: Optional[int] = None,
) -> dict:
    """Probability of Backtest Overfitting (PBO).

    PBO = probability that the best in-sample model underperforms
    out-of-sample relative to the median model.

    Args:
        ret_matrix: DataFrame where rows = trials, cols = time periods (OOS returns)
        n_splits: number of combinatoric splits (default: all combinations)

    Returns:
        dict with pbo, n_combinations, best_is_rank_os
    """
    from itertools import combinations

    n_trials, n_periods = ret_matrix.shape
    if n_trials < 2 or n_periods < 2:
        return {"pbo": None, "error": "need >= 2 trials and >= 2 periods"}

    cols = list(range(n_periods))
    if n_splits is None:
        # All possible 50/50 IS-OOS splits
        split_point = n_periods // 2
        all_is = list(combinations(cols, split_point))
        all_is = all_is[:100]  # Cap for performance
    else:
        all_is = [np.random.choice(cols, size=n_periods // 2, replace=False)
                  for _ in range(n_splits)]

    values = ret_matrix.values
    overfit_count = 0
    total = 0

    for is_cols in all_is:
        is_cols = list(is_cols)
        oos_cols = [c for c in cols if c not in is_cols]

        is_perf = values[:, is_cols].mean(axis=1)  # SR per trial IS
        oos_perf = values[:, oos_cols].mean(axis=1)  # SR per trial OOS

        best_is = np.argmax(is_perf)
        median_oos_rank = stats.rankdata(oos_perf)[best_is] / n_trials

        # Best IS model underperforms median OOS
        if oos_perf[best_is] < np.median(oos_perf):
            overfit_count += 1
        total += 1

    pbo = overfit_count / total if total > 0 else 0.5

    return {
        "pbo": pbo,
        "n_combinations": total,
        "interpretation": "likely_overfit" if pbo > 0.5 else "likely_robust",
    }


def min_track_record_length(
    sr: float,
    target_sr: float = 0.0,
    skew: float = 0.0,
    kurt: float = 3.0,
    alpha: float = 0.05,
) -> int:
    """Minimum track record length to be confident SR > target.

    Answers: how many observations needed to reject H0: SR <= target?

    Args:
        sr: observed Sharpe ratio
        target_sr: minimum acceptable Sharpe (usually 0)
        skew: return skewness
        kurt: return kurtosis
        alpha: significance level

    Returns:
        Minimum number of observations
    """
    if sr <= target_sr:
        return np.inf  # Never significant

    z_alpha = stats.norm.ppf(1 - alpha)

    # Standard error formula incorporates non-normality
    var_sr = 1 + 0.5 * sr ** 2 - skew * sr + (kurt - 3) / 4 * sr ** 2

    min_n = var_sr * (z_alpha / (sr - target_sr)) ** 2

    return int(np.ceil(min_n))
