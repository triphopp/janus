"""Run presets and universe presets for the user-facing CLI.

Presets replace a wall of low-level flags with a small set of named intents.
A *run preset* picks the trust posture (guards, reproducibility). A *universe
preset* picks which option rows enter pricing/metrics. Low-level values (DTE
bands, IV caps, delta bands) live here in config-backed definitions instead of
in default ``--help``.
"""

from __future__ import annotations

import copy

__all__ = [
    "RUN_PRESETS",
    "UNIVERSE_PRESETS",
    "PresetError",
    "apply_run_preset",
    "apply_universe_preset",
    "parse_override",
    "apply_overrides",
]


class PresetError(ValueError):
    """Raised on an unknown preset or malformed override."""


# Run presets describe trust posture, not numbers.
#   require_pinned   -> an official run refuses unpinned file-backed data
#   reproducible     -> outputs are labelled reproducible
#   allow_provider   -> live/provider fetch is permitted (non-reproducible)
RUN_PRESETS: dict[str, dict] = {
    "official": {
        "require_pinned": True,
        "reproducible": True,
        "allow_provider": False,
        "description": "Trusted backtest/export. Hash-pinned source required; "
        "fail closed on P0 integrity gates.",
    },
    "diagnostic": {
        "require_pinned": False,
        "reproducible": False,
        "allow_provider": True,
        "description": "Fast exploration / provider reads. Outputs marked "
        "non-reproducible when input is not pinned.",
    },
    "export": {
        "require_pinned": True,
        "reproducible": True,
        "allow_provider": False,
        "compute_greeks": True,
        "description": "Downstream artifacts (option_chain_greeks). Export "
        "withheld when readiness is blocked.",
    },
    "research": {
        "require_pinned": True,
        "reproducible": True,
        "allow_provider": False,
        "description": "Explicit research-universe choices; every override "
        "recorded in summary/manifest with guards visible.",
    },
}

DEFAULT_PRESET = "official"


# Universe presets are config fragments merged into option_universe.
UNIVERSE_PRESETS: dict[str, dict] = {
    "all": {
        "description": "No research filters beyond expiry-day removal.",
        "option_universe": {"min_dte_days": 1},
    },
    "liquid": {
        "description": "Tradable options: priced, bounded IV, mid delta band.",
        "option_universe": {
            "min_dte_days": 1,
            "max_dte_days": 365,
            "min_option_price": 0.05,
            "max_iv": 3.0,
            "delta_band": {"min_abs_delta": 0.10, "max_abs_delta": 0.90},
        },
    },
    "near-term": {
        "description": "Front of the surface: <= 90 DTE.",
        "option_universe": {"min_dte_days": 1, "max_dte_days": 90},
    },
}

DEFAULT_UNIVERSE = "all"


def apply_run_preset(cfg: dict, preset: str) -> dict:
    """Return a copy of cfg with run-preset posture applied.

    Sets ``require_fixed_data_version``, ``reproducible``, and ``preset`` markers.
    Does not by itself attach a data source — that is the plan's job.
    """
    if preset not in RUN_PRESETS:
        known = ", ".join(sorted(RUN_PRESETS))
        raise PresetError(f"unknown preset {preset!r}. Known presets: {known}")
    spec = RUN_PRESETS[preset]
    out = copy.deepcopy(cfg)
    out["preset"] = preset
    out["reproducible"] = bool(spec["reproducible"])
    out["require_fixed_data_version"] = bool(spec["require_pinned"])
    if spec.get("compute_greeks"):
        out.setdefault("pricing", {})["compute_greeks"] = True
        out["compute_greeks"] = True
    return out


def _deep_merge(base: dict, overlay: dict) -> dict:
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = copy.deepcopy(v)
    return base


def apply_universe_preset(cfg: dict, universe: str) -> dict:
    """Merge a named universe preset into cfg.option_universe.

    ``custom:<name>`` defers to a config-backed ``universe_presets.<name>`` block
    in the instrument YAML, so advanced research universes stay in config.
    """
    out = copy.deepcopy(cfg)
    if universe.startswith("custom:"):
        name = universe.split(":", 1)[1]
        custom = (out.get("universe_presets") or {}).get(name)
        if not custom:
            raise PresetError(
                f"custom universe {name!r} not found in instrument config "
                "under universe_presets."
            )
        out["universe"] = universe
        _deep_merge(out.setdefault("option_universe", {}), custom.get("option_universe", custom))
        return out

    if universe not in UNIVERSE_PRESETS:
        known = ", ".join(sorted(UNIVERSE_PRESETS)) + ", custom:<name>"
        raise PresetError(f"unknown universe {universe!r}. Known: {known}")
    out["universe"] = universe
    fragment = UNIVERSE_PRESETS[universe].get("option_universe", {})
    _deep_merge(out.setdefault("option_universe", {}), fragment)
    return out


