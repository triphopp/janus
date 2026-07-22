"""Risk-free rate resolution for pricing and Greeks.

This module is the single place that may turn config, constants, or a sourced
rate table into the per-row ``r`` column consumed by pricers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


DEFAULT_RISK_FREE_RATE = 0.05
RATE_CONVENTION = "continuously_compounded_act365"


def simple_act360_to_cc_act365(rate: float | pd.Series, tenor_days: float = 1.0):
    """Convert annualized simple ACT/360 money-market rates to cc ACT/365."""
    tenor = float(tenor_days)
    if tenor <= 0:
        raise ValueError("tenor_days must be positive")
    return (365.0 / tenor) * np.log1p(pd.to_numeric(rate, errors="coerce") * tenor / 360.0)


def resolve_scalar_rate(cfg: dict | None = None, *, default: float | None = None) -> tuple[float, dict]:
    """Resolve a scalar rate through the same policy as row-level resolution."""
    frame = pd.DataFrame({"_row": [0]})
    rates, summary = resolve_rate(frame, cfg or {}, default=default)
    value = float(rates.iloc[0]) if pd.notna(rates.iloc[0]) else np.nan
    return value, summary


def stamp_rate(
    df: pd.DataFrame,
    cfg: dict | None = None,
    *,
    default: float | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Return a copy of ``df`` with a resolved per-row ``r`` column."""
    out = df.copy()
    out["r"], summary = resolve_rate(out, cfg or {}, default=default)
    return out, summary


def resolve_rate(
    df: pd.DataFrame,
    cfg: dict | None = None,
    *,
    default: float | None = None,
) -> tuple[pd.Series, dict]:
    """Resolve the per-row discount rate used by pricing engines.

    Precedence is:
      1. existing finite ``df["r"]`` values;
      2. configured source, currently ``sofr`` when a rate table is supplied;
      3. configured scalar ``rf_rate``;
      4. the module fallback ``DEFAULT_RISK_FREE_RATE``.

    The fallback is deliberately loud in the returned summary.
    """
    cfg = cfg or {}
    fallback = DEFAULT_RISK_FREE_RATE if default is None else float(default)
    n = len(df)
    index = df.index
    out = pd.Series(np.nan, index=index, dtype=float)

    existing_mask = pd.Series(False, index=index)
    if "r" in df.columns:
        existing = pd.to_numeric(df["r"], errors="coerce")
        existing_mask = existing.notna() & np.isfinite(existing)
        out.loc[existing_mask] = existing.loc[existing_mask]

    requested_source = _rate_source(cfg)
    source_series = pd.Series(np.nan, index=index, dtype=float)
    source_status = "not_requested"
    source_message = None
    if requested_source == "sofr":
        source_data = _rate_source_frame(cfg)
        if source_data is None:
            source_status = "missing_source"
            source_message = "rf_rate_source=sofr configured but no SOFR rate table was provided"
        else:
            source_series = _join_pit_rate(df, source_data, cfg)
            source_status = "joined"
    elif requested_source in {"constant", "configured", "scalar", ""}:
        source_status = "constant"
    else:
        source_status = "unsupported_source"
        source_message = f"unsupported rf_rate_source={requested_source!r}; using fallback policy"

    unresolved = out.isna()
    source_mask = unresolved & source_series.notna() & np.isfinite(source_series)
    out.loc[source_mask] = source_series.loc[source_mask]

    configured_rate = _configured_scalar_rate(cfg)
    configured_mask = pd.Series(False, index=index)
    if configured_rate is not None:
        unresolved = out.isna()
        configured_mask = unresolved
        out.loc[configured_mask] = configured_rate

    unresolved = out.isna()
    fallback_mask = unresolved
    out.loc[fallback_mask] = fallback

    fallback_rows = int(fallback_mask.sum())
    existing_rows = int(existing_mask.sum())
    sourced_rows = int(source_mask.sum())
    configured_rows = int(configured_mask.sum())
    non_fallback_rows = existing_rows + sourced_rows + configured_rows
    resolved_rows = int(out.notna().sum())

    status = "pass"
    warnings: list[str] = []
    if source_message:
        warnings.append(source_message)
    if requested_source == "sofr" and source_status == "missing_source":
        status = "fail"
    elif fallback_rows:
        status = "warn"
        warnings.append(f"{fallback_rows} rows used fallback risk-free rate {fallback:.8f}")

    summary = {
        "status": status,
        "source_requested": requested_source,
        "source_status": source_status,
        "source_used": _source_used(existing_rows, sourced_rows, configured_rows, fallback_rows),
        "rows": int(n),
        "coverage_pct": float(non_fallback_rows / n) if n else 1.0,
        "source_coverage_pct": float(sourced_rows / n) if n else 1.0,
        "resolved_coverage_pct": float(resolved_rows / n) if n else 1.0,
        "existing_r_rows": existing_rows,
        "sourced_rows": sourced_rows,
        "configured_rows": configured_rows,
        "fallback_rows": fallback_rows,
        "fallback_rate": float(fallback),
        "configured_rate": configured_rate,
        "convention": RATE_CONVENTION,
        "warnings": warnings,
    }
    return out, summary


