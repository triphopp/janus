"""Unified option pricing engines and bracketed implied-volatility solver.

v1.3: Unified pricing API. Adapter selects model via cfg['pricing_model'].
Black-76 for futures options (underlying = futures, no carry).
BS-Merton for equity/index options with dividend yield.
IV solver uses Brent's method — more robust than Newton near vega≈0.
"""

from dataclasses import dataclass
from statistics import NormalDist
from typing import Any, Mapping, Optional, Tuple

import numpy as np
from core import pricing_models as _models

try:
    from scipy.optimize import brentq
except ImportError:  # pragma: no cover - environment fallback
    brentq = None

_NORMAL = NormalDist()


def _norm_cdf(x: float) -> float:
    return _NORMAL.cdf(float(x))


def _norm_pdf(x: float) -> float:
    value = float(x)
    return float(np.exp(-0.5 * value * value) / np.sqrt(2.0 * np.pi))


@dataclass(frozen=True)
class PriceResult:
    """Scalar price plus optional model-specific solver diagnostics."""

    value: float
    diagnostics: dict[str, Any]


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


def _root_with_diagnostics(
    fn,
    low: float,
    high: float,
    tol: float,
    max_iter: int,
) -> tuple[float, int]:
    """Bracketed scalar root with a deterministic iteration count."""
    if brentq is not None:
        root, result = brentq(
            fn,
            low,
            high,
            xtol=tol,
            maxiter=max_iter,
            full_output=True,
            disp=False,
        )
        if not result.converged:
            raise ValueError("root solver did not converge")
        return float(root), int(result.iterations)

    f_low = fn(low)
    f_high = fn(high)
    if f_low * f_high > 0:
        raise ValueError("root is not bracketed")
    lo, hi = low, high
    for iteration in range(1, max_iter + 1):
        mid = (lo + hi) / 2.0
        f_mid = fn(mid)
        if abs(f_mid) <= tol or (hi - lo) / 2.0 <= tol:
            return mid, iteration
        if f_low * f_mid <= 0:
            hi = mid
            f_high = f_mid
        else:
            lo = mid
            f_low = f_mid
    raise ValueError("root solver did not converge")


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


def _european_price_lower_bound(
    model: str,
    S_or_F: float,
    K: float,
    T: float,
    r: float,
    right: str,
    q: float,
) -> float:
    """Return the zero-volatility no-arbitrage lower price bound.

    Black-76 discounts the futures intrinsic value.  Spot models instead use
    the present values of spot carry and strike; applying the Black-76 bound
    to BS/BSM can reject perfectly valid deep-ITM prices before root finding.
    """
    if model in {"black76", "bachelier", "black76_shifted"}:
        return np.exp(-r * T) * _expired_intrinsic(S_or_F, K, right)

    q_value = 0.0 if model == "bs" else q
    pv_spot = S_or_F * np.exp(-q_value * T)
    pv_strike = K * np.exp(-r * T)
    if right == "C":
        return max(0.0, pv_spot - pv_strike)
    return max(0.0, pv_strike - pv_spot)


def _black76_value(
    F: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    right: str,
) -> float:
    sqrt_t = np.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sigma ** 2 * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    disc = np.exp(-r * T)
    if right == "C":
        return float(disc * (F * _norm_cdf(d1) - K * _norm_cdf(d2)))
    return float(disc * (K * _norm_cdf(-d2) - F * _norm_cdf(-d1)))


def _bsm_value(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    right: str,
    q: float,
) -> float:
    sqrt_t = np.sqrt(T)
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    disc_r = np.exp(-r * T)
    disc_q = np.exp(-q * T)
    if right == "C":
        return float(S * disc_q * _norm_cdf(d1) - K * disc_r * _norm_cdf(d2))
    return float(K * disc_r * _norm_cdf(-d2) - S * disc_q * _norm_cdf(-d1))


def _bachelier_value(
    F: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    right: str,
) -> float:
    """European normal-model futures option value.

    ``sigma`` is an annualized *absolute price* volatility, not a Black
    percentage volatility.  The model identity in exports keeps those units
    from being mixed silently.
    """
    stddev = sigma * np.sqrt(T)
    d = (F - K) / stddev
    disc = np.exp(-r * T)
    if right == "C":
        return float(disc * ((F - K) * _norm_cdf(d) + stddev * _norm_pdf(d)))
    return float(disc * ((K - F) * _norm_cdf(-d) + stddev * _norm_pdf(d)))


