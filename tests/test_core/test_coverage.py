"""Coverage / freshness SLA gate tests — silent under-coverage must surface as a break."""

import pandas as pd

from core import coverage


def _frame(dates):
    return pd.DataFrame({"as_of_date": pd.to_datetime(dates), "price": range(len(dates))})


def test_full_coverage_passes():
    days = pd.bdate_range("2024-01-01", "2024-03-29")
    rep = coverage.assess_coverage(_frame(days), "2024-01-01", "2024-03-29")
    assert rep["status"] == "pass"
    assert rep["coverage_ratio"] == 1.0
    assert coverage.coverage_breaks(rep, "run1", "2024-01-01", "2024-03-29") == []


def test_short_window_fails_low_ratio():
    # 4 days present for a ~440-bday window — the exact WTI regression
    days = pd.bdate_range("2024-09-25", "2024-09-30")
    rep = coverage.assess_coverage(_frame(days), "2024-09-25", "2026-05-29")
    assert rep["status"] == "fail"
    assert rep["coverage_ratio"] < 0.05
    assert any("below" in r for r in rep["reasons"])


def test_stale_tail_fails():
    days = pd.bdate_range("2024-01-01", "2024-02-15")  # data stops mid-window
    rep = coverage.assess_coverage(_frame(days), "2024-01-01", "2024-06-28", min_ratio=0.0)
    assert rep["status"] == "fail"
    assert rep["tail_gap_bdays"] > rep["max_gap_days"]
    assert any("stale tail" in r for r in rep["reasons"])


def test_internal_gap_warns():
    a = pd.bdate_range("2024-01-01", "2024-01-31")
    b = pd.bdate_range("2024-03-01", "2024-03-29")  # ~20 bday hole in February
    rep = coverage.assess_coverage(_frame(a.append(b)), "2024-01-01", "2024-03-29", min_ratio=0.0)
    assert rep["status"] in ("warn", "fail")
    assert rep["max_internal_gap_bdays"] > rep["max_gap_days"]


def test_empty_frame_fails():
    rep = coverage.assess_coverage(_frame([]), "2024-01-01", "2024-06-28")
    assert rep["status"] == "fail"
    assert rep["present_trading_days"] == 0
    brks = coverage.coverage_breaks(rep, "run1", "2024-01-01", "2024-06-28")
    assert len(brks) == 1 and brks[0]["severity"] == "high"


def test_coverage_break_shape_matches_cdc_break():
    days = pd.bdate_range("2024-09-25", "2024-09-30")
    rep = coverage.assess_coverage(_frame(days), "2024-09-25", "2026-05-29")
    brks = coverage.coverage_breaks(rep, "run1", "2024-09-25", "2026-05-29")
    b = brks[0]
    # same fields the dashboard + breaks lifecycle expect
    for k in ("break_id", "type", "severity", "status", "stage", "key", "history"):
        assert k in b
    assert b["type"] == "coverage_gap"
    assert b["status"] == "DETECTED"
    assert b["stage"] == "ingestion->coverage_sla"
    from core import breaks as bk
    assert bk.verify_chain(b)  # signed chain valid on creation
