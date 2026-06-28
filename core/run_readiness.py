"""Domain run readiness — option-market checks must affect whether a run is trusted.

Issue 001 (incident regression) and issue 003 (option market checks must affect
run readiness): the pipeline must never present an option run as normal when the
option-domain checks are severely unreliable. Provider/model IV mismatch and
put-call-parity (call/put) mismatch must be able to push a run to ``needs_review``
or ``blocked``.

This module is a pure function over the ``option_quality`` summary produced by
``core.options_quality.summarize`` plus optional config thresholds. It does not read
files or mutate state, so it is cheap to unit test and safe to reuse from both the
pipeline and the dashboard.

Status ladder (worst wins):

    ready < needs_review < blocked

``not_checked`` is never treated as ``pass``. A missing eligible universe surfaces as
``needs_review`` so the dashboard cannot show false confidence.
"""

from __future__ import annotations

from typing import Optional

# Worst-wins ordering for status escalation.
_STATUS_RANK = {"ready": 0, "needs_review": 1, "blocked": 2}

# Finance-friendly labels so the dashboard first screen shows option-domain risk
# in domain language, not internal flag column names (issue 003).
_DOMAIN_LABELS = {
    "iv_provider_model_mismatch": "Provider vs model IV disagreement",
    "pcp_mismatch": "Put-call parity breaks",
    "delta_sign": "Option delta sign sanity",
    "premium_sanity": "Option premium below intrinsic",
    "missing_underlying_match": "Options without an underlying future match",
}

# Default thresholds. Conservative: a small mismatch rate already warrants review,
# a large one blocks an official run. Callers override via
# cfg["option_market_checks"]["thresholds"].
DEFAULT_THRESHOLDS = {
    "iv_mismatch_review_rate": 0.05,
    "iv_mismatch_block_rate": 0.20,
    "pcp_mismatch_review_rate": 0.05,
    "pcp_mismatch_block_rate": 0.20,
    "delta_bad_sign_review_count": 1,
    "premium_sanity_review_rate": 0.02,
    "premium_sanity_block_rate": 0.10,
    "missing_underlying_review_rate": 0.02,
    "missing_underlying_block_rate": 0.10,
}


def _escalate(current: str, candidate: str) -> str:
    """Return the worse of two statuses."""
    if _STATUS_RANK.get(candidate, 0) > _STATUS_RANK.get(current, 0):
        return candidate
    return current


def _rate_check(
    name: str,
    rate,
    review_rate: float,
    block_rate: float,
    reasons: list,
    check_status: str | None = None,
) -> str:
    """Status for a rate-based check; None rate → needs_review (not_checked != pass)."""
    if check_status == "disabled":
        reasons.append(f"{name}_disabled")
        return "needs_review"
    if rate is None:
        reasons.append(f"{name}_not_checked")
        return "needs_review"
    if rate >= block_rate:
        reasons.append(f"{name}_rate={rate:.4f}>=block")
        return "blocked"
    if rate >= review_rate:
        reasons.append(f"{name}_rate={rate:.4f}>=review")
        return "needs_review"
    return "ready"


def _thresholds(cfg: Optional[dict]) -> dict:
    merged = dict(DEFAULT_THRESHOLDS)
    if cfg:
        overrides = (cfg.get("option_market_checks") or {}).get("thresholds") or {}
        merged.update({k: v for k, v in overrides.items() if v is not None})
    return merged


