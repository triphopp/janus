"""Auditable row-level comparisons between option pricing models."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from core import greeks as _greeks
from core import pricing as _pricing
from core import pricing_models as _models


def _option_mask(df: pd.DataFrame) -> pd.Series:
    if "instrument_type" in df.columns:
        return df["instrument_type"].astype("string").str.lower().eq("option").fillna(False)
    right = df.get("right", pd.Series(index=df.index, dtype="object"))
    strike = df.get("strike", df.get("K", pd.Series(index=df.index, dtype=float)))
    return right.astype("string").str.upper().isin({"C", "P"}) & strike.notna()


def _numeric(row: pd.Series, names: tuple[str, ...]) -> float:
    for name in names:
        if name in row.index:
            try:
                value = float(row[name])
            except (TypeError, ValueError):
                continue
            if np.isfinite(value):
                return value
    return float("nan")


def _model_bounds(model: str, row: pd.Series, cfg: dict) -> tuple[float, float]:
    spec = _models.get_model_spec(model)
    if spec.volatility_unit == "absolute_price_per_sqrt_year":
        configured = cfg.get(
            "normal_iv_solver_bounds",
            (cfg.get("pricing") or {}).get("normal_iv_solver_bounds"),
        )
        if configured is not None:
            return tuple(float(x) for x in configured)
        scale = max(abs(_numeric(row, ("underlying_price", "F", "S", "price_std"))), 1.0)
        return (1e-6, 5.0 * scale)
    return tuple(float(x) for x in cfg.get("iv_solver_bounds", (1e-4, 5.0)))


def _row_model_params(base: dict, row: pd.Series) -> dict:
    params = dict(base)
    exercise = row.get("exercise_style", row.get("contract_exercise_style"))
    if exercise is not None and not pd.isna(exercise):
        params["tree_exercise_style"] = str(exercise).lower()
    underlying_type = row.get("option_underlying_type")
    if underlying_type is not None and not pd.isna(underlying_type):
        params["tree_underlying_type"] = str(underlying_type).lower()
    return params


def compare_models(
    df: pd.DataFrame,
    cfg: dict,
    models: Iterable[str] | None = None,
) -> dict:
    """Compare requested models on option rows using market-implied calibration.

    The long-form result calibrates each model to the observed premium before
    comparing Greeks. A fixed-volatility price difference is emitted only when
    both models consume the same volatility unit, preventing Black percentage
    volatility from being silently fed into Bachelier's absolute-volatility API.
    """
    canonical = _models.canonical_model_name(
        cfg.get("pricing_model", (cfg.get("pricing") or {}).get("model", "black76"))
    )
    configured = models if models is not None else cfg.get("compare_models", [])
    if isinstance(configured, str):
        configured = [configured]
    requested = list(configured)
    requested = list(dict.fromkeys(_models.canonical_model_name(m) for m in requested))
    base_params = _models.runtime_model_params(cfg)
    canonical_spec = _models.get_model_spec(canonical)
    q = float(cfg.get("div_yield", 0.0) or 0.0)

    for model in requested:
        _models.price_runtime_model(model)

    records: list[dict] = []
    for row_index, row in df.loc[_option_mask(df)].iterrows():
        underlying = _numeric(row, ("underlying_price", "F", "S", "price_std"))
        strike = _numeric(row, ("strike", "K"))
        maturity = _numeric(row, ("T",))
        rate = _numeric(row, ("r",))
        market_price = _numeric(row, ("option_price", "price"))
        canonical_iv = _numeric(row, ("iv",))
        right = str(row.get("right", "")).upper()
        canonical_delta = _numeric(row, ("delta",))
        params = _row_model_params(base_params, row)

        if not np.isfinite(canonical_delta) and all(
            np.isfinite(x) for x in (underlying, strike, maturity, rate, canonical_iv)
        ):
            canonical_delta = _greeks.single_leg_greeks(
                canonical,
                underlying,
                strike,
                maturity,
                rate,
                canonical_iv,
                right,
                q=q,
                shift=params.get("shift"),
                model_params=params,
            )["delta"]

        for comparison in requested:
            spec = _models.get_model_spec(comparison)
            record = {
                "row_index": row_index,
                "as_of_date": row.get("as_of_date"),
                "expiry": row.get("expiry"),
                "delivery_month": row.get("delivery_month"),
                "right": right,
                "strike": strike,
                "underlying": underlying,
                "T": maturity,
                "r": rate,
                "market_price": market_price,
                "canonical_model": canonical,
                "comparison_model": comparison,
                "canonical_volatility_unit": canonical_spec.volatility_unit,
                "comparison_volatility_unit": spec.volatility_unit,
                "canonical_iv": canonical_iv,
                "canonical_delta": canonical_delta,
                "comparison_iv": np.nan,
                "iv_difference": np.nan,
                "comparison_delta": np.nan,
                "delta_difference": np.nan,
                "model_price_at_canonical_iv": np.nan,
                "price_difference_at_canonical_iv": np.nan,
                "calibrated_price": np.nan,
                "calibration_residual": np.nan,
                "comparison_status": "invalid_input",
            }
            if not all(np.isfinite(x) for x in (underlying, strike, maturity, rate, market_price)):
                records.append(record)
                continue
            if right not in {"C", "P"} or maturity <= 0 or market_price <= 0:
                records.append(record)
                continue
            if spec.family not in {canonical_spec.family, "generic_options"}:
                record["comparison_status"] = "model_family_mismatch"
                records.append(record)
                continue

            solved_iv = _pricing.solve_iv(
                comparison,
                market_price,
                underlying,
                strike,
                maturity,
                rate,
                right,
                q=q,
                bounds=_model_bounds(comparison, row, cfg),
                shift=params.get("shift"),
                model_params=params,
            )
            record["comparison_iv"] = solved_iv
            if not np.isfinite(solved_iv):
                record["comparison_status"] = "iv_not_solved"
                records.append(record)
                continue

            calibrated = _pricing.price(
                comparison,
                underlying,
                strike,
                maturity,
                rate,
                solved_iv,
                right,
                q=q,
                shift=params.get("shift"),
                model_params=params,
            )
            comparison_delta = _greeks.single_leg_greeks(
                comparison,
                underlying,
                strike,
                maturity,
                rate,
                solved_iv,
                right,
                q=q,
                shift=params.get("shift"),
                model_params=params,
            )["delta"]
            record["calibrated_price"] = calibrated
            record["calibration_residual"] = calibrated - market_price
            record["comparison_delta"] = comparison_delta
            record["delta_difference"] = comparison_delta - canonical_delta
            if spec.volatility_unit == canonical_spec.volatility_unit:
                record["iv_difference"] = solved_iv - canonical_iv
                fixed_vol_price = _pricing.price(
                    comparison,
                    underlying,
                    strike,
                    maturity,
                    rate,
                    canonical_iv,
                    right,
                    q=q,
                    shift=params.get("shift"),
                    model_params=params,
                )
                record["model_price_at_canonical_iv"] = fixed_vol_price
                record["price_difference_at_canonical_iv"] = fixed_vol_price - market_price
            record["comparison_status"] = "ok"
            records.append(record)

    frame = pd.DataFrame.from_records(records)
    by_model: dict[str, dict] = {}
    if not frame.empty:
        for model, group in frame.groupby("comparison_model", dropna=False):
            ok = group["comparison_status"].eq("ok")
            by_model[str(model)] = {
                "rows": int(len(group)),
                "rows_ok": int(ok.sum()),
                "rows_failed": int((~ok).sum()),
                "mean_abs_calibration_residual": _mean_abs(group.loc[ok, "calibration_residual"]),
                "mean_abs_delta_difference": _mean_abs(group.loc[ok, "delta_difference"]),
                "mean_abs_price_difference_at_canonical_iv": _mean_abs(
                    group.loc[ok, "price_difference_at_canonical_iv"]
                ),
            }
    summary = {
        "canonical_model": canonical,
        "comparison_models": requested,
        "rows": int(len(frame)),
        "by_model": by_model,
    }
    return {"frame": frame, "summary": summary}


def _mean_abs(values: pd.Series) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    return None if numeric.empty else float(numeric.abs().mean())


def write_model_comparison(df: pd.DataFrame, cfg: dict, run_dir: str | Path) -> dict:
    """Write comparison CSV and JSON summary under the run tables directory."""
    built = compare_models(df, cfg)
    tables = Path(run_dir) / "tables"
    tables.mkdir(parents=True, exist_ok=True)
    csv_path = tables / "model_comparison.csv"
    json_path = tables / "model_comparison_summary.json"
    built["frame"].to_csv(csv_path, index=False)
    json_path.write_text(json.dumps(built["summary"], indent=2, default=str), encoding="utf-8")
    return {
        **built["summary"],
        "csv": str(csv_path),
        "summary_json": str(json_path),
    }
