"""Coverage / freshness SLA gate (bronze) — catches silent under-coverage.

The row-shape contract (core/contracts.py) answers "is each row well-formed?". It says
nothing about "did we get ENOUGH rows, spanning the window we asked for?". A query for
2024-09..2026-05 that returns 28 rows passes every per-row rule yet is worthless — the
file simply ended. That silent gap is the classic data-ops failure this module closes.

assess_coverage compares the trading days actually present against the requested calendar
window and flags three distinct failures:
  - low coverage   : present_days / expected_days < min_ratio
  - stale tail     : business days between the last data point and the requested end
  - internal gap   : largest run of missing business days inside the window

A failure becomes a Break (severity high=fail / medium=warn) so it surfaces on the
dashboard with the same signed lifecycle chain as a CDC break.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from core import breaks as _breaks
from core import cdc as _cdc

DEFAULT_MIN_RATIO = 0.80
DEFAULT_MAX_GAP_DAYS = 10
COVERAGE_BREAK_SEQ = 900  # high seq so it never collides with CDC break ids


def _present_days(df: pd.DataFrame, date_col: str) -> list[pd.Timestamp]:
    if date_col not in df.columns or df.empty:
        return []
    dts = pd.to_datetime(df[date_col], errors="coerce", utc=False)
    try:
        dts = dts.dt.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    dts = dts.dropna().dt.normalize()
    return sorted(set(dts.tolist()))


def _max_internal_gap(days: list[pd.Timestamp]) -> int:
    """Largest count of missing BUSINESS days between two consecutive present days."""
    worst = 0
    for a, b in zip(days[:-1], days[1:]):
        missing = len(pd.bdate_range(a + pd.Timedelta(days=1), b)) - 1  # exclude b itself
        worst = max(worst, missing)
    return worst


def assess_coverage(
    df: pd.DataFrame,
    start,
    end,
    *,
    date_col: str = "as_of_date",
    min_ratio: float = DEFAULT_MIN_RATIO,
    max_gap_days: int = DEFAULT_MAX_GAP_DAYS,
    calendar_id: str | None = None,
    holidays=None,
) -> dict:
    """Return a coverage report: status (pass|warn|fail), ratios, gaps, reasons.

    Expected trading days come from the resolved exchange/provider calendar when one
    is available (issue 013); otherwise a generic business-day calendar is used and
    labelled ``calendar_id == "generic"`` so the fallback is never mistaken for
    exchange truth.
    """
    from core.exchange_calendar import resolve_calendar

    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    calendar = resolve_calendar(calendar_id, start, end, holidays=holidays)
    expected = calendar["trading_days"]
    n_expected = len(expected)

    days = _present_days(df, date_col)
    n_present = len(days)
    first = days[0] if days else None
    last = days[-1] if days else None
    ratio = (n_present / n_expected) if n_expected else 1.0

    tail_gap = 0
    if last is not None and last < end:
        tail_gap = len(pd.bdate_range(last + pd.Timedelta(days=1), end))
    elif last is None:
        tail_gap = n_expected
    lead_gap = 0
    if first is not None and first > start:
        lead_gap = len(pd.bdate_range(start, first - pd.Timedelta(days=1)))

    internal_gap = _max_internal_gap(days) if n_present > 1 else 0

    reasons: list[str] = []
    status = "pass"
    if n_expected and ratio < min_ratio:
        status = "fail"
        reasons.append(
            f"coverage {ratio:.1%} below {min_ratio:.0%} SLA "
            f"({n_present}/{n_expected} trading days present)"
        )
    if tail_gap > max_gap_days:
        status = "fail"
        reasons.append(
            f"stale tail: {tail_gap} business days have no data after last point "
            f"{last.date() if last is not None else 'n/a'}"
        )
    if lead_gap > max_gap_days:
        if status == "pass":
            status = "warn"
        reasons.append(
            f"late start: {lead_gap} business days missing before first point "
            f"{first.date() if first is not None else 'n/a'}"
        )
    if internal_gap > max_gap_days:
        if status == "pass":
            status = "warn"
        reasons.append(f"internal gap of {internal_gap} consecutive business days")

    return {
        "status": status,
        "coverage_ratio": round(ratio, 4),
        "expected_trading_days": n_expected,
        "present_trading_days": n_present,
        "first_present": str(first.date()) if first is not None else None,
        "last_present": str(last.date()) if last is not None else None,
        "tail_gap_bdays": tail_gap,
        "lead_gap_bdays": lead_gap,
        "max_internal_gap_bdays": internal_gap,
        "min_ratio": min_ratio,
        "max_gap_days": max_gap_days,
        "calendar_id": calendar["calendar_id"],
        "calendar_source": calendar["source"],
        "calendar_holidays_in_window": calendar["holidays"],
        "reasons": reasons,
        "window": [str(start.date()), str(end.date())],
    }


def coverage_breaks(report: dict, run_id: str, start, end) -> list[dict]:
    """Turn a failing/ warning coverage report into a single Break (lifecycle-tracked)."""
    if report["status"] == "pass":
        return []
    severity = "high" if report["status"] == "fail" else "medium"
    rec = _cdc.ChangeRecord(
        stage_from="ingestion",
        stage_to="coverage_sla",
        change_type="coverage_gap",
        key={"window": f"{report['window'][0]}..{report['window'][1]}"},
        column="as_of_date",
        before=report["expected_trading_days"],
        after=report["present_trading_days"],
        delta=float(report["present_trading_days"] - report["expected_trading_days"]),
        reason="; ".join(report["reasons"]) or "coverage_gap",
        run_id=run_id,
    )
    brk = _breaks.new_break(rec, "coverage_gap", severity, run_id, COVERAGE_BREAK_SEQ)
    return [brk]
