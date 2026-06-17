"""Config normalization helpers shared by pipeline and adapters."""

from copy import deepcopy
from typing import Any


def normalize_config(cfg: dict | None) -> dict:
    """Return a config with nested instrument sections lifted to core keys.

    Instrument YAMLs keep domain settings grouped under sections such as
    ``pricing``, ``cv`` and ``validation``. Core modules consume flat keys, so
    adapters normalize once before doing any pricing or validation math.
    Existing top-level keys win over nested defaults.
    """
    out = deepcopy(cfg or {})

    pricing = out.get("pricing") or {}
    cv = out.get("cv") or {}
    validation = out.get("validation") or {}
    performance = out.get("performance") or {}
    columns = out.get("columns") or {}
    stability = out.get("stability") or {}
    outlier_policy = out.get("outlier_policy") or {}

    _setdefault_from(out, "pricing_model", pricing, "model")
    for key in (
        "div_yield",
        "iv_validate_threshold",
        "iv_solver_bounds",
        "vega_bucket_cutoff",
        "vega_beta",
    ):
        _setdefault_from(out, key, pricing, key)

    for key in (
        "n_folds",
        "purge_bars",
        "event_embargo_bars",
        "regime_axes",
        "max_concentration",
        "kl_threshold",
        "js_threshold",
    ):
        _setdefault_from(out, key, cv, key)

    for key in ("min_oi", "outlier_k", "iv_cap", "min_volume", "futures_oi_floor", "roll_days"):
        _setdefault_from(out, key, validation, key)

    _setdefault_from(out, "n_trials", performance, "n_trials")
    _setdefault_from(out, "rf_rate_col", performance, "rf_rate_source")

    for key in (
        "psi_threshold",
        "psi_bins",
        "feature_cols",
        "target_col",
        "forward_return_col",
        "stability_grain",
    ):
        _setdefault_from(out, key, stability, key)

    for key in ("price_col", "vol_col", "return_col"):
        _setdefault_from(out, key, columns, key)

    for key in ("return_action", "derived_return_col", "metrics_return_col"):
        _setdefault_from(out, key, outlier_policy, key)

    return out


def _setdefault_from(target: dict, target_key: str, source: dict, source_key: str) -> None:
    value: Any = source.get(source_key)
    if target_key not in target and value is not None:
        target[target_key] = value
