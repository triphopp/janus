"""Stage 2 stability checks — stationarity, distribution shift, feature quality.

v1.3 expanded: Variance Ratio, Ljung-Box, Jarque-Bera, Hurst exponent,
Information Coefficient, VIF condition number.
All tests are asset-agnostic — pure statistical functions.
"""

from typing import Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.tsa.stattools import adfuller, kpss


def adf_kpss_check(series: pd.Series, alpha: float = 0.05) -> dict:
    """Combined ADF + KPSS stationarity check.

    ADF H0: unit root (non-stationary). KPSS H0: stationary.
    Consensus: both agree → strong signal.

    Returns:
        dict with adf_stat, adf_pval, kpss_stat, kpss_pval, is_stationary, consensus
    """
    clean = series.dropna()
    if len(clean) < 20:
        return {"is_stationary": None, "error": "insufficient data"}

    # ADF — H0: non-stationary
    adf_result = adfuller(clean, autolag="AIC")
    adf_stat, adf_pval = adf_result[0], adf_result[1]

    # KPSS — H0: stationary
    kpss_result = kpss(clean, regression="c", nlags="auto")
    kpss_stat, kpss_pval = kpss_result[0], kpss_result[1]

    adf_stationary = adf_pval < alpha  # reject unit root → stationary
    kpss_stationary = kpss_pval > alpha  # fail to reject stationarity → stationary
    consensus = "stationary" if (adf_stationary and kpss_stationary) else \
                "non_stationary" if (not adf_stationary and not kpss_stationary) else \
                "mixed"

    return {
        "adf_stat": adf_stat,
        "adf_pval": adf_pval,
        "kpss_stat": kpss_stat,
        "kpss_pval": kpss_pval,
        "is_stationary": adf_stationary,
        "consensus": consensus,
    }


def arch_lm_test(series: pd.Series, lags: int = 10) -> dict:
    """ARCH-LM test for volatility clustering (heteroskedasticity).

    H0: no ARCH effects (homoskedastic).
    Rejection → volatility clustering present.

    Returns:
        dict with lm_stat, lm_pval, has_arch_effects
    """
    from statsmodels.stats.diagnostic import het_arch
    clean = series.dropna()
    if len(clean) < lags + 5:
        return {"has_arch_effects": None, "error": "insufficient data"}
    lm_stat, lm_pval, _, _ = het_arch(clean, nlags=lags)
    return {"lm_stat": lm_stat, "lm_pval": lm_pval, "has_arch_effects": lm_pval < 0.05}


def variance_ratio_test(series: pd.Series, q: int = 2) -> dict:
    """Variance Ratio test for random walk.

    VR(q) = Var(q-period return) / (q * Var(1-period return)).
    Under random walk, VR ≈ 1.
    VR < 1 → mean reversion. VR > 1 → momentum/trending.

    Returns:
        dict with vr_stat, interpretation
    """
    clean = series.dropna()
    if len(clean) < q * 10:
        return {"vr_stat": None, "error": "insufficient data"}

    rets_1 = clean.diff().dropna()
    rets_q = clean.diff(q).dropna()

    var_1 = rets_1.var()
    var_q = rets_q.var()

    if var_1 == 0:
        return {"vr_stat": None, "error": "zero variance"}

    vr = (var_q / q) / var_1
    se = np.sqrt(2 * (2 * q - 1) * (q - 1) / (3 * q * len(rets_q)))
    z_stat = (vr - 1) / se if se > 0 else 0

    interpretation = "mean_reverting" if vr < 0.9 else \
                     "trending" if vr > 1.1 else "random_walk"

    return {"vr_stat": vr, "z_stat": z_stat, "interpretation": interpretation}


def ljung_box(series: pd.Series, lags: int = 10) -> dict:
    """Ljung-Box test for autocorrelation in residuals.

    H0: no autocorrelation up to lag k.
    Use on model residuals — significant autocorrelation = model misspecification.

    Returns:
        dict with lb_stat, lb_pval, has_autocorr
    """
    from statsmodels.stats.diagnostic import acorr_ljungbox
    clean = series.dropna()
    if len(clean) < lags + 5:
        return {"has_autocorr": None, "error": "insufficient data"}
    lb_result = acorr_ljungbox(clean, lags=[lags], return_df=True)
    lb_stat = lb_result["lb_stat"].iloc[0]
    lb_pval = lb_result["lb_pvalue"].iloc[0]
    return {"lb_stat": lb_stat, "lb_pval": lb_pval, "has_autocorr": lb_pval < 0.05}


def jarque_bera(series: pd.Series) -> dict:
    """Jarque-Bera normality test.

    H0: data is normally distributed.
    High JB stat → fat tails or skew.

    Returns:
        dict with jb_stat, jb_pval, skew, kurtosis, is_normal
    """
    clean = series.dropna()
    if len(clean) < 8:
        return {"is_normal": None, "error": "insufficient data"}
    jb_stat, jb_pval = stats.jarque_bera(clean)
    return {
        "jb_stat": jb_stat,
        "jb_pval": jb_pval,
        "skew": float(clean.skew()),
        "kurtosis": float(clean.kurtosis()),
        "is_normal": jb_pval > 0.05,
    }


