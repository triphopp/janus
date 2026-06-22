"""CaseBuilder — build OutlierCasePackage from tagged outlier rows.

Input rows come from scanner.load_tagged_return_outliers(run_id)["rows"].
case_id is stable and deterministic per the contract in Section 1 of the
deterministic contract plan.
"""

from __future__ import annotations

import math
from typing import Any

from .schema import OutlierCasePackage
from .ids import case_id as make_case_id

_EQUITY_SOURCE_HINTS = ["SEC", "earnings", "analyst"]
_FUTURES_SOURCE_HINTS = ["EIA", "OPEC", "CME", "settlement"]
_OPTIONS_SOURCE_HINTS = ["earnings", "FDA", "merger"]

_DIRECTION_TERMS_HIGH = ["rise", "jump", "surge", "rally", "beat", "upgrade", "approval"]
_DIRECTION_TERMS_LOW = ["fall", "drop", "plunge", "selloff", "miss", "downgrade"]
_DIRECTION_TERMS_NEUTRAL = ["move", "change", "price"]


def _clean_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _direction_terms(direction: str) -> list[str]:
    if direction == "high":
        return list(_DIRECTION_TERMS_HIGH)
    if direction == "low":
        return list(_DIRECTION_TERMS_LOW)
    return list(_DIRECTION_TERMS_NEUTRAL)


def _family_source_hints(family: str | None) -> list[str]:
    f = (family or "").lower()
    if f == "futures":
        return list(_FUTURES_SOURCE_HINTS)
    if f in ("equity_options", "futures_options"):
        return list(_OPTIONS_SOURCE_HINTS)
    return list(_EQUITY_SOURCE_HINTS)


def _sanitize_local_context(row: dict, run_id: str) -> dict:
    safe: dict[str, Any] = {"run_id": run_id}
    scalar_keys = [
        "as_of_date", "symbol", "instrument", "family",
        "_return_outlier_direction", "_return_outlier_severity",
        "_return_outlier_reason", "_return_validation_status",
        "signal_type",
    ]
    for k in scalar_keys:
        if k in row and row[k] is not None:
            safe[k] = str(row[k])
    float_keys = ["return_std", "return_raw", "_return_outlier_zscore",
                  "_return_prior_median"]
    for k in float_keys:
        val = _clean_float(row.get(k))
        if val is not None:
            safe[k] = val
    return safe


def build_case_package_from_tagged_return_outlier(
    *,
    run_id: str,
    row: dict,
    run_context: dict,
) -> OutlierCasePackage:
    family = run_context.get("family")
    instrument = run_context.get("instrument")
    symbol = row.get("symbol") or row.get("Symbol")
    as_of_date = str(row.get("as_of_date", ""))[:10]
    metric_name = "return_std"

    cid = make_case_id(
        run_id=run_id,
        signal_type="return_outlier",
        as_of_date=as_of_date,
        metric_name=metric_name,
        family=family,
        symbol=symbol,
        instrument=instrument,
    )

    direction = str(row.get("_return_outlier_direction", "")).lower()
    severity = str(row.get("_return_outlier_severity", "")).lower()

    observed_value = _clean_float(row.get("return_std") or row.get("return_raw"))
    baseline_value = _clean_float(row.get("_return_prior_median"))
    z_score = _clean_float(row.get("_return_outlier_zscore"))
    pct_change = _clean_float(row.get("return_raw"))

    return OutlierCasePackage(
        case_id=cid,
        run_id=run_id,
        signal_type="return_outlier",
        as_of_date=as_of_date,
        family=family,
        instrument=instrument,
        symbol=symbol,
        severity=severity or None,
        metric_name=metric_name,
        observed_value=observed_value,
        baseline_value=baseline_value,
        z_score=z_score,
        pct_change=pct_change,
        local_context=_sanitize_local_context(row, run_id),
        protected_columns=[],
        candidate_terms=_direction_terms(direction),
        source_hints=_family_source_hints(family),
    )
