"""Greek Input Contract resolver.

Resolves pricing inputs from a prepared option DataFrame with flexible column names.
Returns a validated input frame and a quality summary — never crashes on bad rows.
Invalid rows receive NaN in all Greek outputs downstream.

Column precedence:
  underlying : underlying_price > S > F > price_std
  iv         : iv > iv_provided (only when iv_source='provided')
  T          : T > compute_dte(as_of_date, expiry, dte_cfg)
  r          : r (row-level) > cfg['rf_rate'] > rf_rate_default

Does NOT mutate the input DataFrame.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from core.dte import compute_dte


_UNDERLYING_COLS = ["underlying_price", "S", "F", "price_std"]
_RIGHT_VALID = {"C", "P"}


def _to_numeric(series: pd.Series) -> pd.Series:
    """Coerce to float, turning strings/bad values into NaN."""
    return pd.to_numeric(series, errors="coerce")


def resolve_greek_inputs(
    df: pd.DataFrame,
    cfg: dict | None = None,
    *,
    iv_source: str = "computed",
    rf_rate_default: float = 0.0,
    dte_cfg: dict | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Resolve Greek pricing inputs from a prepared option DataFrame.

    Args:
        df: Input DataFrame. Must not be mutated.
        cfg: Optional instrument config dict. Used to read rf_rate.
        iv_source: 'computed' (use 'iv' column) or 'provided' (prefer 'iv_provided').
        rf_rate_default: Fallback rate when no row-level 'r' column and no cfg rf_rate.
        dte_cfg: DTE config passed to compute_dte() when T is absent.

    Returns:
        (resolved_df, summary) where resolved_df has canonical columns:
            S_or_F, K, T, r, sigma, right, greek_input_valid, greek_invalid_reason
        and summary contains row-level quality counts.
    """
    cfg = cfg or {}
    dte_cfg = dte_cfg or {"basis": "calendar", "day_count": "act_365", "exclude_expiry_date": False}
    n = len(df)
    out = df.copy()

    invalid_reasons: dict[str, list[bool]] = {
        "missing_underlying": [False] * n,
        "missing_iv": [False] * n,
        "missing_or_expired_T": [False] * n,
        "bad_right": [False] * n,
    }

    # ── Underlying ────────────────────────────────────────────────────────────
    S_or_F = pd.Series(np.nan, index=df.index)
    for col in _UNDERLYING_COLS:
        if col in df.columns:
            col_numeric = _to_numeric(df[col])
            S_or_F = S_or_F.combine_first(col_numeric.where(col_numeric > 0))
    missing_under = S_or_F.isna()
    for i, v in enumerate(missing_under):
        invalid_reasons["missing_underlying"][i] = bool(v)
    out["S_or_F"] = S_or_F

    # ── Strike ────────────────────────────────────────────────────────────────
    if "K" in df.columns:
        out["K"] = _to_numeric(df["K"])
    elif "strike" in df.columns:
        out["K"] = _to_numeric(df["strike"])
    else:
        out["K"] = np.nan

    # ── IV ────────────────────────────────────────────────────────────────────
    sigma = pd.Series(np.nan, index=df.index)
    if iv_source == "provided" and "iv_provided" in df.columns:
        iv_prov = _to_numeric(df["iv_provided"])
        sigma = iv_prov.where(iv_prov > 0)
        if "iv" in df.columns:
            iv_comp = _to_numeric(df["iv"])
            sigma = sigma.combine_first(iv_comp.where(iv_comp > 0))
    elif "iv" in df.columns:
        iv_comp = _to_numeric(df["iv"])
        sigma = iv_comp.where(iv_comp > 0)
    missing_iv = sigma.isna()
    for i, v in enumerate(missing_iv):
        invalid_reasons["missing_iv"][i] = bool(v)
    out["sigma"] = sigma

    # ── T (time to expiry in years) ───────────────────────────────────────────
    T = pd.Series(np.nan, index=df.index)
    if "T" in df.columns:
        T_raw = _to_numeric(df["T"])
        T = T_raw.where(T_raw > 0)
    if T.isna().any() and "as_of_date" in df.columns and "expiry" in df.columns:
        needs_T = T.isna()
        for idx in df.index[needs_T]:
            try:
                t_val = compute_dte(df.at[idx, "as_of_date"], df.at[idx, "expiry"], dte_cfg)
                if t_val > 0:
                    T.at[idx] = t_val
            except Exception:
                pass
    missing_T = T.isna() | (T <= 0)
    for i, v in enumerate(missing_T):
        invalid_reasons["missing_or_expired_T"][i] = bool(v)
    out["T"] = T

    # ── Rate ──────────────────────────────────────────────────────────────────
    cfg_rate = float(cfg.get("rf_rate", rf_rate_default))
    if "r" in df.columns:
        r = _to_numeric(df["r"]).fillna(cfg_rate)
    else:
        r = pd.Series(cfg_rate, index=df.index)
    out["r"] = r

    # ── Right ─────────────────────────────────────────────────────────────────
    if "right" in df.columns:
        right = df["right"].astype(str).str.upper()
    elif "option_type" in df.columns:
        right = df["option_type"].astype(str).str.upper().map(
            {"CALL": "C", "PUT": "P", "C": "C", "P": "P"}
        ).fillna("")
    else:
        right = pd.Series("", index=df.index)
    bad_right = ~right.isin(_RIGHT_VALID)
    for i, v in enumerate(bad_right):
        invalid_reasons["bad_right"][i] = bool(v)
    out["right"] = right

    # ── Strike validity ───────────────────────────────────────────────────────
    missing_strike = out["K"].isna() | (out["K"] <= 0)

    # ── Valid mask ────────────────────────────────────────────────────────────
    invalid_mask = missing_under | missing_iv | missing_T | bad_right | missing_strike
    out["greek_input_valid"] = ~invalid_mask

    # ── Invalid reason string (semicolon-separated) ───────────────────────────
    reason_flags = {
        "missing_underlying": missing_under,
        "missing_strike": missing_strike,
        "missing_iv": missing_iv,
        "missing_or_expired_T": missing_T,
        "bad_right": bad_right,
    }
    reasons_list: list[str] = []
    for i in range(n):
        row_reasons = [name for name, mask in reason_flags.items() if mask.iloc[i]]
        reasons_list.append(";".join(row_reasons))
    out["greek_invalid_reason"] = reasons_list

    # ── Summary ───────────────────────────────────────────────────────────────
    invalid_by_reason = {k: int(sum(v)) for k, v in invalid_reasons.items()}
    invalid_by_reason["missing_strike"] = int(missing_strike.sum())
    summary = {
        "total_rows": n,
        "valid_rows": int((~invalid_mask).sum()),
        "invalid_rows": int(invalid_mask.sum()),
        "invalid_by_reason": invalid_by_reason,
    }

    return out, summary
