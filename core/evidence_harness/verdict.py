"""Verdict reducer — deterministic, purely from checks + registry + claims.

Precedence (highest to lowest):
  failed > conflicting_evidence > suspected_data_issue > supported_event
  > unsupported > insufficient_evidence
"""

from __future__ import annotations

from .schema import SourceRegistryRecord, EvidenceClaim

VERDICTS = [
    "failed",
    "conflicting_evidence",
    "suspected_data_issue",
    "supported_event",
    "unsupported",
    "insufficient_evidence",
]


def _check_status(checks: list[dict], name: str) -> str:
    for c in checks:
        if c.get("name") == name:
            return c.get("status", "warn")
    return "warn"


def _failed(checks: list[dict]) -> bool:
    mandatory = ("query_budget", "fetch_budget")
    for name in mandatory:
        if _check_status(checks, name) == "fail":
            return True
    return False


def _conflicting(
    checks: list[dict],
    claims: list[EvidenceClaim],
    sources: list[SourceRegistryRecord],
) -> bool:
    contradiction_status = _check_status(checks, "contradiction_detection")
    if contradiction_status not in ("warn", "fail"):
        return False
    credible_supports = [
        c for c in claims
        if c.support_score > 0.5
        and any(
            s.document_id == c.document_id
            and s.source_tier in ("tier1_official", "tier2_reputable")
            for s in sources
        )
    ]
    credible_contradicts = [
        c for c in claims
        if c.contradiction_score > 0.5
        and any(
            s.document_id == c.document_id
            and s.source_tier in ("tier1_official", "tier2_reputable")
            for s in sources
        )
    ]
    return bool(credible_supports) and bool(credible_contradicts)


def _suspected_data_issue(claims: list[EvidenceClaim]) -> bool:
    data_issue_types = {"data_issue", "bad_tick", "vendor_correction", "stale_data"}
    return any(
        c.event_type in data_issue_types and c.support_score > 0.5
        for c in claims
    )


def _supported(
    checks: list[dict],
    sources: list[SourceRegistryRecord],
) -> bool:
    if _check_status(checks, "source_quality") == "fail":
        return False
    if _check_status(checks, "temporal_consistency") == "fail":
        return False
    if _check_status(checks, "instrument_relevance") == "fail":
        return False

    tier1 = [s for s in sources if s.source_tier == "tier1_official"]
    tier2 = [s for s in sources if s.source_tier == "tier2_reputable"]
    return bool(tier1) or len(tier2) >= 2


def _unsupported(checks: list[dict], queries_run: int) -> bool:
    min_queries = 3
    if queries_run < min(min_queries, 1):
        return False
    critical_failures = ("query_budget", "fetch_budget", "source_policy")
    for name in critical_failures:
        if _check_status(checks, name) == "fail":
            return False
    source_quality = _check_status(checks, "source_quality")
    return source_quality == "fail"


def reduce_verdict(
    checks: list[dict],
    sources: list[SourceRegistryRecord],
    claims: list[EvidenceClaim],
    queries_run: int = 0,
) -> tuple[str, str]:
    """Return (verdict, confidence)."""
    if _failed(checks):
        return "failed", "high"

    if _conflicting(checks, claims, sources):
        return "conflicting_evidence", "medium"

    if _suspected_data_issue(claims):
        return "suspected_data_issue", "medium"

    if _supported(checks, sources):
        tier1 = [s for s in sources if s.source_tier == "tier1_official"]
        confidence = "high" if tier1 else "medium"
        return "supported_event", confidence

    if _unsupported(checks, queries_run):
        return "unsupported", "medium"

    return "insufficient_evidence", "low"
