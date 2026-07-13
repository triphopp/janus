"""Option pricing — Black-76, BS-Merton, IV solver (Brent root-find).

v1.3: Unified pricing API. Adapter selects model via cfg['pricing_model'].
Black-76 for futures options (underlying = futures, no carry).
BS-Merton for equity/index options with dividend yield.
IV solver uses Brent's method — more robust than Newton near vega≈0.
"""

from statistics import NormalDist
from typing import Optional, Tuple

import numpy as np
from core import pricing_models as _models

try:
    from scipy.optimize import brentq
except ImportError:  # pragma: no cover - environment fallback
    brentq = None

_NORMAL = NormalDist()


def _norm_cdf(x: float) -> float:
    return _NORMAL.cdf(float(x))


def _bisect_root(fn, low: float, high: float, tol: float, max_iter: int = 100) -> float:
    """Small no-dependency fallback for bracketed IV roots."""
    f_low = fn(low)
    f_high = fn(high)
    if f_low * f_high > 0:
        raise ValueError("root is not bracketed")

    lo, hi = low, high
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        f_mid = fn(mid)
        if abs(f_mid) <= tol or (hi - lo) / 2.0 <= tol:
            return mid
        if f_low * f_mid <= 0:
            hi = mid
            f_high = f_mid
        else:
            lo = mid
            f_low = f_mid
    return (lo + hi) / 2.0


def _root(fn, low: float, high: float, tol: float) -> float:
    if brentq is not None:
        return brentq(fn, low, high, xtol=tol)
    return _bisect_root(fn, low, high, tol)


def _nan() -> float:
    return float("nan")