def _coerce(value: str):
    low = value.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("none", "null"):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def parse_override(token: str) -> tuple[str, object]:
    """Parse ``a.b.c=value`` into a dotted key and a coerced value."""
    if "=" not in token:
        raise PresetError(f"override must be key=value, got {token!r}")
    key, _, raw = token.partition("=")
    key = key.strip()
    if not key:
        raise PresetError(f"override has empty key: {token!r}")
    return key, _coerce(raw)


def apply_overrides(cfg: dict, overrides: list[str] | None) -> tuple[dict, dict]:
    """Apply ``--override a.b=c`` advanced overrides onto cfg.

    Returns ``(cfg, recorded)`` where ``recorded`` maps dotted key -> value, so
    the run can write the overrides into summary/manifest.
    """
    out = copy.deepcopy(cfg)
    recorded: dict = {}
    for token in overrides or []:
        key, value = parse_override(token)
        recorded[key] = value
        node = out
        parts = key.split(".")
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                node[part] = nxt
            node = nxt
        node[parts[-1]] = value
        _sync_normalized_override(out, key, value)
    if recorded:
        out.setdefault("runtime_overrides", {}).update(recorded)
        out["advanced_overrides"] = recorded
    return out, recorded


def _sync_normalized_override(cfg: dict, dotted_key: str, value: object) -> None:
    """Keep advanced nested overrides aligned with normalized flat keys.

    Profiles are normalized before presets/overrides are applied. Without this
    sync, ``--override pricing.compute_greeks=true`` can coexist with an older
    top-level ``compute_greeks=false`` and the pipeline will read the stale flat
    value. The CLI should treat the advanced override as authoritative.
    """
    aliases = {
        "pricing.compute_greeks": "compute_greeks",
        "pricing.div_yield": "div_yield",
        "pricing.iv_validate_threshold": "iv_validate_threshold",
        "pricing.iv_solver_bounds": "iv_solver_bounds",
        "pricing.vega_bucket_cutoff": "vega_bucket_cutoff",
        "pricing.vega_beta": "vega_beta",
        "pricing.greeks_backend": "greeks_backend",
        "pricing.greeks_batch_size": "greeks_batch_size",
        "pricing.greeks_dtype": "greeks_dtype",
        "pricing.greeks_cuda_min_rows": "greeks_cuda_min_rows",
        "cv.n_folds": "n_folds",
        "cv.purge_bars": "purge_bars",
        "cv.event_embargo_bars": "event_embargo_bars",
        "cv.regime_axes": "regime_axes",
        "cv.max_concentration": "max_concentration",
        "cv.kl_threshold": "kl_threshold",
        "cv.js_threshold": "js_threshold",
        "validation.min_oi": "min_oi",
        "validation.outlier_k": "outlier_k",
        "validation.iv_cap": "iv_cap",
        "validation.min_volume": "min_volume",
        "validation.futures_oi_floor": "futures_oi_floor",
        "validation.roll_days": "roll_days",
        "performance.n_trials": "n_trials",
        "performance.rf_rate_source": "rf_rate_col",
        "stability.psi_threshold": "psi_threshold",
        "stability.psi_bins": "psi_bins",
        "stability.feature_cols": "feature_cols",
        "stability.target_col": "target_col",
        "stability.forward_return_col": "forward_return_col",
        "stability.stability_grain": "stability_grain",
        "columns.price_col": "price_col",
        "columns.vol_col": "vol_col",
        "columns.return_col": "return_col",
        "outlier_policy.return_action": "return_action",
        "outlier_policy.derived_return_col": "derived_return_col",
        "outlier_policy.metrics_return_col": "metrics_return_col",
    }
    flat_key = aliases.get(dotted_key)
    if flat_key:
        cfg[flat_key] = value
