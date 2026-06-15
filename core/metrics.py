"""Stage 4 — Full metric set + per-fold/per-regime breakdown.

Six categories: Return, Risk-adjusted, Drawdown, Distribution, Tail, Hit/Consistency.
Every metric computed at aggregate, per-fold, and per-regime levels.
"""

from typing import Optional

import numpy as np
import pandas as pd

EPS = 1e-12


# ── Return metrics ──

def return_metrics(returns: pd.Series, periods_per_year: int = 252) -> dict:
    """Basic return metrics.

    Returns:
        dict with total_return, cagr, ann_return, ann_vol, mean_return, median_return
    """
    r = returns.dropna()
    if len(r) == 0:
        return {k: None for k in ["total_return", "cagr", "ann_return", "ann_vol"]}

    total = (1 + r).prod() - 1
    n_years = len(r) / periods_per_year
    cagr = (1 + total) ** (1 / n_years) - 1 if n_years > 0 else 0.0

    return {
        "total_return": total,
        "cagr": cagr,
        "ann_return": r.mean() * periods_per_year,
        "ann_vol": r.std() * np.sqrt(periods_per_year),
        "mean_return": r.mean(),
        "median_return": r.median(),
    }


# ── Risk-adjusted metrics ──

def risk_adjusted(returns: pd.Series, rf: float = 0.0, periods_per_year: int = 252) -> dict:
    """Risk-adjusted return metrics.

    Returns:
        dict with sharpe, sortino, calmar, information_ratio
    """
    r = returns.dropna()
    if len(r) < 2:
        return {k: None for k in ["sharpe", "sortino", "calmar", "information_ratio"]}

    excess = r - rf / periods_per_year
    ann_excess = excess.mean() * periods_per_year
    ann_vol = r.std() * np.sqrt(periods_per_year)

    # Sharpe
    sharpe = ann_excess / ann_vol if ann_vol > EPS else 0.0

    # Sortino (downside deviation only)
    downside = excess[excess < 0]
    if len(downside) > 0:
        downside_vol = downside.std() * np.sqrt(periods_per_year)
        sortino = ann_excess / downside_vol if downside_vol > EPS else 0.0
    else:
        sortino = np.inf

    # Calmar
    cum_returns = (1 + r).cumprod()
    running_max = cum_returns.expanding().max()
    drawdowns = cum_returns / running_max - 1
    max_dd = drawdowns.min()
    calmar = ann_excess / abs(max_dd) if max_dd != 0 else 0.0

    return {
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "information_ratio": sharpe,  # simplified; proper IR needs benchmark
    }


# ── Drawdown metrics ──

def drawdown_metrics(equity: pd.Series) -> dict:
    """Drawdown analysis.

    Returns:
        dict with max_dd, avg_dd, max_dd_duration, ulcer_index
    """
    cum = equity.dropna()
    if len(cum) < 2:
        return {k: None for k in ["max_dd", "avg_dd", "max_dd_duration", "ulcer_index"]}

    running_max = cum.expanding(min_periods=1).max()
    drawdowns = cum / running_max - 1

    max_dd = drawdowns.min()
    avg_dd = drawdowns[drawdowns < 0].mean() if (drawdowns < 0).any() else 0.0

    # Max DD duration
    in_dd = drawdowns < 0
    dd_groups = (in_dd != in_dd.shift()).cumsum()
    max_dd_duration = in_dd.groupby(dd_groups).sum().max() if in_dd.any() else 0

    # Ulcer Index
    ulcer = np.sqrt((drawdowns ** 2).mean())

    return {
        "max_dd": max_dd,
        "avg_dd": avg_dd,
        "max_dd_duration": max_dd_duration,
        "ulcer_index": ulcer,
    }


# ── Distribution metrics ──

def distribution_metrics(returns: pd.Series) -> dict:
    """Distribution characteristics.

    Returns:
        dict with min, max, mean, median, std, skew, kurtosis
    """
    r = returns.dropna()
    if len(r) < 4:
        return {k: None for k in ["min", "max", "mean", "median", "std", "skew", "kurtosis"]}

    return {
        "min": r.min(),
        "max": r.max(),
        "mean": r.mean(),
        "median": r.median(),
        "std": r.std(),
        "skew": float(r.skew()),
        "kurtosis": float(r.kurtosis()),
    }


# ── Tail metrics ──

def tail_metrics(returns: pd.Series, alpha: float = 0.05) -> dict:
    """Tail risk metrics.

    Args:
        alpha: tail probability (default 0.05 = 95% VaR/CVaR)

    Returns:
        dict with var, cvar, worst_day, worst_week
    """
    r = returns.dropna()
    if len(r) < 10:
        return {k: None for k in ["var", "cvar", "worst_day", "worst_week"]}

    var = r.quantile(alpha)
    cvar = r[r <= var].mean()  # Conditional VaR (expected shortfall)

    # Worst day / week
    worst_day = r.min()

    # Approximate weekly from daily (if daily data)
    if len(r) >= 5:
        rolling_week = r.rolling(5).sum().dropna()
        worst_week = rolling_week.min() if len(rolling_week) > 0 else worst_day
    else:
        worst_week = worst_day

    return {
        "var": var,
        "cvar": cvar,
        "worst_day": worst_day,
        "worst_week": worst_week,
    }


