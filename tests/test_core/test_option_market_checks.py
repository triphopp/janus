"""Option-market checks affect run readiness with domain labels (issue 003)."""

from core.run_readiness import assess_option_market_readiness


def _summary(iv_rate=0.0, pcp_rate=0.0, bad_sign=0, option_rows=100,
             premium_rate=0.0, missing_underlying_rate=0.0):
    return {
        "option_rows": option_rows,
        "iv": {"flag_rate": iv_rate},
        "pcp": {"flag_rate": pcp_rate},
        "delta": {"bad_sign_count": bad_sign},
        "premium": {"flag_rate": premium_rate},
        "underlying_map": {"drop_rate": missing_underlying_rate},
    }


def test_iv_mismatch_rate_can_block_or_review_run():
    review = assess_option_market_readiness(_summary(iv_rate=0.10))
    assert review["checks"]["iv_provider_model_mismatch"]["status"] == "needs_review"
    assert review["status"] == "needs_review"

    blocked = assess_option_market_readiness(_summary(iv_rate=0.40))
    assert blocked["checks"]["iv_provider_model_mismatch"]["status"] == "blocked"
    assert blocked["status"] == "blocked"


def test_pcp_mismatch_rate_can_block_or_review_run():
    review = assess_option_market_readiness(_summary(pcp_rate=0.08))
    assert review["status"] == "needs_review"
    blocked = assess_option_market_readiness(_summary(pcp_rate=0.30))
    assert blocked["status"] == "blocked"


def test_missing_eligible_universe_is_not_checked_not_pass():
    out = assess_option_market_readiness(_summary(option_rows=0))
    assert out["status"] == "needs_review"
    assert any("not_checked" in r for r in out["reasons"])


def test_checks_carry_domain_labels():
    out = assess_option_market_readiness(_summary(iv_rate=0.10, pcp_rate=0.10))
    assert out["checks"]["iv_provider_model_mismatch"]["domain_label"] == \
        "Provider vs model IV disagreement"
    assert out["checks"]["pcp_mismatch"]["domain_label"] == "Put-call parity breaks"
    assert out["checks"]["delta_sign"]["domain_label"] == "Option delta sign sanity"


def test_premium_sanity_can_block_or_review_run():
    review = assess_option_market_readiness(_summary(premium_rate=0.05))
    assert review["checks"]["premium_sanity"]["status"] == "needs_review"
    blocked = assess_option_market_readiness(_summary(premium_rate=0.20))
    assert blocked["checks"]["premium_sanity"]["status"] == "blocked"
    assert blocked["status"] == "blocked"


def test_missing_underlying_match_can_block_or_review_run():
    review = assess_option_market_readiness(_summary(missing_underlying_rate=0.05))
    assert review["checks"]["missing_underlying_match"]["status"] == "needs_review"
    blocked = assess_option_market_readiness(_summary(missing_underlying_rate=0.20))
    assert blocked["checks"]["missing_underlying_match"]["status"] == "blocked"
    assert blocked["status"] == "blocked"


def test_all_five_phase2_checks_present_with_domain_labels():
    """Phase 2 exit: IV, PCP, premium, delta, and missing-underlying are all checked."""
    out = assess_option_market_readiness(_summary())
    for key in ("iv_provider_model_mismatch", "pcp_mismatch", "premium_sanity",
                "delta_sign", "missing_underlying_match"):
        assert key in out["checks"]
        assert out["checks"][key]["domain_label"]


def test_thresholds_are_configurable():
    cfg = {"option_market_checks": {"thresholds": {"iv_mismatch_block_rate": 0.05}}}
    out = assess_option_market_readiness(_summary(iv_rate=0.06), cfg)
    assert out["status"] == "blocked"


def test_disabled_pcp_check_is_visible_not_clean():
    summary = _summary(pcp_rate=None)
    summary["pcp"]["status"] = "disabled"
    out = assess_option_market_readiness(summary)
    assert out["checks"]["pcp_mismatch"]["status"] == "needs_review"
    assert out["checks"]["pcp_mismatch"]["check_status"] == "disabled"
    assert "pcp_mismatch_disabled" in out["reasons"]
