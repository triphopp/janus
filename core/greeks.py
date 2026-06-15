"""Closed-form Greeks + net Greeks for spreads.

v1.3: always use closed-form as primary. Numerical bump is sanity check in tests only.
Calendar spread: vega must be split into short_term/long_term buckets because
term-structure shifts are NOT parallel — short-end moves more than long-end (vega_beta < 1).

Futures options MUST use Black-76 Greeks, not BS.
BS delta = N(d₁); Black-76 delta = e^(-rT)·N(d₁) — difference is ~e^(rT) factor.
"""

from dataclasses import dataclass
from math import exp, pi, sqrt
from statistics import NormalDist
from typing import Optional

import numpy as np

_NORMAL = NormalDist()


def _norm_pdf(x: float) -> float:
    return exp(-0.5 * float(x) ** 2) / sqrt(2 * pi)


def _norm_cdf(x: float) -> float:
    return _NORMAL.cdf(float(x))


@dataclass
class Leg:
    """Single option leg in a spread."""
    qty: float          # +1 long, -1 short, +n multi-contract
    right: str          # 'C' or 'P'
    K: float            # strike
    expiry: object      # expiry date
    F_at_t: float       # forward/futures price at time t
    iv_at_t: float      # IV at time t
    T_at_t: float       # time to expiry at time t (years)