# ── Hit / consistency metrics ──

def hit_metrics(returns: pd.Series) -> dict:
    """Hit rate and consistency metrics.

    Returns:
        dict with win_rate, profit_factor, payoff_ratio, longest_losing_streak
    """
    r = returns.dropna()
    if len(r) < 2:
        return {k: None for k in ["win_rate", "profit_factor", "payoff_ratio", "longest_losing_streak"]}

    wins = r[r > 0]
    losses = r[r < 0]

    win_rate = len(wins) / len(r) if len(r) > 0 else 0.0

    total_gain = wins.sum() if len(wins) > 0 else 0.0
    total_loss = abs(losses.sum()) if len(losses) > 0 else 0.0
    profit_factor = total_gain / total_loss if total_loss > 0 else np.inf

    avg_win = wins.mean() if len(wins) > 0 else 0.0
    avg_loss = abs(losses.mean()) if len(losses) > 0 else 0.0
    payoff_ratio = avg_win / avg_loss if avg_loss > 0 else np.inf

    # Longest losing streak
    streak = 0
    max_streak = 0
    for val in r:
        if val <= 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    return {
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "payoff_ratio": payoff_ratio,
        "longest_losing_streak": max_streak,
    }


# ── Per-fold / per-regime breakdown ──

def per_fold_breakdown(
    fold_returns: dict,
    regime_labels: Optional[pd.Series] = None,
    periods_per_year: int = 252,
) -> pd.DataFrame:
    """One row per fold — see actual dispersion, not just aggregate mean.

    Args:
        fold_returns: dict of {fold_id: pd.Series of returns}
        regime_labels: Series indexed same as returns (optional)

    Returns:
        DataFrame with columns: fold, date_range, dominant_regime,
        total_return, sharpe, sortino, max_dd, cvar_95, hit_rate, worst_day
    """
    rows = []
    for fid, r in fold_returns.items():
        r = r.dropna()
        if len(r) < 2:
            continue

        row = {
            "fold": fid,
            "date_range": (r.index[0], r.index[-1]),
            "dominant_regime": regime_labels.loc[r.index].mode().iloc[0]
            if regime_labels is not None and len(r.index.intersection(regime_labels.index)) > 0
            else None,
            "total_return": (1 + r).prod() - 1,
            "sharpe": risk_adjusted(r, periods_per_year=periods_per_year)["sharpe"],
            "sortino": risk_adjusted(r, periods_per_year=periods_per_year)["sortino"],
            "max_dd": drawdown_metrics((1 + r).cumprod())["max_dd"],
            "cvar_95": tail_metrics(r, 0.05)["cvar"],
            "hit_rate": (r > 0).mean(),
            "worst_day": r.min(),
        }
        rows.append(row)

    return pd.DataFrame(rows)


def per_regime_breakdown(
    returns: pd.Series,
    regime_labels: pd.Series,
    periods_per_year: int = 252,
) -> pd.DataFrame:
    """One row per regime — performance by market condition.

    Args:
        returns: strategy returns
        regime_labels: regime label per return observation
        periods_per_year: annualization factor

    Returns:
        DataFrame with columns: regime, n_obs, total_return, sharpe, sortino, max_dd
    """
    rows = []
    for regime_name, idx in regime_labels.groupby(regime_labels).groups.items():
        r = returns.reindex(idx).dropna()
        if len(r) < 5:
            continue

        row = {
            "regime": regime_name,
            "n_obs": len(r),
            "total_return": (1 + r).prod() - 1,
            "sharpe": risk_adjusted(r, periods_per_year=periods_per_year)["sharpe"],
            "sortino": risk_adjusted(r, periods_per_year=periods_per_year)["sortino"],
            "max_dd": drawdown_metrics((1 + r).cumprod())["max_dd"],
            "hit_rate": (r > 0).mean(),
        }
        rows.append(row)

    return pd.DataFrame(rows)


def stability_score(per_fold: pd.DataFrame) -> dict:
    """Measure consistency across folds.

    High mean but high variance = fragile strategy.

    Returns:
        dict with sharpe_mean, sharpe_std, sharpe_min, pct_profitable_folds, worst_fold_return
    """
    if per_fold.empty or "sharpe" not in per_fold.columns:
        return {}

    s = per_fold["sharpe"].dropna()
    tr = per_fold["total_return"].dropna()

    return {
        "sharpe_mean": s.mean(),
        "sharpe_std": s.std(),
        "sharpe_min": s.min(),
        "pct_profitable_folds": (tr > 0).mean(),
        "worst_fold_return": tr.min(),
    }