def _crr_value(
    *,
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    right: str,
    q: float,
    steps: int,
    exercise_style: str,
) -> PriceResult:
    """Cox-Ross-Rubinstein tree for European or American exercise.

    Futures are represented with ``q=r`` (zero cost of carry), while spot
    products use their configured continuous dividend yield.
    """
    if steps < 2:
        return PriceResult(
            _nan(),
            {"pricing_status": "invalid_tree_steps", "tree_steps": steps},
        )
    if exercise_style not in {"american", "european"}:
        return PriceResult(
            _nan(),
            {
                "pricing_status": "invalid_tree_exercise_style",
                "tree_exercise_style": exercise_style,
            },
        )

    dt = T / steps
    up = float(np.exp(sigma * np.sqrt(dt)))
    down = 1.0 / up
    denominator = up - down
    if denominator <= 0 or not np.isfinite(denominator):
        return PriceResult(_nan(), {"pricing_status": "invalid_tree_dynamics"})
    probability = (np.exp((r - q) * dt) - down) / denominator
    if not np.isfinite(probability) or probability < 0.0 or probability > 1.0:
        return PriceResult(
            _nan(),
            {
                "pricing_status": "tree_probability_out_of_bounds",
                "tree_probability": float(probability),
                "tree_steps": steps,
            },
        )

    node = np.arange(steps + 1, dtype=float)
    terminal = S * (up ** node) * (down ** (steps - node))
    if right == "C":
        values = np.maximum(terminal - K, 0.0)
    else:
        values = np.maximum(K - terminal, 0.0)
    discount = float(np.exp(-r * dt))

    for level in range(steps - 1, -1, -1):
        values = discount * (
            probability * values[1:level + 2]
            + (1.0 - probability) * values[:level + 1]
        )
        if exercise_style == "american":
            level_node = np.arange(level + 1, dtype=float)
            underlying = S * (up ** level_node) * (down ** (level - level_node))
            intrinsic = (
                np.maximum(underlying - K, 0.0)
                if right == "C"
                else np.maximum(K - underlying, 0.0)
            )
            values = np.maximum(values, intrinsic)

    return PriceResult(
        float(values[0]),
        {
            "pricing_status": "ok",
            "tree_steps": steps,
            "tree_exercise_style": exercise_style,
            "tree_probability": float(probability),
        },
    )


def _baw_boundary(
    *,
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    right: str,
    q: float,
    tol: float,
    max_iter: int,
) -> tuple[float | None, float | None, int, str]:
    """Solve the BAW critical exercise boundary for a generalized spot model."""
    variance = sigma * sigma * T
    if variance <= np.finfo(float).eps:
        return None, None, 0, "variance_too_small"
    if r < 0:
        return None, None, 0, "negative_rate_not_supported"

    # At zero rates there is no benefit to receiving the strike early for a
    # put.  A call also has no early-exercise benefit unless q > 0.
    if right == "P" and abs(r) <= 1e-14:
        return None, None, 0, "early_exercise_not_optimal"
    if right == "C" and q <= 0:
        return None, None, 0, "early_exercise_not_optimal"

    disc_r = np.exp(-r * T)
    disc_q = np.exp(-q * T)
    n = 2.0 * np.log(disc_q / disc_r) / variance
    if abs(1.0 - disc_r) <= 1e-12:
        kappa = 2.0 / variance
    else:
        kappa = -2.0 * np.log(disc_r) / (variance * (1.0 - disc_r))
    radical = (n - 1.0) ** 2 + 4.0 * kappa
    if radical <= 0 or not np.isfinite(radical):
        return None, None, 0, "invalid_boundary_exponent"
    if right == "C":
        exponent = (-(n - 1.0) + np.sqrt(radical)) / 2.0
    else:
        exponent = (-(n - 1.0) - np.sqrt(radical)) / 2.0
    if not np.isfinite(exponent) or abs(exponent) <= 1e-14:
        return None, None, 0, "invalid_boundary_exponent"

    sqrt_variance = np.sqrt(variance)

    def equation(boundary: float) -> float:
        d1 = (
            np.log(boundary / K)
            + (r - q + 0.5 * sigma * sigma) * T
        ) / sqrt_variance
        european = _bsm_value(boundary, K, T, r, sigma, right, q)
        if right == "C":
            premium_at_boundary = (
                (1.0 - disc_q * _norm_cdf(d1)) * boundary / exponent
            )
            return boundary - K - european - premium_at_boundary
        premium_at_boundary = -(
            (1.0 - disc_q * _norm_cdf(-d1)) * boundary / exponent
        )
        return K - boundary - european - premium_at_boundary

    root_tol = max(float(tol) * max(1.0, abs(K)), 1e-12)
    try:
        if right == "C":
            low = max(K, np.nextafter(K, np.inf))
            f_low = equation(low)
            high = max(2.0 * K, 2.0 * S, K + 1.0)
            f_high = equation(high)
            expansions = 0
            while f_low * f_high > 0 and expansions < 64:
                high *= 2.0
                f_high = equation(high)
                expansions += 1
            if not np.isfinite(f_low) or not np.isfinite(f_high) or f_low * f_high > 0:
                return None, exponent, expansions, "boundary_not_bracketed"
            boundary, iterations = _root_with_diagnostics(
                equation, low, high, root_tol, max_iter
            )
        else:
            low = max(K * 1e-12, np.finfo(float).tiny)
            high = np.nextafter(K, 0.0)
            f_low = equation(low)
            f_high = equation(high)
            if not np.isfinite(f_low) or not np.isfinite(f_high) or f_low * f_high > 0:
                return None, exponent, 0, "boundary_not_bracketed"
            boundary, iterations = _root_with_diagnostics(
                equation, low, high, root_tol, max_iter
            )
    except (ArithmeticError, OverflowError, ValueError):
        return None, exponent, 0, "boundary_solver_failed"
    return boundary, exponent, iterations, "converged"


