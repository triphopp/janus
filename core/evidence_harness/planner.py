"""QueryPlanner — deterministic seed query generator.

Seed queries are derived entirely from OutlierCasePackage fields and config.
LLM query expansion is validated here but only triggered by the controller
after seed queries are exhausted. Phase 2 implements seed queries only.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from .schema import OutlierCasePackage, SearchQuery
from .ids import query_id as make_query_id


# ── Date window ───────────────────────────────────────────────────────────────

def _prev_business_days(d: date, n: int) -> date:
    while n > 0:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def date_window(case: OutlierCasePackage) -> tuple[str, str]:
    """Return (date_start, date_end) as ISO strings for a case's signal type."""
    try:
        as_of = date.fromisoformat(case.as_of_date)
    except ValueError:
        as_of = date.today()

    signal = case.signal_type or ""

    if signal == "vol_surface_cluster":
        start = _prev_business_days(as_of, 2)
        end = as_of + timedelta(days=2)
    elif signal in ("diff_finding", "data_quality_finding"):
        end = as_of
        start = as_of - timedelta(days=7)
    else:
        # daily return_outlier default
        start = _prev_business_days(as_of, 2)
        end = as_of + timedelta(days=2)

    return start.isoformat(), end.isoformat()


# ── Query templates ────────────────────────────────────────────────────────────

_MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _friendly_date(iso_date: str) -> str:
    """Convert '2017-02-01' → 'February 2017' for more natural news searches."""
    try:
        d = date.fromisoformat(iso_date)
        return f"{_MONTH_NAMES[d.month]} {d.year}"
    except (ValueError, IndexError):
        return iso_date


def _equity_queries(case: OutlierCasePackage, direction: str, date: str) -> list[tuple[str, str]]:
    symbol = case.symbol or case.instrument or ""
    terms = _direction_terms(case, direction)
    dir_term = terms[0] if terms else "move"
    analyst_term = "upgrade" if direction == "high" else "downgrade"

    # Contract-specified seed order is mandatory: neutral first, then directional/event/official/analyst.
    # candidate_terms are appended AFTER contract seeds so they never displace index 0.
    generic = [
        (f"{symbol} stock move {date}", "equity.return_outlier.neutral.v1"),
        (f"{symbol} shares {dir_term} {date}", "equity.return_outlier.directional.v1"),
        (f"{symbol} earnings guidance {date}", "equity.return_outlier.event.v1"),
        (f"{symbol} SEC filing {date}", "equity.return_outlier.official.v1"),
        (f"{symbol} analyst {analyst_term} {date}", "equity.return_outlier.analyst.v1"),
    ]
    candidate_queries: list[tuple[str, str]] = [
        (term, "equity.candidate_term.v1") for term in (case.candidate_terms or [])
    ]
    return generic + candidate_queries


def _futures_queries(case: OutlierCasePackage, date: str) -> list[tuple[str, str]]:
    instrument = case.instrument or case.symbol or ""
    queries = [
        (f"{instrument} price move {date}", "futures.return_outlier.neutral.v1"),
        (f"{instrument} futures settlement {date}", "futures.return_outlier.settlement.v1"),
        (f"{instrument} inventory report {date}", "futures.return_outlier.inventory.v1"),
    ]
    hints = [h.upper() for h in (case.source_hints or [])]
    if "EIA" in hints:
        queries.append((f"EIA crude oil inventory {date}", "futures.return_outlier.eia.v1"))
    if "OPEC" in hints:
        queries.append((f"OPEC oil market {date}", "futures.return_outlier.opec.v1"))
    if "CME" in hints and len(queries) < 6:
        queries.append((f"CME {instrument} contract notice {date}", "futures.return_outlier.cme.v1"))
    return queries


