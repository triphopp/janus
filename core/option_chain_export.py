"""Downstream option-chain Greeks export (issues 023 + 024).

An *additive* market-facing dataset: a clean daily option chain with full Black-76
Greeks for downstream consumers (Lean, research notebooks, …). It is NOT a
data-quality report — only rows that pass the release gate are exported, with
finance-friendly headers and no review/debug/raw-vendor columns. The existing
prepared/dashboard/report artifacts are untouched.

Two gates protect the export:

- run-level: if option-market readiness is ``blocked`` the export is withheld
  entirely (the run is not trustworthy enough to publish a market dataset);
- row-level: only clean, fully-priced option rows that reconcile to an underlying
  future are written; flagged/quarantined rows stay in the review artifacts.

``COLUMN_SPEC`` is the single source of truth: the CSV projection, ``schema.json``,
and ``data_dictionary.md`` are all derived from it so they cannot drift apart
(issue 024).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from core import greeks as _greeks
from core import pricing_models as _pricing_models

# Futures month codes for building underlying/option symbols.
_MONTH_CODE = {1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
               7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z"}

PRICE_DP = 2
IV_DP = 6
GREEK_DP = 8

# ── Single source of truth: one spec per exported column ──────────────────────
# Drives CSV column order/formatting, schema.json, and data_dictionary.md.
COLUMN_SPEC: list[dict] = [
    {
        "name": "trade_date", "domain_label": "Trade Date",
        "description": "Market session date described by the settlement row. NOT the "
                       "time a downstream consumer may act on the row.",
        "source_field": "as_of_date (raw TRADE DATE)",
        "source_transform": "ISO 8601 date; market session date",
        "dtype": "string", "format": "date", "unit": None, "precision": None,
        "nullable": False, "allowed_values": None,
        "example_canonical": "2024-09-25", "example_display": "2024-09-25",
    },
    {
        "name": "product", "domain_label": "Product",
        "description": "Domain product label.",
        "source_field": "config product policy",
        "source_transform": "uppercase market label",
        "dtype": "string", "format": "string", "unit": None, "precision": None,
        "nullable": False, "allowed_values": None,
        "example_canonical": "PRODUCT", "example_display": "Product",
    },
    {
        "name": "underlying_symbol", "domain_label": "Underlying Futures",
        "description": "Underlying futures contract for the option's delivery month.",
        "source_field": "underlying_root + delivery_month",
        "source_transform": "root + month code + 2-digit year",
        "dtype": "string", "format": "string", "unit": None, "precision": None,
        "nullable": False, "allowed_values": None,
        "example_canonical": "ROOTX24", "example_display": "ROOTX24",
    },
    {
        "name": "option_symbol", "domain_label": "Option Contract",
        "description": "Stable option contract label for downstream use.",
        "source_field": "option_root + delivery_month + option_type + strike",
        "source_transform": "derived contract identity",
        "dtype": "string", "format": "string", "unit": None, "precision": None,
        "nullable": False, "allowed_values": None,
        "example_canonical": "OPTX24C70", "example_display": "OPT Nov24 70 Call",
    },
    {
        "name": "contract_month", "domain_label": "Contract Month",
        "description": "Underlying contract month, normalized from raw STRIP date.",
        "source_field": "delivery_month (raw STRIP)",
        "source_transform": "ISO 8601 YYYY-MM-01; display may be YYYY-MM",
        "dtype": "string", "format": "date", "unit": None, "precision": None,
        "nullable": False, "allowed_values": None,
        "example_canonical": "2024-11-01", "example_display": "Nov 2024",
    },
    {
        "name": "expiration_date", "domain_label": "Expiration Date",
        "description": "Option expiration date.",
        "source_field": "expiry (raw EXPIRATION DATE)",
        "source_transform": "ISO 8601 date",
        "dtype": "string", "format": "date", "unit": None, "precision": None,
        "nullable": False, "allowed_values": None,
        "example_canonical": "2024-10-17", "example_display": "2024-10-17",
    },
    {
        "name": "option_type", "domain_label": "Option Type",
        "description": "Call or put.",
        "source_field": "right (raw CONTRACT TYPE)",
        "source_transform": "C -> call, P -> put",
        "dtype": "string", "format": "enum", "unit": None, "precision": None,
        "nullable": False, "allowed_values": ["call", "put"],
        "example_canonical": "call", "example_display": "Call",
    },
    {
        "name": "strike_price", "domain_label": "Strike Price",
        "description": "Option strike.",
        "source_field": "strike (raw STRIKE)",
        "source_transform": f"round to {PRICE_DP} decimals",
        "dtype": "number", "format": "decimal", "unit": "price_unit",
        "precision": PRICE_DP, "nullable": False, "allowed_values": None,
        "example_canonical": "70.00", "example_display": "70.00 price units",
    },
    {
        "name": "option_settlement_price", "domain_label": "Option Settlement Price",
        "description": "Option settlement premium.",
        "source_field": "option_price / price on option rows (raw SETTLEMENT PRICE)",
        "source_transform": f"round to {PRICE_DP} decimals",
        "dtype": "number", "format": "decimal", "unit": "price_unit",
        "precision": PRICE_DP, "nullable": False, "allowed_values": None,
        "example_canonical": "2.10", "example_display": "2.10 price units",
    },
    {
        "name": "underlying_settlement_price", "domain_label": "Underlying Settlement Price",
        "description": "Underlying futures settlement used for Greeks.",
        "source_field": "matched futures settlement (underlying_price)",
        "source_transform": f"round to {PRICE_DP} decimals",
        "dtype": "number", "format": "decimal", "unit": "price_unit",
        "precision": PRICE_DP, "nullable": False, "allowed_values": None,
        "example_canonical": "70.00", "example_display": "70.00 price units",
    },
    {
        "name": "implied_volatility", "domain_label": "Implied Volatility",
        "description": "Implied volatility in the selected model's recorded volatility unit.",
        "source_field": "iv (raw OPTION_VOLATILITY via unit registry)",
        "source_transform": f"model-specific canonical unit; round to {IV_DP} decimals",
        "dtype": "number", "format": "decimal", "unit": "model_volatility_unit",
        "precision": IV_DP, "nullable": False, "allowed_values": None,
        "example_canonical": "0.300000", "example_display": "30.00%",
    },
    {
        "name": "delta", "domain_label": "Delta",
        "description": "Delta under the recorded pricing model.", "source_field": "core.greeks.batch_greeks",
        "source_transform": f"selected model; round to {GREEK_DP} decimals",
        "dtype": "number", "format": "decimal", "unit": None, "precision": GREEK_DP,
        "nullable": False, "allowed_values": None,
        "example_canonical": "0.52341098", "example_display": "0.5234",
    },
    {
        "name": "gamma", "domain_label": "Gamma",
        "description": "Gamma under the recorded pricing model.", "source_field": "core.greeks.batch_greeks",
        "source_transform": f"selected model; round to {GREEK_DP} decimals",
        "dtype": "number", "format": "decimal", "unit": None, "precision": GREEK_DP,
        "nullable": False, "allowed_values": None,
        "example_canonical": "0.04210000", "example_display": "0.0421",
    },
    {
        "name": "vega", "domain_label": "Vega",
        "description": "Vega under the recorded pricing model.", "source_field": "core.greeks.batch_greeks",
        "source_transform": f"selected model; round to {GREEK_DP} decimals",
        "dtype": "number", "format": "decimal", "unit": None, "precision": GREEK_DP,
        "nullable": False, "allowed_values": None,
        "example_canonical": "0.08123456", "example_display": "0.0812",
    },
    {
        "name": "theta", "domain_label": "Theta",
        "description": "Theta under the recorded pricing model.", "source_field": "core.greeks.batch_greeks",
        "source_transform": f"selected model; round to {GREEK_DP} decimals",
        "dtype": "number", "format": "decimal", "unit": None, "precision": GREEK_DP,
        "nullable": False, "allowed_values": None,
        "example_canonical": "-0.01987654", "example_display": "-0.0199",
    },
    {
        "name": "rho", "domain_label": "Rho",
        "description": "Rho under the recorded pricing model.", "source_field": "core.greeks.batch_greeks",
        "source_transform": f"selected model; round to {GREEK_DP} decimals",
        "dtype": "number", "format": "decimal", "unit": None, "precision": GREEK_DP,
        "nullable": False, "allowed_values": None,
        "example_canonical": "0.03456789", "example_display": "0.0346",
    },
    {
        "name": "dte_days", "domain_label": "Days To Expiration",
        "description": "Days to expiration on the configured DTE basis.",
        "source_field": "expiration_date - trade_date",
        "source_transform": "configured DTE basis; integer days",
        "dtype": "integer", "format": "integer", "unit": "days", "precision": 0,
        "nullable": False, "allowed_values": None,
        "example_canonical": "22", "example_display": "22 days",
    },
    {
        "name": "pricing_model", "domain_label": "Pricing Model",
        "description": "Pricing model used for Greeks.",
        "source_field": "product policy",
        "source_transform": "configured product pricing model",
        "dtype": "string", "format": "enum", "unit": None, "precision": None,
        "nullable": False, "allowed_values": list(_pricing_models.implemented_greek_model_names()),
        "example_canonical": "black76", "example_display": "Black-76",
    },
    {
        "name": "greek_method", "domain_label": "Greek Method",
        "description": "Closed-form or numerical-bump method used for Greeks.",
        "source_field": "pricing model registry",
        "source_transform": "registry default Greek method",
        "dtype": "string", "format": "enum", "unit": None, "precision": None,
        "nullable": False, "allowed_values": ["closed_form", "numerical_bump"],
        "example_canonical": "numerical_bump", "example_display": "Numerical bump",
    },
    {
        "name": "volatility_unit", "domain_label": "Volatility Unit",
        "description": "Unit convention consumed by the selected pricing model.",
        "source_field": "pricing model registry",
        "source_transform": "registry volatility-unit metadata",
        "dtype": "string", "format": "enum", "unit": None, "precision": None,
        "nullable": False,
        "allowed_values": ["fraction_per_sqrt_year", "absolute_price_per_sqrt_year"],
        "example_canonical": "fraction_per_sqrt_year", "example_display": "Fraction / sqrt(year)",
    },
    {
        "name": "pricing_shift", "domain_label": "Pricing Shift",
        "description": "Explicit displacement used by shifted-lognormal models.",
        "source_field": "pricing.shifted_black.shift",
        "source_transform": "configured scalar; blank for non-shifted models",
        "dtype": "number", "format": "decimal", "unit": "price_unit", "precision": IV_DP,
        "nullable": True, "allowed_values": None,
        "example_canonical": "50.000000", "example_display": "50 price units",
    },
    {
        "name": "product_family", "domain_label": "Product Family",
        "description": "Resolved product family from product identity.",
        "source_field": "product_family",
        "source_transform": "row-level product identity",
        "dtype": "string", "format": "enum", "unit": None, "precision": None,
        "nullable": True, "allowed_values": ["futures_options", "equity_options"],
        "example_canonical": "futures_options", "example_display": "Futures options",
    },
    {
        "name": "option_underlying_type", "domain_label": "Option Underlying Type",
        "description": "Whether the option is on a future or spot underlying.",
        "source_field": "option_underlying_type",
        "source_transform": "row-level product identity",
        "dtype": "string", "format": "enum", "unit": None, "precision": None,
        "nullable": True, "allowed_values": ["future", "spot"],
        "example_canonical": "future", "example_display": "Future",
    },
    {
        "name": "exercise_style", "domain_label": "Exercise Style",
        "description": "Contract exercise style resolved from product identity.",
        "source_field": "exercise_style / contract_exercise_style",
        "source_transform": "row-level product identity",
        "dtype": "string", "format": "enum", "unit": None, "precision": None,
        "nullable": True, "allowed_values": ["american", "european"],
        "example_canonical": "american", "example_display": "American",
    },
    {
        "name": "pricing_model_target", "domain_label": "Pricing Model Target",
        "description": "Policy target model before any diagnostic fallback.",
        "source_field": "pricing_model_target",
        "source_transform": "pricing model policy",
        "dtype": "string", "format": "enum", "unit": None, "precision": None,
        "nullable": True, "allowed_values": list(_pricing_models.supported_model_names()),
        "example_canonical": "black76_baw", "example_display": "Black-76 BAW",
    },
    {
        "name": "pricing_model_source", "domain_label": "Pricing Model Source",
        "description": "How the canonical model was selected.",
        "source_field": "pricing_model_source",
        "source_transform": "pricing model policy",
        "dtype": "string", "format": "enum", "unit": None, "precision": None,
        "nullable": True, "allowed_values": ["policy_default", "explicit", "temporary_fallback", "configured"],
        "example_canonical": "policy_default", "example_display": "Policy default",
    },
    {
        "name": "pricing_model_contract_match", "domain_label": "Pricing Model Contract Match",
        "description": "Whether the selected model matches the resolved contract terms.",
        "source_field": "pricing_model_contract_match",
        "source_transform": "boolean serialized as true/false",
        "dtype": "string", "format": "boolean_string", "unit": None, "precision": None,
        "nullable": False, "allowed_values": ["true", "false"],
        "example_canonical": "true", "example_display": "true",
    },
    {
        "name": "pricing_model_contract_reason", "domain_label": "Pricing Model Contract Reason",
        "description": "Reason for model/contract match or mismatch.",
        "source_field": "pricing_model_contract_reason",
        "source_transform": "pricing model policy reason code",
        "dtype": "string", "format": "string", "unit": None, "precision": None,
        "nullable": True, "allowed_values": None,
        "example_canonical": "policy_default_contract_match",
        "example_display": "policy_default_contract_match",
    },
    {
        "name": "is_model_approximation", "domain_label": "Model Approximation",
        "description": "True when a diagnostic fallback is used instead of the target contract model.",
        "source_field": "is_model_approximation",
        "source_transform": "boolean serialized as true/false",
        "dtype": "string", "format": "boolean_string", "unit": None, "precision": None,
        "nullable": False, "allowed_values": ["true", "false"],
        "example_canonical": "false", "example_display": "false",
    },
]

EXPORT_COLUMNS = [c["name"] for c in COLUMN_SPEC]
_REQUIRED_EXPORT_POLICY = (
    "product",
    "underlying_root",
    "option_root",
    "exchange",
    "currency",
    "price_unit",
    "contract_unit",
    "price_tick",
    "exchange_calendar",
    "timezone",
)

# Columns that must NEVER reach the downstream CSV (review/debug/raw-vendor).
FORBIDDEN_COLUMNS = {
    "run_health", "quarantine", "quarantine_reason", "held_back",
    "excluded_from_study", "_bound_flag", "_missing_flag", "_pcp_flag", "iv_flag",
    "_iv_quality_flag", "_delta_quality_flag", "_premium_quality_flag",
    "_underlying_map_flag", "provider", "product_id", "iv_provided", "iv_provided_raw",
}


def _fmt(value, dp: Optional[int]) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)) or pd.isna(value):
        return ""
    if dp is None:
        return str(value)
    return f"{float(value):.{dp}f}"


def _option_mask(df: pd.DataFrame) -> pd.Series:
    it = df.get("instrument_type")
    if it is None:
        return pd.Series(False, index=df.index)
    return it.astype("string").str.lower().eq("option").fillna(False)


def _clean_release_mask(df: pd.DataFrame, model: str = "black76") -> pd.Series:
    """Row-level release gate: clean, fully-priced option rows with an underlying."""
    mask = _option_mask(df)

    iv = pd.to_numeric(df.get("iv"), errors="coerce") if "iv" in df.columns else pd.Series(np.nan, index=df.index)
    mask &= iv.notna() & (iv > 0)

    underlying = _underlying_series(df)
    mask &= underlying.notna()
    if not _pricing_models.get_model_spec(model).supports_negative_underlying:
        mask &= underlying > 0

    if "T" in df.columns:
        mask &= pd.to_numeric(df["T"], errors="coerce").fillna(0) > 0

    # Drop rows carrying a genuine quality/quarantine flag. IV provider/model
    # disagreement (iv_flag / _iv_quality_flag) is deliberately NOT an exclusion
    # under exchange-authoritative IV (issue 025): a deep ITM/OTM price-inversion
    # mismatch is an artifact, not bad exchange data. It only moves run readiness via
    # the near-money aggregate. Genuine corruption (premium below intrinsic, bad
    # delta sign, PCP break, missing underlying, quarantine) still excludes.
    for flag in ("_delta_quality_flag", "_premium_quality_flag",
                 "_pcp_flag", "_underlying_map_flag", "quarantine", "held_back"):
        if flag in df.columns:
            mask &= ~df[flag].fillna(False).astype(bool)
    if "pricing_domain_valid" in df.columns:
        mask &= df["pricing_domain_valid"].fillna(False).astype(bool)
    return mask


def _underlying_series(df: pd.DataFrame) -> pd.Series:
    out = pd.Series(np.nan, index=df.index, dtype=float)
    for col in ("underlying_price", "F", "S"):
        if col in df.columns:
            vals = pd.to_numeric(df[col], errors="coerce")
            out = out.where(out.notna(), vals)
    return out


def _option_price_series(df: pd.DataFrame) -> pd.Series:
    if "option_price" in df.columns:
        primary = pd.to_numeric(df["option_price"], errors="coerce")
    else:
        primary = pd.Series(np.nan, index=df.index, dtype=float)
    if "price" in df.columns:
        primary = primary.where(primary.notna(), pd.to_numeric(df["price"], errors="coerce"))
    return primary


def _text_value(row: pd.Series, col: str, default: str = "") -> str:
    value = row.get(col, default)
    if value is None or pd.isna(value):
        return default
    return str(value)


def _bool_text(value, default: bool = False) -> str:
    if value is None or pd.isna(value):
        return "true" if default else "false"
    if isinstance(value, bool):
        return "true" if value else "false"
    return "true" if str(value).strip().lower() in {"1", "true", "t", "yes", "y"} else "false"


def _root_series(rows: pd.DataFrame, cfg: dict, exp: dict) -> tuple[pd.Series, pd.Series, dict]:
    """Resolve per-row underlying/option roots and provenance."""
    export_policy = (cfg or {}).get("export") or {}
    root_source = str(
        export_policy.get("root_source")
        or export_policy.get("symbol_root_source")
        or "row_identity"
    ).strip().lower()

    option_root = pd.Series(str(exp["option_root"]), index=rows.index, dtype="object")
    underlying_root = pd.Series(str(exp["underlying_root"]), index=rows.index, dtype="object")
    option_source = "config_fallback"
    underlying_source = "config_fallback"

    if root_source in {"cme", "cme_equivalent", "equivalent_cme"}:
        if "equivalent_option_root_cme" in rows.columns:
            equiv = rows["equivalent_option_root_cme"].astype("string")
            use = equiv.notna() & equiv.str.len().gt(0)
            option_root.loc[use] = equiv.loc[use].astype(object)
            if use.any():
                option_source = "equivalent_option_root_cme"
        # CME-style underlying roots are venue conventions; configs may provide
        # them even when source rows use ICE-native roots such as T.
        underlying_source = "config_fallback"
    else:
        if "source_option_root" in rows.columns:
            src = rows["source_option_root"].astype("string")
            use = src.notna() & src.str.len().gt(0)
            option_root.loc[use] = src.loc[use].astype(object)
            if use.any():
                option_source = "source_option_root"
        if "underlying_root" in rows.columns:
            und = rows["underlying_root"].astype("string")
            use = und.notna() & und.str.len().gt(0)
            underlying_root.loc[use] = und.loc[use].astype(object)
            if use.any():
                underlying_source = "underlying_root"

    return underlying_root, option_root, {
        "root_source_policy": root_source,
        "underlying_root_source": underlying_source,
        "option_root_source": option_source,
        "underlying_roots": sorted(str(v) for v in underlying_root.dropna().unique()),
        "option_roots": sorted(str(v) for v in option_root.dropna().unique()),
    }


def _symbol_parts(delivery_month: pd.Timestamp):
    mc = _MONTH_CODE.get(int(delivery_month.month), "?")
    yy = f"{int(delivery_month.year) % 100:02d}"
    return mc, yy


def _normalize_local_time(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    if len(text.split(":")) == 2:
        return f"{text}:00"
    return text


def export_config(cfg: dict) -> dict:
    """Resolve the export/product policy block from config only.

    Core export code must not provide instrument-specific defaults. If a market
    convention is required for symbols, units, or calendars, it belongs in the
    instrument YAML or merged runtime config.
    """
    export = dict((cfg or {}).get("export") or {})
    symbol = (cfg or {}).get("symbol") or {}
    export.setdefault("product", symbol.get("hub") or cfg.get("product") or "UNKNOWN")
    export.setdefault("underlying_root", symbol.get("contract_root"))
    export.setdefault("option_root", export["underlying_root"])
    export.setdefault("exchange", cfg.get("exchange"))
    export.setdefault("currency", cfg.get("currency"))
    export.setdefault("price_unit", cfg.get("price_unit"))
    export.setdefault("contract_unit", cfg.get("contract_unit"))
    export.setdefault("price_tick", cfg.get("price_tick"))
    export.setdefault("exchange_calendar", cfg.get("exchange_calendar") or export.get("exchange"))
    export.setdefault("timezone", cfg.get("exchange_tz") or cfg.get("timezone"))
    missing = [key for key in _REQUIRED_EXPORT_POLICY if export.get(key) in (None, "")]
    if missing:
        raise ValueError(
            "Downstream option-chain export requires instrument policy in config: "
            + ", ".join(missing)
        )
    return export


def _settlement_timing_policy(cfg: dict, exp: dict) -> dict:
    timing = dict((cfg or {}).get("settlement_timing") or exp.get("settlement_timing") or {})
    local_time = _normalize_local_time(
        timing.get("local_time") or timing.get("release_time") or cfg.get("settlement_release_time")
    )
    timezone = timing.get("timezone") or cfg.get("exchange_tz") or exp.get("timezone")
    if not local_time or not timezone:
        return {}
    out = {
        "time_kind": timing.get("time_kind", "settlement_period_end"),
        "local_time": local_time,
        "timezone": timezone,
        "same_day_file_availability_assumption": timing.get(
            "same_day_file_availability_assumption", "not_assumed"
        ),
    }
    if timing.get("source_reference"):
        out["source_reference"] = timing["source_reference"]
    return out


def _resolve_column_spec(cfg: Optional[dict] = None) -> list[dict]:
    spec = [dict(c) for c in COLUMN_SPEC]
    if cfg is None:
        return spec
    exp = export_config(cfg)
    model = cfg.get("pricing_model", (cfg.get("pricing") or {}).get("model", "black76"))
    volatility_unit = _pricing_models.get_model_spec(model).volatility_unit
    schema_volatility_unit = (
        "decimal_fraction"
        if volatility_unit == "fraction_per_sqrt_year"
        else volatility_unit
    )
    for col in spec:
        if col.get("unit") == "price_unit":
            col["unit"] = exp["price_unit"]
        elif col.get("unit") == "model_volatility_unit":
            col["unit"] = schema_volatility_unit
    return spec


def build_option_chain_greeks(df: pd.DataFrame, cfg: dict, readiness: Optional[dict] = None) -> dict:
    """Build the clean downstream export frame (string-formatted) + stats.

    Returns a dict with ``frame`` (the export DataFrame), ``n_input_option_rows``,
    ``n_exported``, ``n_excluded``, and ``pricing_model``.
    """
    exp = export_config(cfg)
    model = cfg.get("pricing_model", (cfg.get("pricing") or {}).get("model", "black76"))
    model_spec = _pricing_models.get_model_spec(model)
    model_params = _pricing_models.runtime_model_params(cfg)

    opt_mask = _option_mask(df)
    n_option_rows = int(opt_mask.sum())
    clean = _clean_release_mask(df, model)
    rows = df.loc[clean].copy()

    for column, key in (
        ("exercise_style", "tree_exercise_style"),
        ("contract_exercise_style", "tree_exercise_style"),
        ("option_underlying_type", "tree_underlying_type"),
    ):
        if column in rows.columns:
            values = rows[column].dropna().astype(str).str.lower().unique()
            if len(values) == 1:
                model_params[key] = values[0]

    if rows.empty:
        frame = pd.DataFrame(columns=EXPORT_COLUMNS)
        return {"frame": frame, "n_input_option_rows": n_option_rows,
                "n_exported": 0, "n_excluded": n_option_rows, "pricing_model": model,
                "root_provenance": {"root_source_policy": "none"}}

    # Greeks: always recompute from the single source of truth so the export is
    # complete regardless of the run's compute_greeks flag.
    underlying = _underlying_series(rows)
    strike = pd.to_numeric(rows["strike"], errors="coerce")
    T = pd.to_numeric(rows["T"], errors="coerce")
    r = (
        pd.to_numeric(rows["r"], errors="coerce")
        if "r" in rows.columns
        else pd.Series(np.nan, index=rows.index, dtype=float)
    )
    iv = pd.to_numeric(rows["iv"], errors="coerce")
    right = rows["right"].astype("string").str.upper()
    g = _greeks.batch_greeks(
        model=model, S_or_F=underlying.to_numpy(), K=strike.to_numpy(),
        T=T.to_numpy(), r=r.to_numpy(), sigma=iv.to_numpy(),
        right=right.to_numpy(), q=cfg.get("div_yield", 0.0),
        shift=model_params.get("shift"), model_params=model_params,
    )

    greek_valid = np.logical_and.reduce([
        np.isfinite(g[name]) for name in ("delta", "gamma", "vega", "theta", "rho")
    ])
    if not greek_valid.all():
        keep = np.flatnonzero(greek_valid)
        rows = rows.iloc[keep].copy()
        underlying = underlying.iloc[keep]
        strike = strike.iloc[keep]
        T = T.iloc[keep]
        r = r.iloc[keep]
        iv = iv.iloc[keep]
        right = right.iloc[keep]
        g = {name: values[keep] for name, values in g.items()}
    if rows.empty:
        frame = pd.DataFrame(columns=EXPORT_COLUMNS)
        return {
            "frame": frame,
            "n_input_option_rows": n_option_rows,
            "n_exported": 0,
            "n_excluded": n_option_rows,
            "pricing_model": model,
            "root_provenance": {"root_source_policy": "none"},
        }

    delivery = pd.to_datetime(rows["delivery_month"])
    expiry = pd.to_datetime(rows["expiry"])
    trade_date = pd.to_datetime(rows["as_of_date"])
    opt_price = _option_price_series(rows)
    if "dte_days" in rows.columns:
        dte = pd.to_numeric(rows["dte_days"], errors="coerce")
    else:
        dte = pd.Series(np.nan, index=rows.index, dtype=float)
    if dte.isna().all():
        dte = (expiry - trade_date).dt.days
    underlying_roots, option_roots, root_provenance = _root_series(rows, cfg, exp)

    out = {c: [] for c in EXPORT_COLUMNS}
    for i, (_, row) in enumerate(rows.iterrows()):
        mc, yy = _symbol_parts(delivery.iloc[i])
        rt = "call" if str(right.iloc[i]) == "C" else "put"
        k = float(strike.iloc[i])
        k_label = f"{int(k)}" if float(k).is_integer() else f"{k:g}"
        out["trade_date"].append(trade_date.iloc[i].strftime("%Y-%m-%d"))
        out["product"].append(str(exp["product"]))
        out["underlying_symbol"].append(f"{underlying_roots.iloc[i]}{mc}{yy}")
        out["option_symbol"].append(f"{option_roots.iloc[i]}{mc}{yy}{right.iloc[i]}{k_label}")
        out["contract_month"].append(delivery.iloc[i].strftime("%Y-%m-01"))
        out["expiration_date"].append(expiry.iloc[i].strftime("%Y-%m-%d"))
        out["option_type"].append(rt)
        out["strike_price"].append(_fmt(k, PRICE_DP))
        out["option_settlement_price"].append(_fmt(opt_price.iloc[i], PRICE_DP))
        out["underlying_settlement_price"].append(_fmt(underlying.iloc[i], PRICE_DP))
        out["implied_volatility"].append(_fmt(iv.iloc[i], IV_DP))
        out["delta"].append(_fmt(g["delta"][i], GREEK_DP))
        out["gamma"].append(_fmt(g["gamma"][i], GREEK_DP))
        out["vega"].append(_fmt(g["vega"][i], GREEK_DP))
        out["theta"].append(_fmt(g["theta"][i], GREEK_DP))
        out["rho"].append(_fmt(g["rho"][i], GREEK_DP))
        out["dte_days"].append("" if pd.isna(dte.iloc[i]) else str(int(dte.iloc[i])))
        out["pricing_model"].append(str(model))
        out["greek_method"].append(model_spec.default_greek_method)
        out["volatility_unit"].append(model_spec.volatility_unit)
        out["pricing_shift"].append(
            "" if model_params.get("shift") is None else _fmt(model_params["shift"], IV_DP)
        )
        out["product_family"].append(_text_value(row, "product_family"))
        out["option_underlying_type"].append(_text_value(row, "option_underlying_type"))
        out["exercise_style"].append(
            _text_value(row, "exercise_style", _text_value(row, "contract_exercise_style"))
        )
        out["pricing_model_target"].append(_text_value(row, "pricing_model_target", str(model)))
        out["pricing_model_source"].append(_text_value(row, "pricing_model_source", "configured"))
        out["pricing_model_contract_match"].append(
            _bool_text(row.get("pricing_model_contract_match"), default=True)
        )
        out["pricing_model_contract_reason"].append(
            _text_value(row, "pricing_model_contract_reason", "not_checked")
        )
        out["is_model_approximation"].append(
            _bool_text(row.get("is_model_approximation"), default=False)
        )

    frame = pd.DataFrame(out, columns=EXPORT_COLUMNS)
    return {"frame": frame, "n_input_option_rows": n_option_rows,
            "n_exported": int(len(frame)), "n_excluded": n_option_rows - int(len(frame)),
            "pricing_model": model, "root_provenance": root_provenance}


def build_export_manifest(
    cfg: dict,
    readiness: Optional[dict] = None,
    *,
    root_provenance: Optional[dict] = None,
) -> dict:
    """Build the downstream manifest carrying per-dataset policy (issue 023)."""
    exp = export_config(cfg)
    gate = (readiness or {}).get("status", "not_checked")
    model = cfg.get("pricing_model", (cfg.get("pricing") or {}).get("model", "black76"))
    model_spec = _pricing_models.get_model_spec(model)
    pricing_resolution = ((cfg.get("option_quality") or {}).get("pricing_model_resolution") or {})
    manifest = {
        "product": exp["product"],
        "exchange": exp["exchange"],
        "underlying_root": exp["underlying_root"],
        "option_root": exp["option_root"],
        "root_provenance": root_provenance or {
            "root_source_policy": "config_fallback",
            "underlying_root_source": "config_fallback",
            "option_root_source": "config_fallback",
        },
        "currency": exp["currency"],
        "price_unit": exp["price_unit"],
        "contract_unit": exp["contract_unit"],
        "price_tick": exp["price_tick"],
        "pricing_model": model,
        "pricing_model_target": pricing_resolution.get("pricing_model_target", model),
        "pricing_model_source": pricing_resolution.get("pricing_model_source", "configured"),
        "pricing_model_runtime_status": pricing_resolution.get(
            "pricing_model_runtime_status", "implemented"
        ),
        "pricing_model_contract_match": pricing_resolution.get(
            "pricing_model_contract_match", True
        ),
        "pricing_model_contract_reason": pricing_resolution.get(
            "pricing_model_contract_reason", "not_checked"
        ),
        "is_model_approximation": pricing_resolution.get("is_model_approximation", False),
        "contract_exercise_style": pricing_resolution.get("contract_exercise_style"),
        "selected_model_exercise_style": pricing_resolution.get(
            "selected_model_exercise_style", model_spec.exercise_style
        ),
        "pricing_model_family": model_spec.family,
        "pricing_exercise_style": model_spec.exercise_style,
        "pricing_price_dynamics": model_spec.price_dynamics,
        "pricing_approximation": model_spec.approximation,
        "pricing_parity_check_mode": model_spec.parity_check_mode,
        "greek_method": model_spec.default_greek_method,
        "volatility_unit": model_spec.volatility_unit,
        "pricing_shift": _pricing_models.runtime_model_params(cfg).get("shift"),
        "max_recommended_tenor_years": model_spec.max_recommended_tenor_years,
        "model_runtime_params": _pricing_models.runtime_model_params(cfg),
        "data_frequency": "daily",
        "date_format": "ISO_8601_YYYY_MM_DD",
        "contract_month_format": "ISO_8601_YYYY_MM_01",
        "contract_month_source_field": "STRIP",
        "contract_month_display": "YYYY-MM",
        "trade_date_meaning": "market_session_date",
        "availability_policy": "available_next_trading_session_after_settlement",
        "tradable_time_policy": "next_trading_session_after_trade_date",
        "exchange_calendar": exp["exchange_calendar"],
        "timezone": exp["timezone"],
        "iv_unit": (
            "decimal"
            if model_spec.volatility_unit == "fraction_per_sqrt_year"
            else model_spec.volatility_unit
        ),
        "iv_decimal_places": IV_DP,
        "price_decimal_places": PRICE_DP,
        "greek_decimal_places": GREEK_DP,
        "quality_gate": gate,
    }
    timing = _settlement_timing_policy(cfg, exp)
    if timing:
        manifest["settlement_timing"] = timing
    rate_summary = cfg.get("rate_summary") or ((cfg.get("option_quality") or {}).get("rate_summary"))
    if rate_summary:
        manifest["rate_summary"] = rate_summary
    return manifest


def build_export_schema(cfg: Optional[dict] = None) -> dict:
    """Machine-readable schema covering every exported column (issue 024)."""
    spec = _resolve_column_spec(cfg) if cfg else COLUMN_SPEC
    return {
        "name": "option_chain_greeks",
        "primary_key": ["trade_date", "option_symbol"],
        "columns": [
            {
                "name": c["name"], "dtype": c["dtype"], "format": c["format"],
                "unit": c["unit"], "precision": c["precision"],
                "nullable": c["nullable"], "allowed_values": c["allowed_values"],
            }
            for c in spec
        ],
    }


def build_data_dictionary(cfg: Optional[dict] = None) -> str:
    """Human-readable data dictionary covering every exported column (issue 024)."""
    exp = export_config(cfg) if cfg else {}
    manifest = build_export_manifest(cfg, {"status": "not_checked"}) if cfg else {}
    timing = manifest.get("settlement_timing") or {}
    price_unit = exp.get("price_unit", "configured price unit")
    lines = [
        "# Option Chain Greeks — Data Dictionary",
        "",
        "Downstream-ready daily option chain with full Black-76 Greeks. Canonical CSV "
        "values are for machines; display values are for humans.",
        "",
        "## Timing and Availability",
        "",
        "`trade_date` is the **market session date** described by the data. It is **not** "
        "the time a downstream consumer may act on the row. Tradable consumer time is "
        "derived by the importer from manifest policy:",
        "",
        "```text",
        "tradable_time = next_trading_session_after_trade_date(trade_date, exchange_calendar)",
        "```",
        "",
        "`available_at`, `decision_time`, and `tradable_time` are not row-level columns "
        "in this date-only downstream CSV. They belong in the manifest/importer policy "
        "and review artifacts.",
        "",
        f"- **Availability policy:** {manifest.get('availability_policy', 'configured in manifest')}",
        f"- **Tradable-time policy:** {manifest.get('tradable_time_policy', 'configured in manifest')}",
        f"- **Exchange calendar:** {manifest.get('exchange_calendar', 'configured in manifest')}",
        f"- **Timezone:** {manifest.get('timezone', 'configured in manifest')}",
    ]
    if timing:
        lines.extend([
            f"- **Settlement time kind:** {timing.get('time_kind')}",
            f"- **Settlement local time:** {timing.get('local_time')} {timing.get('timezone')}",
            "- **Same-day file availability assumption:** "
            f"{timing.get('same_day_file_availability_assumption')}",
        ])
        if timing.get("source_reference"):
            lines.append(f"- **Timing source:** {timing['source_reference']}")
    lines.extend([
        "",
        "## Display rules",
        "",
        "| Concept | Raw | Canonical CSV | Domain display |",
        "| --- | --- | --- | --- |",
        "| Contract month | `11/1/2024` | `2024-11-01` | `Nov 2024` |",
        "| Option type | `C` | `call` | `Call` |",
        "| IV | `30.0` percent | `0.300000` | `30.00%` |",
        f"| Price | `34.69000` | `34.69` | `34.69 {price_unit}` |",
        "",
        "## Columns",
        "",
    ])
    for c in _resolve_column_spec(cfg) if cfg else COLUMN_SPEC:
        lines.append(f"### `{c['name']}` — {c['domain_label']}")
        lines.append("")
        lines.append(f"- **Description:** {c['description']}")
        lines.append(f"- **Source field:** {c['source_field']}")
        lines.append(f"- **Source transform:** {c['source_transform']}")
        lines.append(f"- **Type / format:** {c['dtype']} / {c['format']}")
        if c["unit"]:
            lines.append(f"- **Unit:** {c['unit']}")
        if c["precision"] is not None:
            lines.append(f"- **Precision:** {c['precision']} decimals")
        lines.append(f"- **Nullable:** {c['nullable']}")
        if c["allowed_values"]:
            lines.append(f"- **Allowed values:** {', '.join(c['allowed_values'])}")
        lines.append(f"- **Example (canonical):** `{c['example_canonical']}`")
        lines.append(f"- **Example (display):** {c['example_display']}")
        lines.append("")
    return "\n".join(lines)


def write_option_chain_export(
    df: pd.DataFrame, cfg: dict, readiness: Optional[dict], run_dir) -> dict:
    """Write the downstream export bundle under ``run_dir/exports/option_chain_greeks/``.

    Run-level gate: a ``blocked`` readiness withholds the export entirely. Returns a
    dict of artifact paths (or a ``status="blocked"`` record) for summary.json.
    """
    out_dir = Path(run_dir) / "exports" / "option_chain_greeks"
    gate = (readiness or {}).get("status", "not_checked")
    if gate == "blocked":
        return {
            "status": "blocked",
            "reason": "option-market readiness is blocked; downstream export withheld",
            "readiness_reasons": (readiness or {}).get("reasons", []),
        }

    built = build_option_chain_greeks(df, cfg, readiness)
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / "option_chain_greeks.csv"
    built["frame"].to_csv(csv_path, index=False)

    manifest = build_export_manifest(
        cfg,
        readiness,
        root_provenance=built.get("root_provenance"),
    )
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    schema_path = out_dir / "schema.json"
    schema_path.write_text(json.dumps(build_export_schema(cfg), indent=2), encoding="utf-8")

    dict_path = out_dir / "data_dictionary.md"
    dict_path.write_text(build_data_dictionary(cfg), encoding="utf-8")

    return {
        "status": gate,
        "option_chain_greeks_csv": str(csv_path),
        "option_chain_greeks_manifest": str(manifest_path),
        "option_chain_greeks_schema": str(schema_path),
        "option_chain_greeks_data_dictionary": str(dict_path),
        "n_exported": built["n_exported"],
        "n_excluded": built["n_excluded"],
    }