def hurst_exponent(series: pd.Series, max_lag: int = 100) -> float:
    """Hurst exponent via R/S analysis.

    H ≈ 0.5: random walk
    H > 0.5: trending / persistent
    H < 0.5: mean-reverting / anti-persistent

    Returns:
        Hurst exponent in [0, 1]
    """
    clean = series.dropna().values
    n = len(clean)
    if n < max_lag * 2:
        max_lag = max(10, n // 4)

    lags = range(2, min(max_lag, n // 2))
    rs_values = []
    for lag in lags:
        chunks = n // lag
        if chunks < 2:
            break
        rs = []
        for i in range(chunks):
            chunk = clean[i * lag : (i + 1) * lag]
            dev = chunk - chunk.mean()
            cum_dev = dev.cumsum()
            r = cum_dev.max() - cum_dev.min()
            s = chunk.std()
            if s > 0:
                rs.append(r / s)
        if rs:
            rs_values.append((lag, np.mean(rs)))

    if len(rs_values) < 5:
        return 0.5

    lags_arr = np.log([r[0] for r in rs_values])
    rs_arr = np.log([r[1] for r in rs_values])
    slope, _ = np.polyfit(lags_arr, rs_arr, 1)

    return min(max(slope, 0.0), 1.0)


def distribution_shift(
    train: pd.Series,
    val: pd.Series,
    cfg: dict,
) -> dict:
    """Detect distribution shift between train and validation sets.

    Uses PSI (Population Stability Index), KS test, Wasserstein distance.

    Returns:
        dict with psi, ks_stat, ks_pval, wasserstein, has_shift
    """
    from scipy.stats import ks_2samp, wasserstein_distance

    psi_threshold = cfg.get("psi_threshold", 0.25)

    t = train.dropna()
    v = val.dropna()

    if len(t) < 10 or len(v) < 10:
        return {"has_shift": None, "error": "insufficient data"}

    # PSI
    psi_val = _compute_psi(t, v)

    # KS
    ks_stat, ks_pval = ks_2samp(t, v)

    # Wasserstein
    w_dist = wasserstein_distance(t, v)

    has_shift = psi_val > psi_threshold or ks_pval < 0.01

    return {
        "psi": psi_val,
        "ks_stat": ks_stat,
        "ks_pval": ks_pval,
        "wasserstein": w_dist,
        "has_shift": has_shift,
    }


def information_coefficient(predictions: pd.Series, forward_returns: pd.Series) -> dict:
    """Information Coefficient — correlation between predictions and actual forward returns.

    IC (Pearson) and Rank IC (Spearman).

    Returns:
        dict with ic_pearson, ic_spearman
    """
    mask = predictions.notna() & forward_returns.notna()
    p = predictions[mask]
    f = forward_returns[mask]
    if len(p) < 5:
        return {"ic_pearson": None, "ic_spearman": None, "error": "insufficient data"}
    ic, _ = stats.pearsonr(p, f)
    ric, _ = stats.spearmanr(p, f)
    return {"ic_pearson": ic, "ic_spearman": ric}


def vif_condition_number(df: pd.DataFrame) -> dict:
    """Variance Inflation Factor + condition number for multicollinearity.

    VIF > 10 → serious collinearity.
    Condition number > 30 → multicollinearity present.

    Returns:
        dict with vif_per_column, condition_number
    """
    from numpy.linalg import cond, eigvals

    numeric = df.select_dtypes(include=[np.number]).dropna()
    if numeric.shape[1] < 2:
        return {"vif_per_column": {}, "condition_number": None, "error": "need >= 2 numeric columns"}

    # VIF per column
    vif = {}
    for i, col in enumerate(numeric.columns):
        y = numeric[col]
        X = numeric.drop(columns=[col])
        if X.shape[1] > 0:
            try:
                from statsmodels.api import OLS
                model = OLS(y, X).fit()
                r2 = model.rsquared
                vif[col] = 1.0 / (1.0 - r2) if r2 < 1.0 else np.inf
            except Exception:
                vif[col] = np.nan

    # Condition number
    corr = numeric.corr()
    eigenvals = eigvals(corr)
    cn = np.sqrt(eigenvals.max() / eigenvals.min()) if eigenvals.min() > 0 else np.inf

    return {"vif_per_column": vif, "condition_number": cn}


def sign_consistency(df: pd.DataFrame, cfg: dict) -> dict:
    """Check sign consistency of derived features across time periods.

    Returns:
        dict with sign_flip_rate per column
    """
    return_col = cfg.get("return_col", "return_std")
    feature_cols = cfg.get("feature_cols", [])
    if not feature_cols:
        return {"sign_flip_rate": {}}

    result = {}
    for col in feature_cols:
        if col not in df.columns:
            continue
        signs = np.sign(df[col].dropna())
        if len(signs) < 10:
            result[col] = None
            continue
        flips = (signs.diff().abs() > 0).sum()
        result[col] = flips / (len(signs) - 1)

    return {"sign_flip_rate": result}


# ── helpers ──

def _compute_psi(train: pd.Series, val: pd.Series, bins: int = 10) -> float:
    """Population Stability Index."""
    combined = pd.concat([train, val])
    breaks = np.percentile(combined, np.linspace(0, 100, bins + 1))
    breaks = np.unique(breaks)
    if len(breaks) < 2:
        return 0.0

    t_dist = np.histogram(train, bins=breaks)[0] / len(train)
    v_dist = np.histogram(val, bins=breaks)[0] / len(val)

    # Avoid division by zero
    t_dist = np.clip(t_dist, 1e-10, 1)
    v_dist = np.clip(v_dist, 1e-10, 1)

    psi = np.sum((v_dist - t_dist) * np.log(v_dist / t_dist))
    return psi
