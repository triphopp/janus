"""Domain run readiness — option-market checks must affect whether a run is trusted.

Issue 001 (WTI incident regression) and issue 003 (option market checks must affect
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

# Default thresholds. Conservative: a small mismatch rate already warrants review,
# a large one blocks an official run. Callers override via
# cfg["option_market_checks"]["thresholds"].
DEFAULT_THRESHOLDS = {
    "iv_mismatch_review_rate": 0.05,
    "iv_mismatch_block_rate": 0.20,
    "pcp_mismatch_review_rate": 0.05,
    "pcp_mismatch_block_rate": 0.20,
    "delta_bad_sign_review_count": 1,
}


def _escalate(current: str, candidate: str) -> str:
    """Return the worse of two statuses."""
    if _STATUS_RANK.get(candidate, 0) > _STATUS_RANK.get(current, 0):
        return candidate
    return current


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
    if iv_rate is None:
        iv_status = "needs_review"
        reasons.append("iv_provider_model_mismatch_not_checked")
    elif iv_rate >= thresholds["iv_mismatch_block_rate"]:
        iv_status = "blocked"
        reasons.append(f"iv_provider_model_mismatch_rate={iv_rate:.4f}>=block")
    elif iv_rate >= thresholds["iv_mismatch_review_rate"]:
        iv_status = "needs_review"
        reasons.append(f"iv_provider_model_mismatch_rate={iv_rate:.4f}>=review")
    else:
        iv_status = "ready"
    checks["iv_provider_model_mismatch"] = {"rate": iv_rate, "status": iv_status}
    status = _escalate(status, iv_status)

    # ── Put-call-parity (call/put) mismatch ───────────────────────────────────
    pcp_rate = pcp.get("flag_rate")
    if pcp_rate is None:
        pcp_status = "needs_review"
        reasons.append("pcp_mismatch_not_checked")
    elif pcp_rate >= thresholds["pcp_mismatch_block_rate"]:
        pcp_status = "blocked"
        reasons.append(f"pcp_mismatch_rate={pcp_rate:.4f}>=block")
    elif pcp_rate >= thresholds["pcp_mismatch_review_rate"]:
        pcp_status = "needs_review"
        reasons.append(f"pcp_mismatch_rate={pcp_rate:.4f}>=review")
    else:
        pcp_status = "ready"
    checks["pcp_mismatch"] = {"rate": pcp_rate, "status": pcp_status}
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
    checks["delta_sign"] = {"bad_sign_count": bad_sign, "status": delta_status}
    status = _escalate(status, delta_status)

    return {
        "status": status,
        "checks": checks,
        "reasons": reasons,
        "thresholds": thresholds,
    }
