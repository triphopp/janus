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
from scipy.special import ndtr as _ndtr

from core import pricing_models as _models
from core import rates as _rates

_NORMAL = NormalDist()


def _norm_pdf(x: float) -> float:
    return exp(-0.5 * float(x) ** 2) / sqrt(2 * pi)


def _norm_cdf(x: float) -> float:
    return _NORMAL.cdf(float(x))


def _norm_pdf_vec(x: np.ndarray) -> np.ndarray:
    return np.exp(-0.5 * x ** 2) / np.sqrt(2 * np.pi)


def _nan_greeks() -> dict:
    return {"delta": np.nan, "gamma": np.nan, "vega": np.nan, "theta": np.nan, "rho": np.nan}


def _zero_greeks() -> dict:
    return {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0, "rho": 0.0}


def _finite_float(value) -> tuple[bool, float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return False, float("nan")
    return bool(np.isfinite(out)), out


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
    impl = _models.greek_runtime_model(model)
    right_norm = _models.normalize_right(right)
    t_ok, t = _finite_float(T)
    s_ok, s = _finite_float(S_or_F)
    k_ok, strike = _finite_float(K)
    if right_norm is None or not t_ok or not s_ok or not k_ok:
        return _nan_greeks()
    if t <= 0:
        return _zero_greeks()

    domain = _models.validate_pricing_domain(impl, s, strike, t, r, sigma, right_norm)
    if not domain.valid:
        return _nan_greeks()

    r = float(r)
    sigma = float(sigma)

    sqrt_T = np.sqrt(t)
    phi = _norm_pdf  # standard normal PDF
    Phi = _norm_cdf  # standard normal CDF

    if impl == "black76":
        F = s
        d1 = (np.log(F / strike) + 0.5 * sigma ** 2 * t) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T
        disc = np.exp(-r * t)
        phi_d1 = phi(d1)

        if right_norm == "C":
            delta = disc * Phi(d1)
            theta = (
                -disc * F * phi_d1 * sigma / (2 * sqrt_T)
                - r * strike * disc * Phi(d2)
                + r * F * disc * Phi(d1)
            )
            rho_val = -t * disc * (F * Phi(d1) - strike * Phi(d2))
        else:
            delta = -disc * Phi(-d1)
            theta = (
                -disc * F * phi_d1 * sigma / (2 * sqrt_T)
                + r * strike * disc * Phi(-d2)
                - r * F * disc * Phi(-d1)
            )
            rho_val = -t * disc * (strike * Phi(-d2) - F * Phi(-d1))

        gamma = disc * phi_d1 / (F * sigma * sqrt_T)
        vega = disc * F * phi_d1 * sqrt_T  # per 1.0 vol unit

    elif impl in ("bs", "bsm"):
        q_ok, q_value = _finite_float(q)
        if not q_ok:
            return _nan_greeks()
        S = s
        d1 = (np.log(S / strike) + (r - q_value + 0.5 * sigma ** 2) * t) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T
        phi_d1 = phi(d1)
        disc_r = np.exp(-r * t)
        disc_q = np.exp(-q_value * t)

        if right_norm == "C":
            delta = disc_q * Phi(d1)
            theta = (
                -S * phi_d1 * sigma * disc_q / (2 * sqrt_T)
                - r * strike * disc_r * Phi(d2)
                + q_value * S * disc_q * Phi(d1)
            )
            rho_val = strike * t * disc_r * Phi(d2)
        else:
            delta = -disc_q * Phi(-d1)
            theta = (
                -S * phi_d1 * sigma * disc_q / (2 * sqrt_T)
                + r * strike * disc_r * Phi(-d2)
                - q_value * S * disc_q * Phi(-d1)
            )
            rho_val = -strike * t * disc_r * Phi(-d2)

        gamma = disc_q * phi_d1 / (S * sigma * sqrt_T)
        vega = disc_q * S * phi_d1 * sqrt_T

    else:
        raise ValueError(_models.unknown_model_message(model))

    return {
        "delta": delta,
        "gamma": gamma,
        "vega": vega,
        "theta": theta,
        "rho": rho_val,
    }


def _cuda_device_count() -> int:
    try:
        import cupy as cp
        return cp.cuda.runtime.getDeviceCount()
    except Exception:
        return 0


def _cupy_available() -> bool:
    return _cuda_device_count() > 0


# auto-backend CUDA threshold. Breakeven measured at ~100k rows on RTX 3080
# (numpy 13.0ms vs cuda 6.2ms) — see docs/benchmarks/greek_backend_benchmark.md.
# Override per-call via cuda_min_rows / config greeks_cuda_min_rows for other hardware.
_CUDA_AUTO_MIN_ROWS = 100_000


def _resolve_greeks_backend(backend: str, n_rows: int, cuda_min_rows: int | None = None) -> str:
    """Return the concrete backend name for this request."""
    if backend in ("loop", "numpy"):
        return backend
    if backend == "auto":
        if _cupy_available():
            threshold = cuda_min_rows if cuda_min_rows is not None else _CUDA_AUTO_MIN_ROWS
            if n_rows >= threshold:
                return "cuda"
        return "numpy"
    if backend == "cuda":
        if not _cupy_available():
            raise RuntimeError(
                "CUDA backend requested but CuPy is not available or no GPU device found. "
                "Install CuPy matching your CUDA runtime (see requirements-cuda.txt) "
                "or use backend='numpy'."
            )
        return "cuda"
    raise ValueError(f"Unknown greeks backend: {backend!r}. Choose 'loop', 'numpy', 'auto', or 'cuda'.")


def _batch_greeks_numpy(
    model: str,
    S_or_F: np.ndarray,
    K: np.ndarray,
    T: np.ndarray,
    r: np.ndarray,
    sigma: np.ndarray,
    right: np.ndarray,
    q: float,
    dtype: str,
) -> dict[str, np.ndarray]:
    n = len(S_or_F)
    out = {g: np.full(n, np.nan, dtype=dtype) for g in ("delta", "gamma", "vega", "theta", "rho")}

    right_str = np.asarray(right, dtype=object)
    call_mask = right_str == "C"
    put_mask = right_str == "P"
    valid = (
        (T > 0) & np.isfinite(T)
        & (S_or_F > 0) & np.isfinite(S_or_F)
        & (K > 0) & np.isfinite(K)
        & (sigma > 0) & np.isfinite(sigma)
        & np.isfinite(r)
        & (call_mask | put_mask)
    )

    if not valid.any():
        return out

    Sv = S_or_F[valid]
    Kv = K[valid]
    Tv = T[valid]
    rv = r[valid]
    sv = sigma[valid]
    cv = call_mask[valid]

    sqrt_T = np.sqrt(Tv)

    if model == "black76":
        d1 = (np.log(Sv / Kv) + 0.5 * sv ** 2 * Tv) / (sv * sqrt_T)
        d2 = d1 - sv * sqrt_T
        disc = np.exp(-rv * Tv)
        phi_d1 = _norm_pdf_vec(d1)
        Phi_d1 = _ndtr(d1)
        Phi_d2 = _ndtr(d2)
        Phi_nd1 = _ndtr(-d1)
        Phi_nd2 = _ndtr(-d2)

        delta = np.where(cv, disc * Phi_d1, -disc * Phi_nd1)
        gamma = disc * phi_d1 / (Sv * sv * sqrt_T)
        vega = disc * Sv * phi_d1 * sqrt_T
        theta_c = -disc * Sv * phi_d1 * sv / (2 * sqrt_T) - rv * Kv * disc * Phi_d2 + rv * Sv * disc * Phi_d1
        theta_p = -disc * Sv * phi_d1 * sv / (2 * sqrt_T) + rv * Kv * disc * Phi_nd2 - rv * Sv * disc * Phi_nd1
        theta = np.where(cv, theta_c, theta_p)
        rho_c = -Tv * disc * (Sv * Phi_d1 - Kv * Phi_d2)
        rho_p = -Tv * disc * (Kv * Phi_nd2 - Sv * Phi_nd1)
        rho = np.where(cv, rho_c, rho_p)

    elif model in ("bs", "bsm"):
        d1 = (np.log(Sv / Kv) + (rv - q + 0.5 * sv ** 2) * Tv) / (sv * sqrt_T)
        d2 = d1 - sv * sqrt_T
        disc_r = np.exp(-rv * Tv)
        disc_q = np.exp(-q * Tv)
        phi_d1 = _norm_pdf_vec(d1)
        Phi_d1 = _ndtr(d1)
        Phi_d2 = _ndtr(d2)
        Phi_nd1 = _ndtr(-d1)
        Phi_nd2 = _ndtr(-d2)

        delta = np.where(cv, disc_q * Phi_d1, -disc_q * Phi_nd1)
        gamma = disc_q * phi_d1 / (Sv * sv * sqrt_T)
        vega = disc_q * Sv * phi_d1 * sqrt_T
        theta_c = -Sv * phi_d1 * sv * disc_q / (2 * sqrt_T) - rv * Kv * disc_r * Phi_d2 + q * Sv * disc_q * Phi_d1
        theta_p = -Sv * phi_d1 * sv * disc_q / (2 * sqrt_T) + rv * Kv * disc_r * Phi_nd2 - q * Sv * disc_q * Phi_nd1
        theta = np.where(cv, theta_c, theta_p)
        rho_c = Kv * Tv * disc_r * Phi_d2
        rho_p = -Kv * Tv * disc_r * Phi_nd2
        rho = np.where(cv, rho_c, rho_p)

    else:
        raise ValueError(_models.unknown_model_message(model))

    out["delta"][valid] = delta
    out["gamma"][valid] = gamma
    out["vega"][valid] = vega
    out["theta"][valid] = theta
    out["rho"][valid] = rho

    return out


def _batch_greeks_loop(
    model: str,
    S_or_F: np.ndarray,
    K: np.ndarray,
    T: np.ndarray,
    r: np.ndarray,
    sigma: np.ndarray,
    right: np.ndarray,
    q: float,
    dtype: str,
) -> dict[str, np.ndarray]:
    n = len(S_or_F)
    out = {g: np.full(n, np.nan, dtype=dtype) for g in ("delta", "gamma", "vega", "theta", "rho")}
    right_arr = np.asarray(right, dtype=object)

    for i in range(n):
        ri = str(right_arr[i])
        Ti = float(T[i])
        Si = float(S_or_F[i])
        Ki = float(K[i])
        si = float(sigma[i])
        ri_r = float(r[i])

        if (
            not np.isfinite(Ti) or Ti <= 0
            or not np.isfinite(Si) or Si <= 0
            or not np.isfinite(Ki) or Ki <= 0
            or not np.isfinite(si) or si <= 0
            or not np.isfinite(ri_r)
            or ri not in ("C", "P")
        ):
            continue

        g = single_leg_greeks(model=model, S_or_F=Si, K=Ki, T=Ti, r=ri_r, sigma=si, right=ri, q=q)
        for key in ("delta", "gamma", "vega", "theta", "rho"):
            out[key][i] = g[key]

    return out


def _ndtr_gpu(x, cp):
    """Normal CDF on GPU: prefer cupyx.scipy.special.ndtr, fall back to erfc."""
    try:
        from cupyx.scipy.special import ndtr
        return ndtr(x)
    except (ImportError, AttributeError):
        return 0.5 * cp.erfc(-x / cp.sqrt(cp.array(2.0, dtype=x.dtype)))


def _batch_greeks_cuda(
    model: str,
    S_or_F: np.ndarray,
    K: np.ndarray,
    T: np.ndarray,
    r: np.ndarray,
    sigma: np.ndarray,
    right: np.ndarray,
    q: float,
    dtype: str,
) -> dict[str, np.ndarray]:
    import cupy as cp

    n = len(S_or_F)
    out = {g: np.full(n, np.nan, dtype=dtype) for g in ("delta", "gamma", "vega", "theta", "rho")}

    # Validity mask on CPU (avoids GPU string ops)
    right_str = np.asarray(right, dtype=object)
    call_mask_cpu = right_str == "C"
    put_mask_cpu = right_str == "P"
    valid_cpu = (
        (T > 0) & np.isfinite(T)
        & (S_or_F > 0) & np.isfinite(S_or_F)
        & (K > 0) & np.isfinite(K)
        & (sigma > 0) & np.isfinite(sigma)
        & np.isfinite(r)
        & (call_mask_cpu | put_mask_cpu)
    )

    if not valid_cpu.any():
        return out

    # Transfer only valid rows to GPU
    Sv = cp.asarray(S_or_F[valid_cpu], dtype=dtype)
    Kv = cp.asarray(K[valid_cpu], dtype=dtype)
    Tv = cp.asarray(T[valid_cpu], dtype=dtype)
    rv = cp.asarray(r[valid_cpu], dtype=dtype)
    sv = cp.asarray(sigma[valid_cpu], dtype=dtype)
    cv = cp.asarray(call_mask_cpu[valid_cpu])

    sqrt_T = cp.sqrt(Tv)

    if model == "black76":
        d1 = (cp.log(Sv / Kv) + 0.5 * sv ** 2 * Tv) / (sv * sqrt_T)
        d2 = d1 - sv * sqrt_T
        disc = cp.exp(-rv * Tv)
        phi_d1 = cp.exp(-0.5 * d1 ** 2) / cp.sqrt(cp.array(2.0 * np.pi, dtype=dtype))
        Phi_d1 = _ndtr_gpu(d1, cp)
        Phi_d2 = _ndtr_gpu(d2, cp)
        Phi_nd1 = _ndtr_gpu(-d1, cp)
        Phi_nd2 = _ndtr_gpu(-d2, cp)

        delta = cp.where(cv, disc * Phi_d1, -disc * Phi_nd1)
        gamma = disc * phi_d1 / (Sv * sv * sqrt_T)
        vega = disc * Sv * phi_d1 * sqrt_T
        theta_c = -disc * Sv * phi_d1 * sv / (2 * sqrt_T) - rv * Kv * disc * Phi_d2 + rv * Sv * disc * Phi_d1
        theta_p = -disc * Sv * phi_d1 * sv / (2 * sqrt_T) + rv * Kv * disc * Phi_nd2 - rv * Sv * disc * Phi_nd1
        theta = cp.where(cv, theta_c, theta_p)
        rho_c = -Tv * disc * (Sv * Phi_d1 - Kv * Phi_d2)
        rho_p = -Tv * disc * (Kv * Phi_nd2 - Sv * Phi_nd1)
        rho = cp.where(cv, rho_c, rho_p)

    elif model in ("bs", "bsm"):
        d1 = (cp.log(Sv / Kv) + (rv - q + 0.5 * sv ** 2) * Tv) / (sv * sqrt_T)
        d2 = d1 - sv * sqrt_T
        disc_r = cp.exp(-rv * Tv)
        disc_q = cp.exp(-q * Tv)
        phi_d1 = cp.exp(-0.5 * d1 ** 2) / cp.sqrt(cp.array(2.0 * np.pi, dtype=dtype))
        Phi_d1 = _ndtr_gpu(d1, cp)
        Phi_d2 = _ndtr_gpu(d2, cp)
        Phi_nd1 = _ndtr_gpu(-d1, cp)
        Phi_nd2 = _ndtr_gpu(-d2, cp)

        delta = cp.where(cv, disc_q * Phi_d1, -disc_q * Phi_nd1)
        gamma = disc_q * phi_d1 / (Sv * sv * sqrt_T)
        vega = disc_q * Sv * phi_d1 * sqrt_T
        theta_c = -Sv * phi_d1 * sv * disc_q / (2 * sqrt_T) - rv * Kv * disc_r * Phi_d2 + q * Sv * disc_q * Phi_d1
        theta_p = -Sv * phi_d1 * sv * disc_q / (2 * sqrt_T) + rv * Kv * disc_r * Phi_nd2 - q * Sv * disc_q * Phi_nd1
        theta = cp.where(cv, theta_c, theta_p)
        rho_c = Kv * Tv * disc_r * Phi_d2
        rho_p = -Kv * Tv * disc_r * Phi_nd2
        rho = cp.where(cv, rho_c, rho_p)

    else:
        raise ValueError(_models.unknown_model_message(model))

    # Transfer results back to CPU
    out["delta"][valid_cpu] = cp.asnumpy(delta)
    out["gamma"][valid_cpu] = cp.asnumpy(gamma)
    out["vega"][valid_cpu] = cp.asnumpy(vega)
    out["theta"][valid_cpu] = cp.asnumpy(theta)
    out["rho"][valid_cpu] = cp.asnumpy(rho)

    # Release GPU memory
    del Sv, Kv, Tv, rv, sv, cv, sqrt_T, d1, d2, phi_d1, delta, gamma, vega, theta, rho

    return out


def _dispatch_greeks_backend(
    resolved: str,
    model: str,
    S_or_F: np.ndarray,
    K: np.ndarray,
    T: np.ndarray,
    r: np.ndarray,
    sigma: np.ndarray,
    right: np.ndarray,
    q: float,
    dtype: str,
) -> dict[str, np.ndarray]:
    if resolved == "numpy":
        return _batch_greeks_numpy(model, S_or_F, K, T, r, sigma, right, q, dtype)
    if resolved == "loop":
        return _batch_greeks_loop(model, S_or_F, K, T, r, sigma, right, q, dtype)
    if resolved == "cuda":
        return _batch_greeks_cuda(model, S_or_F, K, T, r, sigma, right, q, dtype)
    raise ValueError(f"Unknown resolved backend: {resolved!r}")


_CUDA_DEFAULT_BATCH_SIZE = 250_000


def batch_greeks(
    model: str,
    S_or_F,
    K,
    T,
    r,
    sigma,
    right,
    q: float = 0.0,
    backend: str = "numpy",
    batch_size: int | None = None,
    dtype: str = "float64",
    cuda_min_rows: int | None = None,
) -> dict[str, np.ndarray]:
    """Vectorized Greeks for a batch of option rows.

    Invalid rows (T<=0, missing values, unknown right) return NaN in all outputs.
    Backends: 'numpy' (default), 'loop' (scalar fallback), 'auto', 'cuda' (requires CuPy).
    CUDA chunking: if backend resolves to 'cuda' and batch_size is None,
    defaults to 250_000 rows per chunk to avoid OOM.
    """
    model = _models.greek_runtime_model(model)
    S_arr = np.asarray(S_or_F, dtype=dtype)
    K_arr = np.asarray(K, dtype=dtype)
    T_arr = np.asarray(T, dtype=dtype)
    r_arr = np.asarray(r, dtype=dtype)
    sigma_arr = np.asarray(sigma, dtype=dtype)
    right_arr = np.asarray(right, dtype=object)

    n = len(S_arr)
    resolved = _resolve_greeks_backend(backend, n, cuda_min_rows=cuda_min_rows)

    # CUDA requires chunking; apply conservative default if not set
    effective_batch_size = batch_size
    if resolved == "cuda" and effective_batch_size is None:
        effective_batch_size = _CUDA_DEFAULT_BATCH_SIZE

    if effective_batch_size is None or int(effective_batch_size) >= n:
        return _dispatch_greeks_backend(resolved, model, S_arr, K_arr, T_arr, r_arr, sigma_arr, right_arr, q, dtype)

    bsz = int(effective_batch_size)
    greek_keys = ("delta", "gamma", "vega", "theta", "rho")
    chunks: dict[str, list] = {g: [] for g in greek_keys}

    for start in range(0, n, bsz):
        end = min(start + bsz, n)
        chunk = _dispatch_greeks_backend(
            resolved, model,
            S_arr[start:end], K_arr[start:end], T_arr[start:end],
            r_arr[start:end], sigma_arr[start:end], right_arr[start:end],
            q, dtype,
        )
        for g in greek_keys:
            chunks[g].append(chunk[g])

    return {g: np.concatenate(chunks[g]) for g in greek_keys}


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
    r, _ = _rates.resolve_scalar_rate(cfg)
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
    impl = _models.greek_runtime_model(model)
    right_norm = _models.normalize_right(right)
    if right_norm is None:
        return _nan_greeks()
    domain = _models.validate_pricing_domain(impl, S_or_F, K, T, r, sigma, right_norm)
    if not domain.valid:
        return _nan_greeks()
    model = impl
    right = right_norm

    p0 = price_fn(model, S_or_F, K, T, r, sigma, right, q)
    if not np.isfinite(p0):
        return _nan_greeks()

    # Delta: bump underlying
    bump_s = S_or_F * eps
    if not np.isfinite(bump_s) or bump_s == 0:
        return _nan_greeks()
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

    # Theta: finance convention is calendar-time decay, i.e. -dV/dT.
    bump_t = 1.0 / 365.0  # 1 day
    if T > bump_t:
        p_tup = price_fn(model, S_or_F, K, T + bump_t, r, sigma, right, q)
        p_tdn = price_fn(model, S_or_F, K, T - bump_t, r, sigma, right, q)
        theta = -(p_tup - p_tdn) / (2 * bump_t)
    else:
        theta = 0.0

    # Rho: bump rate
    bump_r = 1e-4  # 1 bp
    p_rup = price_fn(model, S_or_F, K, T, r + bump_r, sigma, right, q)
    p_rdn = price_fn(model, S_or_F, K, T, r - bump_r, sigma, right, q)
    rho = (p_rup - p_rdn) / (2 * bump_r)

    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta, "rho": rho}