def assess_option_market_readiness(
    option_quality: Optional[dict],
    cfg: Optional[dict] = None,
) -> dict:
    """Assess option-market run readiness from an option-quality summary.

    Args:
        option_quality: the dict returned by ``core.options_quality.summarize``.
            For non-option runs this is empty/None and the run is trivially ``ready``.
        cfg: merged pipeline config; reads ``option_market_checks.thresholds``.

    Returns:
        A structured readiness dict::

            {
              "status": "ready" | "needs_review" | "blocked",
              "checks": {
                 "iv_provider_model_mismatch": {"rate": .., "status": ..},
                 "pcp_mismatch":               {"rate": .., "status": ..},
                 "delta_sign":                 {"bad_sign_count": .., "status": ..},
              },
              "reasons": [...],
              "thresholds": {...},
            }

    The top-level ``status`` is the worst of the individual check statuses.
    """
    thresholds = _thresholds(cfg)

    if not option_quality or option_quality.get("option_rows", 0) == 0:
        # No option universe to check. This is informative, not a pass: a run that
        # claims to be an option run but has no eligible option rows is review-worthy.
        has_rows = bool(option_quality) and option_quality.get("option_rows", 0) > 0
        status = "ready" if has_rows else "needs_review"
        reasons = [] if has_rows else ["no_eligible_option_universe_not_checked"]
        return {
            "status": "ready" if not option_quality else status,
            "checks": {},
            "reasons": reasons if option_quality else [],
            "thresholds": thresholds,
        }

    iv = option_quality.get("iv") or {}
    pcp = option_quality.get("pcp") or {}
    delta = option_quality.get("delta") or {}

    checks: dict = {}
    reasons: list[str] = []
    status = "ready"

    # ── IV provider/model mismatch ────────────────────────────────────────────
    iv_rate = iv.get("flag_rate")
    iv_status = _rate_check(
        "iv_provider_model_mismatch", iv_rate,
        thresholds["iv_mismatch_review_rate"], thresholds["iv_mismatch_block_rate"],
        reasons,
    )
    checks["iv_provider_model_mismatch"] = {
        "rate": iv_rate, "status": iv_status,
        "domain_label": _DOMAIN_LABELS["iv_provider_model_mismatch"],
    }
    status = _escalate(status, iv_status)

    # ── Put-call-parity (call/put) mismatch ───────────────────────────────────
    pcp_rate = pcp.get("flag_rate")
    pcp_status = _rate_check(
        "pcp_mismatch", pcp_rate,
        thresholds["pcp_mismatch_review_rate"], thresholds["pcp_mismatch_block_rate"],
        reasons,
        pcp.get("status"),
    )
    checks["pcp_mismatch"] = {
        "rate": pcp_rate, "status": pcp_status,
        "check_status": pcp.get("status", "checked"),
        "domain_label": _DOMAIN_LABELS["pcp_mismatch"],
    }
    status = _escalate(status, pcp_status)

    # ── Delta sign sanity ─────────────────────────────────────────────────────
    bad_sign = delta.get("bad_sign_count")
    if bad_sign is None:
        delta_status = "needs_review"
        reasons.append("delta_sign_not_checked")
    elif bad_sign >= thresholds["delta_bad_sign_review_count"]:
        delta_status = "needs_review"
        reasons.append(f"delta_bad_sign_count={bad_sign}")
    else:
        delta_status = "ready"
    checks["delta_sign"] = {
        "bad_sign_count": bad_sign, "status": delta_status,
        "domain_label": _DOMAIN_LABELS["delta_sign"],
    }
    status = _escalate(status, delta_status)

    # ── Premium sanity (premium below intrinsic) ──────────────────────────────
    premium = option_quality.get("premium") or {}
    premium_rate = premium.get("flag_rate")
    premium_status = _rate_check(
        "premium_sanity", premium_rate,
        thresholds["premium_sanity_review_rate"], thresholds["premium_sanity_block_rate"],
        reasons,
    )
    checks["premium_sanity"] = {
        "rate": premium_rate, "status": premium_status,
        "domain_label": _DOMAIN_LABELS["premium_sanity"],
    }
    status = _escalate(status, premium_status)

    # ── Missing underlying match ──────────────────────────────────────────────
    underlying_map = option_quality.get("underlying_map") or {}
    missing_rate = underlying_map.get("drop_rate")
    missing_status = _rate_check(
        "missing_underlying_match", missing_rate,
        thresholds["missing_underlying_review_rate"], thresholds["missing_underlying_block_rate"],
        reasons,
    )
    checks["missing_underlying_match"] = {
        "rate": missing_rate, "status": missing_status,
        "domain_label": _DOMAIN_LABELS["missing_underlying_match"],
    }
    status = _escalate(status, missing_status)

    return {
        "status": status,
        "checks": checks,
        "reasons": reasons,
        "thresholds": thresholds,
    }
