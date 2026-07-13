"""Option-specific quality summarizer.

Pure function — accepts a prepared DataFrame and adapter universe summary,
returns a structured quality dict suitable for inclusion in summary.json.
Missing optional columns produce None or 0, never crash.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Diagnostic columns for the IV-mismatch review artifact (dashboard drill-down).
IV_MISMATCH_REVIEW_COLUMNS = [
    "as_of_date", "contract_root", "right", "strike",
    "underlying_price", "option_price", "intrinsic", "time_value",
    "moneyness", "iv_provided", "iv_solved", "iv_diff", "dte_days", "reason",
]


def iv_mismatch_review(df: pd.DataFrame) -> pd.DataFrame:
    """Per-contract diagnostics for IV-flagged option rows (issue 003 drill-down).

    Answers *where* and *why* provider IV disagrees with our price-inverted IV, so a
    reviewer can see at a glance that most mismatches are deep ITM/OTM rows whose
    settlement price is essentially all intrinsic (no recoverable IV) rather than bad
    exchange data. Returns an empty frame (with the expected columns) when nothing is
    flagged or the inputs are absent.
    """
    if "iv_flag" not in df.columns:
        return pd.DataFrame(columns=IV_MISMATCH_REVIEW_COLUMNS)

    it = df.get("instrument_type", pd.Series(index=df.index, dtype="object"))
    opt = it.astype("string").str.lower().eq("option").fillna(False)
    flagged = df[opt & df["iv_flag"].fillna(False).astype(bool)].copy()
    if flagged.empty:
        return pd.DataFrame(columns=IV_MISMATCH_REVIEW_COLUMNS)

    underlying = pd.to_numeric(
        flagged.get("underlying_price", flagged.get("F")), errors="coerce"
    )
    strike = pd.to_numeric(flagged.get("strike"), errors="coerce")
    opt_price = pd.to_numeric(
        flagged.get("option_price", flagged.get("price")), errors="coerce"
    )
    right = flagged.get("right").astype("string").str.upper()
    intrinsic = np.where(
        right.eq("C"), (underlying - strike).clip(lower=0),
        (strike - underlying).clip(lower=0),
    )
    time_value = opt_price - intrinsic
    moneyness = underlying / strike.replace(0, np.nan)

    out = pd.DataFrame({
        "as_of_date": flagged.get("as_of_date"),
        "contract_root": flagged.get("contract_root"),
        "right": right,
        "strike": strike,
        "underlying_price": underlying.round(4),
        "option_price": opt_price.round(4),
        "intrinsic": np.round(intrinsic, 4),
        "time_value": time_value.round(4),
        "moneyness": moneyness.round(4),
        "iv_provided": pd.to_numeric(flagged.get("iv"), errors="coerce").round(6),
        "iv_solved": pd.to_numeric(flagged.get("iv_solved"), errors="coerce").round(6),
        "iv_diff": pd.to_numeric(flagged.get("iv_diff"), errors="coerce").round(6),
        "dte_days": pd.to_numeric(flagged.get("dte_days"), errors="coerce"),
    })
    # Plain-language reason so the dashboard can group the mismatch causes.
    out["reason"] = np.where(
        out["time_value"] <= 0.01, "no_time_value_deep_itm_otm",
        np.where(out["moneyness"].sub(1).abs() > 0.15,
                 "deep_itm_otm_unstable_inversion", "near_money_genuine_diff"),
    )
    return out.reset_index(drop=True)


def iv_mismatch_review_summary(review: pd.DataFrame) -> dict:
    """Aggregate the review frame by reason for summary.json / dashboard headline."""
    total = int(len(review))
    by_reason = review["reason"].value_counts().to_dict() if total else {}
    return {
        "flagged_rows": total,
        "by_reason": {k: int(v) for k, v in by_reason.items()},
    }


def summarize(
    df: pd.DataFrame,
    cfg: dict,
    adapter_summary: dict | None = None,
) -> dict:
    """Summarize option data quality for one pipeline run.

    Args:
        df: prepared DataFrame (may include both option and future rows)
        cfg: merged pipeline config (unused for now, reserved for thresholds)
        adapter_summary: the _option_quality dict emitted by the adapter prepare()

    Returns:
        Structured quality dict keyed by dimension.
    """
    it_col = df.get("instrument_type", pd.Series(dtype="object")) if hasattr(df, "get") else pd.Series(dtype="object")
    it_str = it_col.astype("string").str.lower()
    option_mask = it_str.eq("option").fillna(False)
    future_mask = it_str.eq("future").fillna(False)

    options = df[option_mask]
    n_options = len(options)
    n_futures = int(future_mask.sum())

    # ── IV ──────────────────────────────────────────────────────────────────
    iv = (
        pd.to_numeric(options["iv"], errors="coerce")
        if "iv" in options.columns
        else pd.Series(dtype=float)
    )
    iv_null_rate = float(iv.isna().sum() / n_options) if n_options > 0 else None

    iv_solved = (
        pd.to_numeric(options["iv_solved"], errors="coerce")
        if "iv_solved" in options.columns
        else None
    )
    iv_solve_fail_rate = (
        float(iv_solved.isna().sum() / n_options)
        if iv_solved is not None and n_options > 0
        else None
    )

    iv_flag = options["iv_flag"] if "iv_flag" in options.columns else None
    iv_flag_rate = (
        float(iv_flag.fillna(False).astype(bool).sum() / n_options)
        if iv_flag is not None and n_options > 0
        else None
    )

    iv_max = float(iv.max()) if n_options > 0 and iv.notna().any() else None

    # IV validation provenance (issue 025): "trusted_exchange" (default — exchange
    # settlement IV used as-is, no price-inversion) or "checked" (model self-test run).
    iv_validation = None
    if "iv_validation" in options.columns and n_options > 0:
        vals = options["iv_validation"].dropna()
        iv_validation = vals.mode().iloc[0] if not vals.empty else None

    # ── Delta ────────────────────────────────────────────────────────────────
    delta = (
        pd.to_numeric(options["delta"], errors="coerce")
        if "delta" in options.columns
        else pd.Series(dtype=float)
    )
    delta_coverage = (
        float(delta.notna().sum() / n_options) if n_options > 0 else None
    )

    bad_sign_count = 0
    if "right" in options.columns and "delta" in options.columns:
        right = options["right"].astype("string").str.upper()
        bad_call = right.eq("C") & delta.notna() & (delta < 0)
        bad_put = right.eq("P") & delta.notna() & (delta > 0)
        bad_sign_count = int((bad_call | bad_put).sum())

    # ── PCP ──────────────────────────────────────────────────────────────────
    pricing_cfg = (cfg or {}).get("pricing") or {}
    pcp_enabled = bool((cfg or {}).get("check_pcp", pricing_cfg.get("check_pcp", True)))

    def _rate(col_name: str) -> float | None:
        if col_name not in options.columns or n_options == 0:
            return None
        return float(options[col_name].fillna(False).astype(bool).sum() / n_options)

    pcp_status = "checked"
    pcp_flag_rate = _rate("_pcp_flag")
    pcp_pair_missing_rate = _rate("pcp_pair_missing")
    pcp_duplicate_pair_rate = _rate("pcp_duplicate_pair")
    if not pcp_enabled:
        pcp_status = "disabled"
        pcp_flag_rate = None
        pcp_pair_missing_rate = None
        pcp_duplicate_pair_rate = None
    elif pcp_flag_rate is None:
        pcp_status = "not_checked"

    # ── Universe (from adapter) ───────────────────────────────────────────────
    universe_out: dict = {}
    underlying_map_out: dict = {}
    greeks_runtime_out: dict = {}
    rate_out: dict = {}
    pricing_model_out: dict = {}
    if adapter_summary:
        universe_out["drop_rows"] = adapter_summary.get("universe_drop_rows", 0)
        universe_out["drop_by_reason"] = dict(
            adapter_summary.get("universe_drop_by_reason") or {}
        )
        # Missing-underlying-match rate (Phase 2 exit criteria): option rows that
        # could not be reconciled to an underlying future. Absent (drop_rate 0.0)
        # when every option mapped cleanly.
        underlying_map_out = dict(adapter_summary.get("underlying_map") or {})
        greeks_runtime_out = dict(adapter_summary.get("greeks_runtime") or {})
        rate_out = dict(adapter_summary.get("rate_summary") or {})
        pricing_model_out = dict(adapter_summary.get("pricing_model_resolution") or {})

    # ── Silver quality flags ──────────────────────────────────────────────────
    def _flag_rate(col_name: str) -> float | None:
        if col_name not in options.columns or n_options == 0:
            return None
        return float(options[col_name].fillna(False).astype(bool).sum() / n_options)

    def _reason_counts(col_name: str) -> dict:
        if col_name not in options.columns:
            return {}
        counts: dict = {}
        for val in options[col_name]:
            for reason in str(val).split(";"):
                reason = reason.strip()
                if reason:
                    counts[reason] = counts.get(reason, 0) + 1
        return counts

    by_reason: dict = {}
    for rc in ("_iv_quality_reason", "_delta_quality_reason", "_premium_quality_reason"):
        for reason, count in _reason_counts(rc).items():
            by_reason[reason] = by_reason.get(reason, 0) + count

    silver_flags = {
        "iv_quality_flag_rate": _flag_rate("_iv_quality_flag"),
        "delta_quality_flag_rate": _flag_rate("_delta_quality_flag"),
        "premium_quality_flag_rate": _flag_rate("_premium_quality_flag"),
        "by_reason": by_reason,
    }

    # ── Near-money IV aggregate (issue 025) ───────────────────────────────────
    # The trustworthy provider/model comparison: aggregate |iv_provided - iv_solved|
    # over rows where price-inversion is valid (near the money, enough time value).
    # This is the IV signal that should move run readiness; deep ITM/OTM inversion
    # artifacts are excluded from it.
    near_money_iv: dict = {
        "invertible_rows": 0, "mismatch_rate": None,
        "median_abs_diff": None, "p95_abs_diff": None,
    }
    if "iv_invertible" in options.columns and n_options > 0:
        inv_mask = options["iv_invertible"].fillna(False).astype(bool)
        diffs = (
            pd.to_numeric(options.loc[inv_mask, "iv_diff"], errors="coerce").dropna()
            if "iv_diff" in options.columns else pd.Series(dtype=float)
        )
        n_inv = int(len(diffs))
        near_money_iv["invertible_rows"] = n_inv
        if n_inv > 0:
            # Aggregate band for the systemic-mismatch detector. Wider than the tight
            # per-row iv_validate_threshold (0.005): the aggregate asks "is exchange IV
            # systematically off near the money?", not "does every row match to 0.5 vol
            # points". Configurable per instrument.
            thr = float((cfg or {}).get("near_money_iv_mismatch_threshold", 0.05))
            near_money_iv["mismatch_rate"] = float((diffs > thr).mean())
            near_money_iv["median_abs_diff"] = float(diffs.median())
            near_money_iv["p95_abs_diff"] = float(diffs.quantile(0.95))

    return {
        "option_rows": n_options,
        "support_future_rows": n_futures,
        "iv": {
            "null_rate": iv_null_rate,
            "solve_fail_rate": iv_solve_fail_rate,
            "flag_rate": iv_flag_rate,
            "max": iv_max,
            "validation": iv_validation,
        },
        "near_money_iv": near_money_iv,
        "delta": {
            "coverage_rate": delta_coverage,
            "bad_sign_count": bad_sign_count,
        },
        "greeks_runtime": greeks_runtime_out,
        "pricing_model": pricing_model_out,
        "rate": rate_out,
        "pcp": {
            "status": pcp_status,
            "flag_rate": pcp_flag_rate,
            "pair_missing_rate": pcp_pair_missing_rate,
            "duplicate_pair_rate": pcp_duplicate_pair_rate,
        },
        "universe": universe_out,
        "underlying_map": {
            "missing_rows": underlying_map_out.get("missing_rows", 0),
            "drop_rate": float(underlying_map_out.get("drop_rate", 0.0)),
        },
        "premium": {
            "flag_rate": silver_flags["premium_quality_flag_rate"],
        },
        "silver_flags": silver_flags,
    }
