"""Option pricing — Black-76, BS-Merton, IV solver (Brent root-find).

v1.3: Unified pricing API. Adapter selects model via cfg['pricing_model'].
Black-76 for futures options (underlying = futures, no carry).
BS-Merton for equity/index options with dividend yield.
IV solver uses Brent's method — more robust than Newton near vega≈0.
"""

from statistics import NormalDist
from typing import Optional, Tuple

import numpy as np

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
    if T <= 0:
        # Expired — intrinsic only
        if right == "C":
            return max(0.0, S_or_F - K)
        else:
            return max(0.0, K - S_or_F)

    if model == "black76":
        F = S_or_F
        d1 = (np.log(F / K) + 0.5 * sigma ** 2 * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)
        disc = np.exp(-r * T)
        if right == "C":
            return disc * (F * _norm_cdf(d1) - K * _norm_cdf(d2))
        else:
            return disc * (K * _norm_cdf(-d2) - F * _norm_cdf(-d1))

    elif model in ("bs", "bsm"):
        S = S_or_F
        d1 = (np.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)
        if right == "C":
            return S * np.exp(-q * T) * _norm_cdf(d1) - K * np.exp(-r * T) * _norm_cdf(d2)
        else:
            return K * np.exp(-r * T) * _norm_cdf(-d2) - S * np.exp(-q * T) * _norm_cdf(-d1)

    raise ValueError(f"Unknown pricing model: {model}")


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
    if T <= 0:
        return np.nan

    # Check arbitrage violation
    if right == "C":
        intrinsic = max(0.0, S_or_F - K)
    else:
        intrinsic = max(0.0, K - S_or_F)

    if mkt_price < intrinsic * np.exp(-r * T) - tol:
        return np.nan  # arbitrage — log and skip

    def f(sigma):
        return price(model, S_or_F, K, T, r, sigma, right, q) - mkt_price

    # Check that root is bracketed
    try:
        f_low = f(bounds[0])
        f_high = f(bounds[1])
    except (ValueError, ZeroDivisionError):
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
            if f_low * f_super_low < 0:
                return _root(f, 1e-6, bounds[0], tol)
        except (ValueError, ZeroDivisionError):
            pass
        return np.nan

    try:
        return _root(f, bounds[0], bounds[1], tol)
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
            r=row.get("r", 0.05),
            right=row["right"],
            q=q,
            bounds=bounds,
        )
        results.append(solved)

    df = df.copy()
    df["iv_solved"] = results
    df["iv_diff"] = (df["iv_solved"] - df.get("iv_provided", np.nan)).abs()
    df["iv_flag"] = df["iv_diff"] > threshold
    return df