def single_leg_greeks(
    model: str,
    S_or_F: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    right: str,
    q: float = 0.0,
) -> dict:
    """Closed-form Greeks for a single option leg.

    Args:
        model: 'black76' | 'bs' | 'bsm'
        S_or_F, K, T, r, sigma, right, q: option parameters

    Returns:
        dict with keys: delta, gamma, vega, theta, rho
    """
    if T <= 0:
        return {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0, "rho": 0.0}

    sqrt_T = np.sqrt(T)
    phi = _norm_pdf  # standard normal PDF
    Phi = _norm_cdf  # standard normal CDF

    if model == "black76":
        F = S_or_F
        d1 = (np.log(F / K) + 0.5 * sigma ** 2 * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T
        disc = np.exp(-r * T)
        phi_d1 = phi(d1)

        if right == "C":
            delta = disc * Phi(d1)
            theta = (
                -F * phi_d1 * sigma / (2 * sqrt_T)
                - r * K * disc * Phi(d2)
                + r * F * disc * Phi(d1)
            )
            rho_val = -T * disc * (F * Phi(d1) - K * Phi(d2))
        else:
            delta = -disc * Phi(-d1)
            theta = (
                -F * phi_d1 * sigma / (2 * sqrt_T)
                + r * K * disc * Phi(-d2)
                - r * F * disc * Phi(-d1)
            )
            rho_val = -T * disc * (K * Phi(-d2) - F * Phi(-d1))

        gamma = disc * phi_d1 / (F * sigma * sqrt_T)
        vega = disc * F * phi_d1 * sqrt_T  # per 1.0 vol unit

    elif model in ("bs", "bsm"):
        S = S_or_F
        d1 = (np.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T
        phi_d1 = phi(d1)
        disc_r = np.exp(-r * T)
        disc_q = np.exp(-q * T)

        if right == "C":
            delta = disc_q * Phi(d1)
            theta = (
                -S * phi_d1 * sigma * disc_q / (2 * sqrt_T)
                - r * K * disc_r * Phi(d2)
                + q * S * disc_q * Phi(d1)
            )
            rho_val = K * T * disc_r * Phi(d2)
        else:
            delta = -disc_q * Phi(-d1)
            theta = (
                -S * phi_d1 * sigma * disc_q / (2 * sqrt_T)
                + r * K * disc_r * Phi(-d2)
                - q * S * disc_q * Phi(-d1)
            )
            rho_val = -K * T * disc_r * Phi(-d2)

        gamma = disc_q * phi_d1 / (S * sigma * sqrt_T)
        vega = disc_q * S * phi_d1 * sqrt_T

    else:
        raise ValueError(f"Unknown pricing model: {model}")

    return {
        "delta": delta,
        "gamma": gamma,
        "vega": vega,
        "theta": theta,
        "rho": rho_val,
    }


def net_greeks(legs: list[Leg], cfg: dict) -> dict:
    """Compute net Greeks for a spread (calendar spread aware).

    Critical for calendar spreads:
    - vega_total = raw sum (often flat/misleading)
    - vega is split into short_term / long_term buckets
    - vega_term_risk = vega_long - vega_beta * vega_short
      This is the root cause of vega bleed — net vega=0 can still bleed
      when short-end IV moves more than long-end.

    Args:
        legs: list of Leg objects (qty, right, K, expiry, F_at_t, iv_at_t, T_at_t)
        cfg: must have keys [pricing_model, vega_bucket_cutoff, vega_beta, rf_rate_col]

    Returns:
        dict with delta, gamma, theta, vega_total, vega_short_term,
        vega_long_term, vega_term_risk
    """
    model = cfg.get("pricing_model", "black76")
    r = cfg.get("rf_rate", 0.05)
    cutoff = cfg.get("vega_bucket_cutoff", 60)  # DTE threshold in days
    vega_beta = cfg.get("vega_beta", 0.7)

    g = {
        "delta": 0.0,
        "gamma": 0.0,
        "theta": 0.0,
        "rho": 0.0,
        "vega_total": 0.0,
        "vega_short_term": 0.0,
        "vega_long_term": 0.0,
    }

    for L in legs:
        # Convert T_at_t from years to DTE for bucketing
        dte = L.T_at_t * 365.0  # approximate; use core/dte.py for precision

        leg_g = single_leg_greeks(
            model=model,
            S_or_F=L.F_at_t,
            K=L.K,
            T=L.T_at_t,
            r=r,
            sigma=L.iv_at_t,
            right=L.right,
        )

        for k in ("delta", "gamma", "theta", "rho"):
            g[k] += L.qty * leg_g[k]

        # Vega bucketing
        bucket = "vega_short_term" if dte < cutoff else "vega_long_term"
        g[bucket] += L.qty * leg_g["vega"]
        g["vega_total"] += L.qty * leg_g["vega"]

    # Vega term risk: exposure to non-parallel term-structure shift
    # If vega_beta < 1, short-end moves more than long-end
    g["vega_term_risk"] = g["vega_long_term"] - vega_beta * g["vega_short_term"]

    return g


def bump_greeks(
    model: str,
    price_fn,
    S_or_F: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    right: str,
    q: float = 0.0,
    eps: float = 1e-4,
) -> dict:
    """Numerical (bump) Greeks — for sanity check / test only.

    NOT for primary calculation. Closed-form is always primary.
    Tolerance vs closed-form should be ≤ 1e-4.

    Returns:
        dict with delta, gamma, vega, theta (same keys as single_leg_greeks)
    """
    p0 = price_fn(model, S_or_F, K, T, r, sigma, right, q)

    # Delta: bump underlying
    bump_s = S_or_F * eps
    p_up = price_fn(model, S_or_F + bump_s, K, T, r, sigma, right, q)
    p_dn = price_fn(model, S_or_F - bump_s, K, T, r, sigma, right, q)
    delta = (p_up - p_dn) / (2 * bump_s)

    # Gamma
    gamma = (p_up - 2 * p0 + p_dn) / (bump_s ** 2)

    # Vega: bump sigma
    bump_v = eps
    p_vup = price_fn(model, S_or_F, K, T, r, sigma + bump_v, right, q)
    p_vdn = price_fn(model, S_or_F, K, T, r, sigma - bump_v, right, q)
    vega = (p_vup - p_vdn) / (2 * bump_v)

    # Theta: bump time
    bump_t = 1.0 / 365.0  # 1 day
    if T > bump_t:
        p_t = price_fn(model, S_or_F, K, T - bump_t, r, sigma, right, q)
        theta = -(p_t - p0) / bump_t  # negative = decay
    else:
        theta = 0.0

    # Rho: bump rate
    bump_r = 1e-4  # 1 bp
    p_rup = price_fn(model, S_or_F, K, T, r + bump_r, sigma, right, q)
    rho = (p_rup - p0) / bump_r

    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta, "rho": rho}
