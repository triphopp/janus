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
        "description": "Canonical decimal implied volatility.",
        "source_field": "iv (raw OPTION_VOLATILITY via unit registry)",
        "source_transform": f"canonical decimal unit; round to {IV_DP} decimals",
        "dtype": "number", "format": "decimal", "unit": "decimal_fraction",
        "precision": IV_DP, "nullable": False, "allowed_values": None,
        "example_canonical": "0.300000", "example_display": "30.00%",
    },
    {
        "name": "delta", "domain_label": "Delta",
        "description": "Black-76 delta.", "source_field": "core.greeks.batch_greeks",
        "source_transform": f"Black-76; round to {GREEK_DP} decimals",
        "dtype": "number", "format": "decimal", "unit": None, "precision": GREEK_DP,
        "nullable": False, "allowed_values": None,
        "example_canonical": "0.52341098", "example_display": "0.5234",
    },
    {
        "name": "gamma", "domain_label": "Gamma",
        "description": "Black-76 gamma.", "source_field": "core.greeks.batch_greeks",
        "source_transform": f"Black-76; round to {GREEK_DP} decimals",
        "dtype": "number", "format": "decimal", "unit": None, "precision": GREEK_DP,
        "nullable": False, "allowed_values": None,
        "example_canonical": "0.04210000", "example_display": "0.0421",
    },
    {
        "name": "vega", "domain_label": "Vega",
        "description": "Black-76 vega.", "source_field": "core.greeks.batch_greeks",
        "source_transform": f"Black-76; round to {GREEK_DP} decimals",
        "dtype": "number", "format": "decimal", "unit": None, "precision": GREEK_DP,
        "nullable": False, "allowed_values": None,
        "example_canonical": "0.08123456", "example_display": "0.0812",
    },
    {
        "name": "theta", "domain_label": "Theta",
        "description": "Black-76 theta.", "source_field": "core.greeks.batch_greeks",
        "source_transform": f"Black-76; round to {GREEK_DP} decimals",
        "dtype": "number", "format": "decimal", "unit": None, "precision": GREEK_DP,
        "nullable": False, "allowed_values": None,
        "example_canonical": "-0.01987654", "example_display": "-0.0199",
    },
    {
        "name": "rho", "domain_label": "Rho",
        "description": "Black-76 rho.", "source_field": "core.greeks.batch_greeks",
        "source_transform": f"Black-76; round to {GREEK_DP} decimals",
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
        "nullable": False, "allowed_values": ["black76", "bsm"],
        "example_canonical": "black76", "example_display": "Black-76",
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


def _clean_release_mask(df: pd.DataFrame) -> pd.Series:
    """Row-level release gate: clean, fully-priced option rows with an underlying."""
    mask = _option_mask(df)

    iv = pd.to_numeric(df.get("iv"), errors="coerce") if "iv" in df.columns else pd.Series(np.nan, index=df.index)
    mask &= iv.notna() & (iv > 0)

    underlying = _underlying_series(df)
    mask &= underlying.notna() & (underlying > 0)

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
    for col in spec:
        if col.get("unit") == "price_unit":
            col["unit"] = exp["price_unit"]
    return spec


def build_option_chain_greeks(df: pd.DataFrame, cfg: dict, readiness: Optional[dict] = None) -> dict:
    """Build the clean downstream export frame (string-formatted) + stats.

    Returns a dict with ``frame`` (the export DataFrame), ``n_input_option_rows``,
    ``n_exported``, ``n_excluded``, and ``pricing_model``.
    """
    exp = export_config(cfg)
    model = cfg.get("pricing_model", (cfg.get("pricing") or {}).get("model", "black76"))

    opt_mask = _option_mask(df)
    n_option_rows = int(opt_mask.sum())
    clean = _clean_release_mask(df)
    rows = df.loc[clean].copy()

    if rows.empty:
        frame = pd.DataFrame(columns=EXPORT_COLUMNS)
        return {"frame": frame, "n_input_option_rows": n_option_rows,
                "n_exported": 0, "n_excluded": n_option_rows, "pricing_model": model}

    # Greeks: always recompute from the single source of truth so the export is
    # complete regardless of the run's compute_greeks flag.
    underlying = _underlying_series(rows)
    strike = pd.to_numeric(rows["strike"], errors="coerce")
    T = pd.to_numeric(rows["T"], errors="coerce")
    r = pd.to_numeric(rows["r"], errors="coerce").fillna(cfg.get("rf_rate", 0.05)) \
        if "r" in rows.columns else pd.Series(cfg.get("rf_rate", 0.05), index=rows.index)
    iv = pd.to_numeric(rows["iv"], errors="coerce")
    right = rows["right"].astype("string").str.upper()
    g = _greeks.batch_greeks(
        model=model, S_or_F=underlying.to_numpy(), K=strike.to_numpy(),
        T=T.to_numpy(), r=r.to_numpy(), sigma=iv.to_numpy(),
        right=right.to_numpy(), q=cfg.get("div_yield", 0.0),
    )

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

    out = {c: [] for c in EXPORT_COLUMNS}
    for i, (_, row) in enumerate(rows.iterrows()):
        mc, yy = _symbol_parts(delivery.iloc[i])
        rt = "call" if str(right.iloc[i]) == "C" else "put"
        k = float(strike.iloc[i])
        k_label = f"{int(k)}" if float(k).is_integer() else f"{k:g}"
        out["trade_date"].append(trade_date.iloc[i].strftime("%Y-%m-%d"))
        out["product"].append(str(exp["product"]))
        out["underlying_symbol"].append(f"{exp['underlying_root']}{mc}{yy}")
        out["option_symbol"].append(f"{exp['option_root']}{mc}{yy}{right.iloc[i]}{k_label}")
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

    frame = pd.DataFrame(out, columns=EXPORT_COLUMNS)
    return {"frame": frame, "n_input_option_rows": n_option_rows,
            "n_exported": int(len(frame)), "n_excluded": n_option_rows - int(len(frame)),
            "pricing_model": model}


def build_export_manifest(cfg: dict, readiness: Optional[dict] = None) -> dict:
    """Build the downstream manifest carrying per-dataset policy (issue 023)."""
    exp = export_config(cfg)
    gate = (readiness or {}).get("status", "not_checked")
    manifest = {
        "product": exp["product"],
        "exchange": exp["exchange"],
        "underlying_root": exp["underlying_root"],
        "option_root": exp["option_root"],
        "currency": exp["currency"],
        "price_unit": exp["price_unit"],
        "contract_unit": exp["contract_unit"],
        "price_tick": exp["price_tick"],
        "pricing_model": cfg.get("pricing_model", (cfg.get("pricing") or {}).get("model", "black76")),
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
        "iv_unit": "decimal",
        "iv_decimal_places": IV_DP,
        "price_decimal_places": PRICE_DP,
        "greek_decimal_places": GREEK_DP,
        "quality_gate": gate,
    }
    timing = _settlement_timing_policy(cfg, exp)
    if timing:
        manifest["settlement_timing"] = timing
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
    """Write the downstream export bundle under ``run_dir/data/option_chain_greeks/``.

    Run-level gate: a ``blocked`` readiness withholds the export entirely. Returns a
    dict of artifact paths (or a ``status="blocked"`` record) for summary.json.
    """
    out_dir = Path(run_dir) / "data" / "option_chain_greeks"
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

    manifest = build_export_manifest(cfg, readiness)
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
