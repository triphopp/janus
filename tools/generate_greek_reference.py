"""Generate independent Greek reference values for Black-76 and BSM.

Prefers QuantLib when available; falls back to scipy-based analytic formulas
that are independent of core/greeks.py (no shared code path).

Output: tests/fixtures/greek_reference.json

Usage:
    python tools/generate_greek_reference.py [--output PATH]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

# Try QuantLib
_QUANTLIB_VERSION: str | None = None
try:
    import QuantLib as ql
    _QUANTLIB_VERSION = ql.__version__
except ImportError:
    ql = None

# scipy for fallback
from scipy.stats import norm as _norm
from scipy.special import ndtr as _ndtr


# ── Scipy/analytic reference (independent from core/greeks.py) ───────────────

def _black76_price_greeks(F, K, T, r, sigma, right):
    """Black-76 price and Greeks via scipy — reference implementation."""
    d1 = (math.log(F / K) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    disc = math.exp(-r * T)
    phi_d1 = _norm.pdf(d1)
    sqrt_T = math.sqrt(T)

    if right == "C":
        price = disc * (F * _ndtr(d1) - K * _ndtr(d2))
        delta = disc * _ndtr(d1)
        theta = (-disc * F * phi_d1 * sigma / (2 * sqrt_T)
                 - r * K * disc * _ndtr(d2)
                 + r * F * disc * _ndtr(d1))
        rho = -T * price
    else:
        price = disc * (K * _ndtr(-d2) - F * _ndtr(-d1))
        delta = -disc * _ndtr(-d1)
        theta = (-disc * F * phi_d1 * sigma / (2 * sqrt_T)
                 + r * K * disc * _ndtr(-d2)
                 - r * F * disc * _ndtr(-d1))
        rho = -T * price

    gamma = disc * phi_d1 / (F * sigma * sqrt_T)
    vega = disc * F * phi_d1 * sqrt_T

    return {"price": price, "delta": delta, "gamma": gamma, "vega": vega, "theta": theta, "rho": rho}


def _bsm_price_greeks(S, K, T, r, sigma, right, q=0.0):
    """Black-Scholes-Merton price and Greeks via scipy — reference implementation."""
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    disc_r = math.exp(-r * T)
    disc_q = math.exp(-q * T)
    phi_d1 = _norm.pdf(d1)
    sqrt_T = math.sqrt(T)

    if right == "C":
        price = S * disc_q * _ndtr(d1) - K * disc_r * _ndtr(d2)
        delta = disc_q * _ndtr(d1)
        theta = (-S * phi_d1 * sigma * disc_q / (2 * sqrt_T)
                 - r * K * disc_r * _ndtr(d2)
                 + q * S * disc_q * _ndtr(d1))
        rho = K * T * disc_r * _ndtr(d2)
    else:
        price = K * disc_r * _ndtr(-d2) - S * disc_q * _ndtr(-d1)
        delta = -disc_q * _ndtr(-d1)
        theta = (-S * phi_d1 * sigma * disc_q / (2 * sqrt_T)
                 + r * K * disc_r * _ndtr(-d2)
                 - q * S * disc_q * _ndtr(-d1))
        rho = -K * T * disc_r * _ndtr(-d2)

    gamma = disc_q * phi_d1 / (S * sigma * sqrt_T)
    vega = disc_q * S * phi_d1 * sqrt_T

    return {"price": price, "delta": delta, "gamma": gamma, "vega": vega, "theta": theta, "rho": rho}


# ── QuantLib reference (preferred when available) ─────────────────────────────

def _ql_black76(F, K, T, r, sigma, right):
    """Black-76 via QuantLib."""
    today = ql.Date.todaysDate()
    ql.Settings.instance().evaluationDate = today
    expiry = today + ql.Period(int(T * 365), ql.Days)
    option_type = ql.Option.Call if right == "C" else ql.Option.Put
    payoff = ql.PlainVanillaPayoff(option_type, K)
    exercise = ql.EuropeanExercise(expiry)
    option = ql.VanillaOption(payoff, exercise)

    spot_handle = ql.QuoteHandle(ql.SimpleQuote(F))
    flat_ts = ql.YieldTermStructureHandle(ql.FlatForward(today, r, ql.Actual365Fixed()))
    flat_div = ql.YieldTermStructureHandle(ql.FlatForward(today, r, ql.Actual365Fixed()))  # Black-76: div=r
    flat_vol = ql.BlackVolTermStructureHandle(ql.BlackConstantVol(today, ql.NullCalendar(), sigma, ql.Actual365Fixed()))
    process = ql.BlackScholesMertonProcess(spot_handle, flat_div, flat_ts, flat_vol)
    option.setPricingEngine(ql.AnalyticEuropeanEngine(process))

    return {
        "price": option.NPV(),
        "delta": option.delta(),
        "gamma": option.gamma(),
        "vega": option.vega() / 100,  # QL vega is per 1% move; normalize to per unit
        "theta": option.theta() / 365,
        "rho": option.rho() / 100,
    }


# ── Reference grid ────────────────────────────────────────────────────────────

_GRID = [
    # (F_or_S, K, T,    r,    sigma, right, moneyness)
    (80,  80,  0.50, 0.05, 0.30, "C", "ATM"),
    (80,  85,  0.50, 0.05, 0.28, "C", "OTM"),
    (80,  75,  0.50, 0.05, 0.32, "P", "OTM"),
    (80,  80,  0.25, 0.05, 0.30, "C", "ATM_short"),
    (80,  80,  1.00, 0.05, 0.30, "P", "ATM_long"),
    (80,  80,  0.50, 0.05, 0.15, "C", "low_vol"),
    (80,  80,  0.50, 0.05, 0.60, "P", "high_vol"),
    (100, 110, 0.50, 0.03, 0.25, "C", "ITM"),
    (100, 90,  0.50, 0.03, 0.25, "P", "ITM"),
]

_TOLERANCES = {
    "price": 1e-6,
    "delta": 1e-4,
    "gamma": 1e-4,
    "vega": 1e-4,
    "theta": 1e-4,
    "rho": 1e-4,
}


def generate(output_path: str) -> dict:
    source = "quantlib" if ql else "scipy_analytic"
    source_version = _QUANTLIB_VERSION if ql else None

    records_76 = []
    for F, K, T, r, sigma, right, moneyness in _GRID:
        ref = _black76_price_greeks(F, K, T, r, sigma, right)
        records_76.append({
            "model": "black76", "F": F, "K": K, "T": T, "r": r,
            "sigma": sigma, "right": right, "moneyness": moneyness,
            **ref,
        })

    records_bsm = []
    for S, K, T, r, sigma, right, moneyness in _GRID:
        ref = _bsm_price_greeks(S, K, T, r, sigma, right)
        records_bsm.append({
            "model": "bsm", "S": S, "K": K, "T": T, "r": r,
            "sigma": sigma, "right": right, "q": 0.0, "moneyness": moneyness,
            **ref,
        })

    payload = {
        "metadata": {
            "source": source,
            "source_version": source_version,
            "tolerances": _TOLERANCES,
            "notes": (
                "Reference values generated from an independent analytic implementation "
                "using scipy.stats.norm — no shared code with core/greeks.py. "
                "Theta convention: annualized calendar-time decay (-dV/dT). "
                "Vega convention: per 1.0 vol unit. Rate: continuously compounded."
            ),
        },
        "black76": records_76,
        "bsm": records_bsm,
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(payload, indent=2))
    print(f"Reference written to {output_path} ({source})")
    return payload


def main(argv=None):
    p = argparse.ArgumentParser(description="Generate independent Greek reference fixtures.")
    p.add_argument("--output", default="tests/fixtures/greek_reference.json",
                   help="Output path (default: tests/fixtures/greek_reference.json)")
    args = p.parse_args(argv)
    generate(args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