def _baw_value(
    *,
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    right: str,
    q: float,
    tol: float,
    max_iter: int,
) -> PriceResult:
    european = _bsm_value(S, K, T, r, sigma, right, q)
    intrinsic = _expired_intrinsic(S, K, right)
    boundary, exponent, iterations, status = _baw_boundary(
        S=S,
        K=K,
        T=T,
        r=r,
        sigma=sigma,
        right=right,
        q=q,
        tol=tol,
        max_iter=max_iter,
    )
    diagnostics: dict[str, Any] = {
        "pricing_status": "ok" if status in {"converged", "early_exercise_not_optimal"} else status,
        "baw_boundary_converged": status in {"converged", "early_exercise_not_optimal"},
        "baw_boundary_iterations": iterations,
        "baw_boundary_solver_status": status,
        "baw_critical_boundary": boundary,
    }
    if T > 1.0:
        diagnostics["model_validity_warning"] = "baw_reference_recommended_for_t_gt_1y"
    if status == "early_exercise_not_optimal":
        return PriceResult(max(european, intrinsic), diagnostics)
    if status != "converged" or boundary is None or exponent is None:
        return PriceResult(_nan(), diagnostics)

    disc_q = np.exp(-q * T)
    sqrt_variance = sigma * np.sqrt(T)
    d1_boundary = (
        np.log(boundary / K) + (r - q + 0.5 * sigma * sigma) * T
    ) / sqrt_variance
    if right == "C":
        if S >= boundary:
            value = intrinsic
        else:
            coefficient = (
                boundary / exponent
                * (1.0 - disc_q * _norm_cdf(d1_boundary))
            )
            value = european + coefficient * (S / boundary) ** exponent
    else:
        if S <= boundary:
            value = intrinsic
        else:
            coefficient = -(
                boundary / exponent
                * (1.0 - disc_q * _norm_cdf(-d1_boundary))
            )
            value = european + coefficient * (S / boundary) ** exponent
    return PriceResult(float(max(value, european, intrinsic)), diagnostics)


def _model_param(
    model_params: Mapping[str, Any] | None,
    name: str,
    default: Any,
) -> Any:
    return default if model_params is None else model_params.get(name, default)


