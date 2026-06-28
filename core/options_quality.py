"""Option-specific quality summarizer.

Pure function — accepts a prepared DataFrame and adapter universe summary,
returns a structured quality dict suitable for inclusion in summary.json.
Missing optional columns produce None or 0, never crash.
"""

from __future__ import annotations

import pandas as pd


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
    if adapter_summary:
        universe_out["drop_rows"] = adapter_summary.get("universe_drop_rows", 0)
        universe_out["drop_by_reason"] = dict(
            adapter_summary.get("universe_drop_by_reason") or {}
        )
        # Missing-underlying-match rate (Phase 2 exit criteria): option rows that
        # could not be reconciled to an underlying future. Absent (drop_rate 0.0)
        # when every option mapped cleanly.
        underlying_map_out = dict(adapter_summary.get("underlying_map") or {})

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

    return {
        "option_rows": n_options,
        "support_future_rows": n_futures,
        "iv": {
            "null_rate": iv_null_rate,
            "solve_fail_rate": iv_solve_fail_rate,
            "flag_rate": iv_flag_rate,
            "max": iv_max,
        },
        "delta": {
            "coverage_rate": delta_coverage,
            "bad_sign_count": bad_sign_count,
        },
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