def _source_used(existing_rows: int, sourced_rows: int, configured_rows: int, fallback_rows: int) -> str:
    parts = []
    if existing_rows:
        parts.append("existing_r")
    if sourced_rows:
        parts.append("sourced")
    if configured_rows:
        parts.append("configured_rf_rate")
    if fallback_rows:
        parts.append("fallback")
    return "+".join(parts) if parts else "none"


def _rate_source(cfg: dict) -> str:
    performance = cfg.get("performance") or {}
    source = (
        cfg.get("rf_rate_source")
        or performance.get("rf_rate_source")
        or cfg.get("rate_source")
        or cfg.get("rf_rate_col")
    )
    if source is None:
        return "constant"
    return str(source).strip().lower()


def _configured_scalar_rate(cfg: dict) -> float | None:
    value = cfg.get("rf_rate")
    if value is None:
        pricing = cfg.get("pricing") or {}
        value = pricing.get("rf_rate")
    if value is None:
        return None
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    return rate if np.isfinite(rate) else None


def _rate_source_frame(cfg: dict) -> pd.DataFrame | None:
    for key in ("rate_series", "rates", "sofr_rates", "rate_data"):
        if key in cfg and cfg[key] is not None:
            return _coerce_rate_frame(cfg[key])
    path = cfg.get("rate_data_path") or cfg.get("sofr_path")
    if path:
        p = Path(path)
        if not p.exists():
            return None
        if p.suffix.lower() == ".parquet":
            return pd.read_parquet(p)
        return pd.read_csv(p)
    return None


def _coerce_rate_frame(value: Any) -> pd.DataFrame:
    if isinstance(value, pd.DataFrame):
        return value.copy()
    if isinstance(value, pd.Series):
        return value.rename("rate").reset_index()
    return pd.DataFrame(value)


def _join_pit_rate(df: pd.DataFrame, rates: pd.DataFrame, cfg: dict) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=float, index=df.index)

    rate_df = rates.copy()
    date_col = _first_present(rate_df, ("as_of_date", "date", "effective_at", "published_at"))
    rate_col = _first_present(rate_df, ("r", "rate", "sofr", "value", "rate_value"))
    if date_col is None or rate_col is None:
        return pd.Series(np.nan, index=df.index, dtype=float)

    rate_df["_rate_date"] = pd.to_datetime(rate_df[date_col], errors="coerce")
    if "available_at" in rate_df.columns:
        rate_df["_rate_available_at"] = pd.to_datetime(rate_df["available_at"], errors="coerce", utc=True)
    else:
        rate_df["_rate_available_at"] = rate_df["_rate_date"].dt.tz_localize("UTC", nonexistent="NaT", ambiguous="NaT")

    rate_values = pd.to_numeric(rate_df[rate_col], errors="coerce")
    if str(cfg.get("rate_unit", cfg.get("rf_rate_unit", "decimal"))).lower() in {"percent", "pct"}:
        rate_values = rate_values / 100.0
    rate_df["_rate"] = simple_act360_to_cc_act365(
        rate_values,
        tenor_days=float(cfg.get("rate_tenor_days", cfg.get("sofr_tenor_days", 1.0))),
    )
    rate_df = rate_df.dropna(subset=["_rate_available_at", "_rate", "_rate_date"]).sort_values("_rate_available_at")
    if rate_df.empty:
        return pd.Series(np.nan, index=df.index, dtype=float)

    left = pd.DataFrame({"_idx": df.index})
    if "available_at" in df.columns:
        left["_row_available_at"] = pd.to_datetime(df["available_at"], errors="coerce", utc=True)
    else:
        left["_row_available_at"] = _row_available_at(df, cfg)
    left = left.dropna(subset=["_row_available_at"]).sort_values("_row_available_at")
    if left.empty:
        return pd.Series(np.nan, index=df.index, dtype=float)

    joined = pd.merge_asof(
        left,
        rate_df[["_rate_available_at", "_rate"]],
        left_on="_row_available_at",
        right_on="_rate_available_at",
        direction="backward",
    )
    out = pd.Series(np.nan, index=df.index, dtype=float)
    out.loc[joined["_idx"]] = joined["_rate"].to_numpy()
    return out


def _row_available_at(df: pd.DataFrame, cfg: dict) -> pd.Series:
    if "as_of_date" not in df.columns:
        return pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")
    base = pd.to_datetime(df["as_of_date"], errors="coerce")
    settlement_time = str(cfg.get("settlement_release_time", "00:00"))
    if len(settlement_time.split(":")) == 2:
        settlement_time = f"{settlement_time}:00"
    local_text = base.dt.strftime("%Y-%m-%d") + " " + settlement_time
    tz = cfg.get("exchange_tz") or cfg.get("timezone") or "UTC"
    local = pd.to_datetime(local_text, errors="coerce")
    try:
        return local.dt.tz_localize(tz, nonexistent="NaT", ambiguous="NaT").dt.tz_convert("UTC")
    except Exception:
        return pd.to_datetime(local_text, errors="coerce", utc=True)


def _first_present(df: pd.DataFrame, names: tuple[str, ...]) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None