def _options_queries(case: OutlierCasePackage, date: str) -> list[tuple[str, str]]:
    symbol = case.symbol or case.instrument or ""
    return [
        (f"{symbol} implied volatility spike {date}", "options.vol_surface.spike.v1"),
        (f"{symbol} options volume {date}", "options.vol_surface.volume.v1"),
        (f"{symbol} volatility skew {date}", "options.vol_surface.skew.v1"),
        (f"{symbol} earnings volatility {date}", "options.vol_surface.earnings.v1"),
    ]


def _diff_queries(case: OutlierCasePackage, date: str) -> list[tuple[str, str]]:
    instrument = case.instrument or case.symbol or ""
    return [
        (f"{instrument} vendor data correction {date}", "diff.data_quality.vendor.v1"),
        (f"{instrument} exchange settlement correction {date}", "diff.data_quality.exchange.v1"),
        (f"{instrument} bad tick correction {date}", "diff.data_quality.bad_tick.v1"),
    ]


def _direction_terms(case: OutlierCasePackage, direction: str) -> list[str]:
    ctx = case.local_context or {}
    explicit = ctx.get("direction") or direction or "neutral"
    terms_map = {
        "high": ["rise", "jump", "surge", "rally"],
        "low": ["fall", "drop", "plunge", "selloff"],
    }
    return terms_map.get(explicit, ["move"])


def _detect_direction(case: OutlierCasePackage) -> str:
    ctx = case.local_context or {}
    if ctx.get("direction"):
        return ctx["direction"]
    if case.pct_change is not None:
        return "high" if case.pct_change > 0 else "low"
    if case.z_score is not None:
        return "high" if case.z_score > 0 else "low"
    return "neutral"


# ── Main planner ───────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


class QueryPlanner:
    """Generate deterministic seed queries from a case package."""

    def __init__(self, max_queries: int = 8) -> None:
        self.max_queries = max_queries

    def plan(self, case: OutlierCasePackage) -> list[SearchQuery]:
        date_start, date_end = date_window(case)
        direction = _detect_direction(case)
        family = (case.family or "").lower()
        signal = (case.signal_type or "").lower()

        if signal in ("diff_finding", "data_quality_finding"):
            raw = _diff_queries(case, case.as_of_date)
        elif family in ("equity_options", "futures_options") or signal == "vol_surface_cluster":
            raw = _options_queries(case, case.as_of_date)
        elif family == "futures":
            raw = _futures_queries(case, case.as_of_date)
        else:
            raw = _equity_queries(case, direction, case.as_of_date)

        seen: set[str] = set()
        queries: list[SearchQuery] = []
        for text, template_id in raw:
            norm = _normalize(text)
            if norm in seen:
                continue
            seen.add(norm)
            qid = make_query_id(case.case_id, text, date_start, date_end, [])
            queries.append(
                SearchQuery(
                    query_id=qid,
                    case_id=case.case_id,
                    text=text,
                    date_start=date_start,
                    date_end=date_end,
                    domains=[],
                    reason=f"seed query ({template_id})",
                    priority=100 - len(queries),
                    template_id=template_id,
                    evidence_goal="explain_price_move",
                )
            )
            if len(queries) >= self.max_queries:
                break

        return queries

    def validate_llm_expansion(
        self,
        proposed_text: str,
        proposed_date_start: str | None,
        proposed_date_end: str | None,
        allowed_date_start: str,
        allowed_date_end: str,
        seen_normalized: set[str],
        deny_domains: list[str],
        remaining_budget: int,
    ) -> str | None:
        """Return None if valid, or a rejection reason string."""
        if remaining_budget <= 0:
            return "query_budget_exhausted"
        norm = _normalize(proposed_text)
        if norm in seen_normalized:
            return "duplicate_query"
        if len(proposed_text) > 200:
            return "query_too_long"
        if proposed_date_start and proposed_date_start < allowed_date_start:
            return "date_window_violation"
        if proposed_date_end and proposed_date_end > allowed_date_end:
            return "date_window_violation"
        return None