def price_with_diagnostics(
    model: str,
    S_or_F: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    right: str,
    q: float = 0.0,
    *,
    shift: float | None = None,
    model_params: Mapping[str, Any] | None = None,
) -> PriceResult:
    """Price a scalar option and retain solver diagnostics when available."""
    selected = _models.canonical_model_name(model)
    impl = _models.price_runtime_model(selected)
    right_norm = _models.normalize_right(right)

    s_ok, s = _finite(S_or_F)
    k_ok, strike = _finite(K)
    t_ok, t = _finite(T)
    if right_norm is None or not s_ok or not k_ok or not t_ok:
        return PriceResult(_nan(), {"pricing_status": "invalid_input"})
    if t <= 0:
        return PriceResult(
            _expired_intrinsic(s, strike, right_norm),
            {"pricing_status": "expired_intrinsic"},
        )

    configured_shift = shift
    if configured_shift is None:
        configured_shift = _model_param(model_params, "shift", None)
    domain = _models.validate_pricing_domain(
        selected, s, strike, t, r, sigma, right_norm, shift=configured_shift
    )
    if not domain.valid:
        return PriceResult(
            _nan(),
            {"pricing_status": "invalid_domain", "pricing_domain_reason": domain.reason},
        )

    r_value = float(r)
    sigma_value = float(sigma)
    q_ok, q_value = _finite(0.0 if impl == "bs" else q)
    if not q_ok:
        return PriceResult(_nan(), {"pricing_status": "invalid_dividend_yield"})

    if impl == "black76":
        value = _black76_value(s, strike, t, r_value, sigma_value, right_norm)
        return PriceResult(value, {"pricing_status": "ok"})
    if impl in {"bs", "bsm"}:
        value = _bsm_value(s, strike, t, r_value, sigma_value, right_norm, q_value)
        return PriceResult(value, {"pricing_status": "ok"})
    if impl == "bachelier":
        value = _bachelier_value(s, strike, t, r_value, sigma_value, right_norm)
        return PriceResult(
            value,
            {"pricing_status": "ok", "volatility_unit": "absolute_price_per_sqrt_year"},
        )
    if impl == "black76_shifted":
        shift_value = float(configured_shift)
        value = _black76_value(
            s + shift_value,
            strike + shift_value,
            t,
            r_value,
            sigma_value,
            right_norm,
        )
        return PriceResult(value, {"pricing_status": "ok", "pricing_shift": shift_value})
    if impl == "crr_binomial":
        underlying_type = str(
            _model_param(model_params, "tree_underlying_type", "spot")
        ).strip().lower()
        tree_q = r_value if underlying_type in {"future", "futures"} else q_value
        return _crr_value(
            S=s,
            K=strike,
            T=t,
            r=r_value,
            sigma=sigma_value,
            right=right_norm,
            q=tree_q,
            steps=int(_model_param(model_params, "tree_steps", 400)),
            exercise_style=str(
                _model_param(model_params, "tree_exercise_style", "american")
            ).strip().lower(),
        )
    if impl in {"black76_baw", "black76_shifted_baw", "bsm_baw"}:
        baw_s = s
        baw_k = strike
        baw_q = q_value if impl == "bsm_baw" else r_value  # b=r-q=0 for futures
        diagnostics_extra: dict[str, Any] = {}
        if impl == "black76_shifted_baw":
            shift_value = float(configured_shift)
            baw_s += shift_value
            baw_k += shift_value
            diagnostics_extra["pricing_shift"] = shift_value
        result = _baw_value(
            S=baw_s,
            K=baw_k,
            T=t,
            r=r_value,
            sigma=sigma_value,
            right=right_norm,
            q=baw_q,
            tol=float(_model_param(model_params, "baw_boundary_tol", 1e-8)),
            max_iter=int(_model_param(model_params, "baw_boundary_max_iter", 100)),
        )
        return PriceResult(result.value, {**result.diagnostics, **diagnostics_extra})

    raise ValueError(_models.unknown_model_message(model))


def price(
    model: str,
    S_or_F: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    right: str,
    q: float = 0.0,
    *,
    shift: float | None = None,
    model_params: Mapping[str, Any] | None = None,
) -> float:
    """Price a single option.

    Args:
        model: any runtime-enabled model from ``core.pricing_models``
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
    return price_with_diagnostics(
        model,
        S_or_F,
        K,
        T,
        r,
        sigma,
        right,
        q,
        shift=shift,
        model_params=model_params,
    ).value


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
    *,
    shift: float | None = None,
    model_params: Mapping[str, Any] | None = None,
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
    selected = _models.canonical_model_name(model)
    impl = _models.price_runtime_model(selected)
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

    configured_shift = shift
    if configured_shift is None:
        configured_shift = _model_param(model_params, "shift", None)
    domain = _models.validate_pricing_domain(
        selected,
        s,
        strike,
        t,
        r_value,
        low,
        right_norm,
        shift=configured_shift,
    )
    if not domain.valid:
        return np.nan

    q_ok, q_value = _finite(0.0 if impl == "bs" else q)
    if not q_ok:
        return np.nan

    # Check the model-specific zero-volatility bound before root finding.
    tree_exercise = str(
        _model_param(model_params, "tree_exercise_style", "american")
    ).strip().lower()
    if impl in {"black76_baw", "black76_shifted_baw", "bsm_baw"} or (
        impl == "crr_binomial" and tree_exercise == "american"
    ):
        lower_bound = _expired_intrinsic(s, strike, right_norm)
    else:
        lower_bound = _european_price_lower_bound(
            impl, s, strike, t, r_value, right_norm, q_value
        )
    if mkt < lower_bound - tol:
        return np.nan  # arbitrage — log and skip

    def f(sigma):
        return price(
            selected,
            s,
            strike,
            t,
            r_value,
            sigma,
            right_norm,
            q_value,
            shift=configured_shift,
            model_params=model_params,
        ) - mkt

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
    model_params = _models.runtime_model_params(cfg)

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
            shift=model_params.get("shift"),
            model_params=model_params,
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
