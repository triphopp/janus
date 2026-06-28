"""Unit registry for critical numeric fields — starting with option IV (issue 002).

Silent IV scaling errors create 100x (percent treated as decimal) or 0.01x (decimal
divided twice) mistakes that quietly invalidate every downstream Greek and backtest.
No loader may divide or multiply IV without:

1. preserving the raw value and the declared raw unit, and
2. recording the canonical value plus the exact scale factor.

This module is the single source of truth for that conversion. It also runs a smoke
check on the canonical result so an obviously-wrong scale (percent-as-decimal,
decimal-divided-twice) blocks an official run instead of shipping corrupt Greeks.

Pure functions over pandas Series / scalars; no I/O.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

# Declared raw unit -> multiplicative factor that converts it to canonical decimal.
#   decimal: 0.2914 already                       -> x1
#   percent: 29.14 (percent points)               -> x0.01
#   bps:     2914 basis points                     -> x0.0001
KNOWN_IV_UNITS: dict[str, float] = {
    "decimal": 1.0,
    "fraction": 1.0,
    "percent": 0.01,
    "percentage": 0.01,
    "pct": 0.01,
    "bps": 1e-4,
    "basis_points": 1e-4,
}

CANONICAL_IV_UNIT = "decimal"

# Plausible band for an annualized IV expressed as a decimal. Outside this band the
# canonical value almost certainly came from the wrong scale.
_SANE_IV_LOW = 0.01      # 1% vol — below this, likely divided twice (0.01x)
_SANE_IV_HIGH = 5.0      # 500% vol — above this, likely percent-not-divided (100x)


class UnknownUnitError(ValueError):
    """Raised when a declared unit is not in the registry."""


def iv_scale_factor(declared_unit: Optional[str]) -> float:
    """Return the canonical-decimal scale factor for a declared IV unit.

    Raises:
        UnknownUnitError: if the unit is missing or unrecognized. An official run
            must declare a known IV unit before its IV data can be trusted.
    """
    if declared_unit is None:
        raise UnknownUnitError(
            "IV unit is undeclared; declare one of "
            f"{sorted(set(KNOWN_IV_UNITS))} before trusting IV"
        )
    key = str(declared_unit).strip().lower()
    if key not in KNOWN_IV_UNITS:
        raise UnknownUnitError(
            f"unknown IV unit {declared_unit!r}; known units: "
            f"{sorted(set(KNOWN_IV_UNITS))}"
        )
    return KNOWN_IV_UNITS[key]


def iv_scale_smoke(
    canonical_iv,
    low: float = _SANE_IV_LOW,
    high: float = _SANE_IV_HIGH,
) -> dict:
    """Smoke-check a canonical decimal IV series for an obvious scaling mistake.

    Returns a status dict::

        {"status": "pass"|"fail"|"not_checked", "reason": str|None, "median": float|None}

    ``fail`` means the canonical values look like the wrong scale (percent-as-decimal
    or decimal-divided-twice) and an official run should block.
    """
    iv = pd.to_numeric(pd.Series(canonical_iv), errors="coerce").dropna()
    iv = iv[iv > 0]
    if iv.empty:
        return {"status": "not_checked", "reason": "no positive IV values", "median": None}

    median = float(iv.median())
    if median > high:
        return {
            "status": "fail",
            "reason": (
                f"canonical IV median {median:.4f} > {high}: looks like percent "
                "treated as decimal (missing /100)"
            ),
            "median": median,
        }
    if median < low:
        return {
            "status": "fail",
            "reason": (
                f"canonical IV median {median:.6f} < {low}: looks like decimal "
                "divided by 100 twice"
            ),
            "median": median,
        }
    return {"status": "pass", "reason": None, "median": median}


def normalize_iv(raw_iv, declared_unit: Optional[str]) -> dict:
    """Convert raw provider IV to canonical decimal IV with full provenance.

    Args:
        raw_iv: scalar or Series of provider IV in ``declared_unit``.
        declared_unit: the raw unit, e.g. ``"percent"`` or ``"decimal"``.

    Returns:
        dict with keys: ``raw_unit``, ``canonical_unit``, ``scale_factor``,
        ``canonical`` (scalar or Series), and ``smoke`` (see :func:`iv_scale_smoke`).

    Raises:
        UnknownUnitError: if ``declared_unit`` is missing/unknown.
    """
    factor = iv_scale_factor(declared_unit)
    is_series = isinstance(raw_iv, (pd.Series, pd.Index, list, tuple, np.ndarray))
    raw_series = pd.to_numeric(pd.Series(raw_iv), errors="coerce")
    canonical = raw_series * factor

    return {
        "raw_unit": str(declared_unit).strip().lower(),
        "canonical_unit": CANONICAL_IV_UNIT,
        "scale_factor": factor,
        "canonical": canonical if is_series else (
            float(canonical.iloc[0]) if canonical.notna().iloc[0] else np.nan
        ),
        "smoke": iv_scale_smoke(canonical),
    }


def iv_unit_assumption(declared_unit: Optional[str], raw_iv=None) -> dict:
    """Build the manifest ``unit_assumptions.iv`` record for a declared IV unit.

    Safe to call with unknown units: records ``known=False`` instead of raising so
    the manifest can carry the assumption while a gate blocks the official run.
    """
    key = None if declared_unit is None else str(declared_unit).strip().lower()
    known = key in KNOWN_IV_UNITS
    record = {
        "field": "implied_volatility",
        "raw_unit": key,
        "canonical_unit": CANONICAL_IV_UNIT,
        "known": known,
        "scale_factor": KNOWN_IV_UNITS.get(key) if known else None,
    }
    if known and raw_iv is not None:
        record["smoke"] = iv_scale_smoke(pd.to_numeric(pd.Series(raw_iv), errors="coerce") * KNOWN_IV_UNITS[key])
    return record
