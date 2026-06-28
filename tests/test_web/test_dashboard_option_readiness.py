"""Dashboard surfacing of P0 option-market readiness + drill-down (issue 003)."""

import numpy as np
import pandas as pd

from core import options_quality as oq
from web.view_model import build_run_detail_v1


def _sections(summary, artifacts=None):
    detail = build_run_detail_v1({"summary": summary, **(artifacts or {})})
    return {s["id"]: s for s in detail["sections"]}


def _summary(status="blocked"):
    return {
        "run_id": "r1",
        "family": "futures_options",
        "domain_run_readiness": {
            "status": status,
            "checks": {
                "iv_provider_model_mismatch": {"rate": 0.209, "status": "blocked",
                                               "domain_label": "Provider vs model IV disagreement"},
                "pcp_mismatch": {"rate": None, "status": "needs_review",
                                 "domain_label": "Put-call parity breaks"},
                "delta_sign": {"bad_sign_count": 0, "status": "ready",
                               "domain_label": "Option delta sign sanity"},
            },
            "reasons": ["iv_provider_model_mismatch_rate=0.2090>=block"],
        },
        "option_quality": {"option_rows": 100},
        "settlement_availability": {"status": "pass"},
        "unit_assumptions_status": {"status": "pass"},
        "iv_mismatch_review": {"flagged_rows": 2012,
                               "by_reason": {"no_time_value_deep_itm_otm": 1361,
                                             "near_money_genuine_diff": 651}},
    }


# ── Readiness section ─────────────────────────────────────────────────────────

def test_readiness_section_maps_blocked_to_fail():
    s = _sections(_summary("blocked"))["option_market_readiness"]
    assert s["kind"] == "scorecard"
    assert s["status"] == "fail"
    labels = {d["name"]: d["status"] for d in s["payload"]["dimensions"]}
    assert labels["Provider vs model IV disagreement"] == "fail"
    assert labels["Put-call parity breaks"] == "warn"     # needs_review -> warn
    assert labels["Option delta sign sanity"] == "pass"


def test_option_quality_status_follows_readiness_not_flat_available():
    s = _sections(_summary("needs_review"))["option_quality"]
    assert s["status"] == "warn"        # was hardcoded "available" before


def test_data_integrity_gates_section():
    s = _sections(_summary())["data_integrity_gates"]
    assert s["status"] == "pass"
    assert "settlement_availability" in s["payload"]


def test_iv_mismatch_drilldown_section_links_artifact():
    summary = _summary()
    summary["artifacts"] = {"iv_mismatch_review": "tables/iv_mismatch_review.csv"}
    s = _sections(summary)["iv_mismatch_review"]
    assert s["payload"]["flagged_rows"] == 2012
    assert s["payload"]["by_reason"]["no_time_value_deep_itm_otm"] == 1361
    assert s["payload"]["artifact"] == "tables/iv_mismatch_review.csv"


def test_blocked_export_section_is_fail():
    summary = _summary()
    summary["option_chain_greeks"] = {"status": "blocked", "reason": "withheld"}
    s = _sections(summary)["option_chain_greeks"]
    assert s["status"] == "fail"


def test_non_option_run_has_no_readiness_section():
    s = _sections({"run_id": "r", "family": "equity"})
    assert "option_market_readiness" not in s


# ── iv_mismatch_review builder ────────────────────────────────────────────────

def test_iv_mismatch_review_classifies_deep_itm_no_time_value():
    df = pd.DataFrame({
        "instrument_type": ["option", "option", "option"],
        "right": ["C", "C", "P"],
        "strike": [35.0, 70.0, 71.0],
        "underlying_price": [69.69, 69.69, 69.69],
        "option_price": [34.69, 2.10, 2.50],   # row0 = pure intrinsic
        "as_of_date": pd.to_datetime(["2024-09-25"] * 3),
        "contract_root": ["T"] * 3,
        "iv": [0.5826, 0.31, 0.30],
        "iv_solved": [0.001, 0.32, 0.31],
        "iv_diff": [0.5816, 0.01, 0.01],
        "dte_days": [22, 22, 22],
        "iv_flag": [True, True, False],         # row2 not flagged
    })
    review = oq.iv_mismatch_review(df)
    assert len(review) == 2                      # only flagged rows
    deep = review[review["strike"] == 35.0].iloc[0]
    assert deep["time_value"] <= 0.01
    assert deep["reason"] == "no_time_value_deep_itm_otm"

    summary = oq.iv_mismatch_review_summary(review)
    assert summary["flagged_rows"] == 2
    assert "no_time_value_deep_itm_otm" in summary["by_reason"]


def test_iv_mismatch_review_empty_when_no_flags():
    df = pd.DataFrame({"instrument_type": ["option"], "iv_flag": [False]})
    review = oq.iv_mismatch_review(df)
    assert review.empty
    assert list(review.columns) == oq.IV_MISMATCH_REVIEW_COLUMNS