def _finite(value) -> tuple[bool, float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return False, float("nan")
    return bool(np.isfinite(out)), out


def _expired_intrinsic(S_or_F: float, K: float, right: str) -> float:
    if right == "C":
        return max(0.0, S_or_F - K)
    return max(0.0, K - S_or_F)


def price(
    model: str,
    S_or_F: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    right: str,
    q: float = 0.0,
) -> float:
    """Price a single option.

    Args:
        model: 'black76' | 'bs' | 'bsm'
        S_or_F: spot price (bs/bsm) or futures price (black76)
        K: strike
        T: time to expiry in years
        r: risk-free rate (continuous)
        sigma: implied volatility (annualized)
        right: 'C' (call) or 'P' (put)
        q: dividend yield (bsm only)

    Returns:
        Option price (premium)
    """
    impl = _models.price_runtime_model(model)
    right_norm = _models.normalize_right(right)

    s_ok, s = _finite(S_or_F)
    k_ok, strike = _finite(K)
    t_ok, t = _finite(T)
    if right_norm is None or not s_ok or not k_ok or not t_ok:
        return _nan()

    if t <= 0:
        # Expired — intrinsic only
        return _expired_intrinsic(s, strike, right_norm)

    domain = _models.validate_pricing_domain(impl, s, strike, t, r, sigma, right_norm)
    if not domain.valid:
        return _nan()

    r = float(r)
    sigma = float(sigma)

    if impl == "black76":
        F = s
        d1 = (np.log(F / strike) + 0.5 * sigma ** 2 * t) / (sigma * np.sqrt(t))
        d2 = d1 - sigma * np.sqrt(t)
        disc = np.exp(-r * t)
        if right_norm == "C":
            return disc * (F * _norm_cdf(d1) - strike * _norm_cdf(d2))
        else:
            return disc * (strike * _norm_cdf(-d2) - F * _norm_cdf(-d1))

    elif impl in ("bs", "bsm"):
        q_ok, q_value = _finite(q)
        if not q_ok:
            return _nan()
        S = s
        d1 = (np.log(S / strike) + (r - q_value + 0.5 * sigma ** 2) * t) / (sigma * np.sqrt(t))
        d2 = d1 - sigma * np.sqrt(t)
        if right_norm == "C":
            return S * np.exp(-q_value * t) * _norm_cdf(d1) - strike * np.exp(-r * t) * _norm_cdf(d2)
        else:
            return strike * np.exp(-r * t) * _norm_cdf(-d2) - S * np.exp(-q_value * t) * _norm_cdf(-d1)

    raise ValueError(_models.unknown_model_message(model))


def solve_iv(
    model: str,
    mkt_price: float,
    S_or_F: float,
    K: float,
    T: float,
    r: float,
    right: str,
    q: float = 0.0,
    bounds: Tuple[float, float] = (1e-4, 5.0),
    tol: float = 1e-6,
) -> float:
    """Solve for implied volatility via Brent root-find.

    More robust than Newton when vega ≈ 0 (deep ITM/OTM, T→0).
    Returns NaN if: arbitrage violation (intrinsic > mkt_price),
    or root outside bounds.

    Args:
        model: pricing model to use
        mkt_price: observed market price
        S_or_F, K, T, r, right, q: option parameters
        bounds: (low, high) sigma search range (annualized)
        tol: convergence tolerance

    Returns:
        Implied volatility (annualized), or NaN if unsolvable
    """
    impl = _models.price_runtime_model(model)
    mkt_ok, mkt = _finite(mkt_price)
    s_ok, s = _finite(S_or_F)
    k_ok, strike = _finite(K)
    t_ok, t = _finite(T)
    r_ok, r_value = _finite(r)
    right_norm = _models.normalize_right(right)
    if (
        not mkt_ok or mkt <= 0
        or not s_ok or not k_ok or not t_ok or t <= 0
        or not r_ok or right_norm is None
    ):
        return np.nan

    low, high = bounds
    low_ok, low = _finite(low)
    high_ok, high = _finite(high)
    if not low_ok or not high_ok or low <= 0 or high <= low:
        return np.nan

    domain = _models.validate_pricing_domain(
        impl, s, strike, t, r_value, low, right_norm
    )
    if not domain.valid:
        return np.nan

    # Check arbitrage violation
    intrinsic = _expired_intrinsic(s, strike, right_norm)

    if mkt < intrinsic * np.exp(-r_value * t) - tol:
        return np.nan  # arbitrage — log and skip

    def f(sigma):
        return price(impl, s, strike, t, r_value, sigma, right_norm, q) - mkt

    # Check that root is bracketed
    try:
        f_low = f(low)
        f_high = f(high)
    except (ValueError, ZeroDivisionError):
        return np.nan

    if not np.isfinite(f_low) or not np.isfinite(f_high):
        return np.nan

    # If both have same sign, root not bracketed
    if f_low * f_high > 0:
        # Try expanding bounds
        if f_low > 0 and f_high > 0:
            # Price too high even at sigma=5.0 — flag
            return np.nan
        # Try lower bound
        try:
            f_super_low = f(1e-6)
            if np.isfinite(f_super_low) and f_low * f_super_low < 0:
                return _root(f, 1e-6, low, tol)
        except (ValueError, ZeroDivisionError):
            pass
        return np.nan

    try:
        return _root(f, low, high, tol)
    except (ValueError, ZeroDivisionError):
        return np.nan


def validate_provided_iv(
    df: "pd.DataFrame",
    cfg: dict,
) -> "pd.DataFrame":
    """Cross-check provided IV vs self-solved IV.

    If difference exceeds threshold → log + flag.
    Exchange uses their own rate/dividend assumptions that may differ from ours.

    Args:
        df: must have columns [price, F, strike, T, r, right, iv_provided]
        cfg: must have keys [pricing_model, iv_validate_threshold]

    Returns:
        DataFrame with added columns: iv_solved, iv_diff, iv_flag
    """
    import pandas as pd

    threshold = cfg.get("iv_validate_threshold", 0.005)  # 0.5 vol-point default
    model = cfg.get("pricing_model", "black76")
    q = cfg.get("div_yield", 0.0)
    bounds = tuple(cfg.get("iv_solver_bounds", (1e-4, 5.0)))

    results = []
    for _, row in df.iterrows():
        mkt_price = row.get("option_price", row.get("price", np.nan))
        underlying = row.get(
            "underlying_price",
            row.get("S", row.get("F", row.get("price_std", np.nan))),
        )
        required = [mkt_price, underlying, row.get("strike", np.nan), row.get("T", np.nan)]
        if any(pd.isna(value) for value in required) or row.get("T", 0) <= 0:
            results.append(np.nan)
            continue

        solved = solve_iv(
            model=model,
            mkt_price=mkt_price,
            S_or_F=underlying,
            K=row["strike"],
            T=row.get("T", np.nan),
            r=row.get("r", np.nan),
            right=row["right"],
            q=q,
            bounds=bounds,
        )
        results.append(solved)

    df = df.copy()
    df["iv_solved"] = results
    df["iv_diff"] = (df["iv_solved"] - df.get("iv_provided", np.nan)).abs()

    # Price-inversion is only trustworthy where the settlement price carries
    # recoverable time value (issue 025). Deep ITM/OTM rows settle at ~intrinsic
    # (time value below one tick), so inverting them yields a meaningless IV; we must
    # NOT flag the (authoritative) exchange IV there. Only near-the-money rows with
    # enough time value are eligible for the provider/model mismatch flag.
    import pandas as pd  # local alias already imported above

    min_ticks = float(cfg.get("iv_validate_min_time_value_ticks", 2.0))
    price_tick = float(
        cfg.get("price_tick", (cfg.get("export") or {}).get("price_tick", 0.01))
    )
    min_time_value = min_ticks * price_tick

    underlying = pd.to_numeric(
        df.get("underlying_price", df.get("F", df.get("price_std"))), errors="coerce"
    )
    strike = pd.to_numeric(df.get("strike"), errors="coerce")
    price = pd.to_numeric(df.get("option_price", df.get("price")), errors="coerce")
    right = df.get("right", pd.Series(index=df.index, dtype="object")).astype("string").str.upper()
    intrinsic = np.where(
        right.eq("C"), np.clip(underlying - strike, 0, None),
        np.clip(strike - underlying, 0, None),
    )
    time_value = price - intrinsic
    df["iv_invertible"] = (time_value >= min_time_value) & df["iv_solved"].notna()

    df["iv_flag"] = df["iv_invertible"] & (df["iv_diff"] > threshold)
    return df
