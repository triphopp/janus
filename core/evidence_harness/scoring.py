"""Candidate scorer — ranks SearchResults before fetching.

score = 0.30 * relevance + 0.25 * temporal + 0.20 * source_tier
      + 0.15 * directness + 0.10 * novelty
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .schema import SearchResult, OutlierCasePackage
from .source_tier import SourceTierClassifier, TIER_SCORES


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", (text or "").lower()).strip()


def _token_overlap(a: str, b: str) -> float:
    ta = set(_norm(a).split())
    tb = set(_norm(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))


def _temporal_score(published_at: str | None, case_date: str) -> float:
    if not published_at:
        return 0.3
    try:
        pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        ref = datetime.fromisoformat(case_date + "T00:00:00+00:00")
        delta_days = abs((pub - ref).total_seconds()) / 86400
        if delta_days <= 1:
            return 1.0
        if delta_days <= 3:
            return 0.8
        if delta_days <= 7:
            return 0.5
        if delta_days <= 30:
            return 0.2
        return 0.05
    except (ValueError, OverflowError):
        return 0.3


def _directness_score(domain: str, tier: str) -> float:
    if tier == "tier1_official":
        return 1.0
    if tier == "tier2_reputable":
        return 0.6
    return 0.2


def score_result(
    result: SearchResult,
    case: OutlierCasePackage,
    classifier: SourceTierClassifier,
    seen_domains: set[str],
) -> float:
    identity = case.symbol or case.instrument or ""
    query_text = ""
    snippet = result.snippet or ""
    title = result.title or ""

    relevance = max(
        _token_overlap(identity, title),
        _token_overlap(identity, snippet),
        _token_overlap(case.metric_name or "", title + " " + snippet) * 0.5,
    )
    for term in (case.candidate_terms or []):
        relevance = max(relevance, _token_overlap(term, title + " " + snippet) * 0.8)
    relevance = min(relevance, 1.0)

    temporal = _temporal_score(result.published_at, case.as_of_date)
    tier = classifier.classify(result.url)
    tier_score = TIER_SCORES.get(tier, 0.2)
    directness = _directness_score(result.domain, tier)
    novelty = 0.0 if result.domain in seen_domains else 1.0

    return (
        0.30 * relevance
        + 0.25 * temporal
        + 0.20 * tier_score
        + 0.15 * directness
        + 0.10 * novelty
    )


def rank_candidates(
    results: list[SearchResult],
    case: OutlierCasePackage,
    classifier: SourceTierClassifier,
    max_fetches: int = 20,
) -> list[SearchResult]:
    """Return results sorted by score, deduplicated by URL, capped at max_fetches."""
    seen_urls: set[str] = set()
    seen_domains: set[str] = set()
    unique: list[SearchResult] = []
    for r in results:
        if r.url not in seen_urls:
            seen_urls.add(r.url)
            unique.append(r)

    scored = [
        (r, score_result(r, case, classifier, seen_domains))
        for r in unique
    ]
    scored.sort(key=lambda x: x[1], reverse=True)

    selected: list[SearchResult] = []
    tier1_seen = 0
    tier2_seen = 0
    for r, _ in scored:
        if len(selected) >= max_fetches:
            break
        tier = classifier.classify(r.url)
        if tier == "tier1_official" and tier1_seen >= 2:
            continue
        if tier == "tier2_reputable" and tier2_seen >= 4:
            continue
        if tier == "tier1_official":
            tier1_seen += 1
        if tier == "tier2_reputable":
            tier2_seen += 1
        selected.append(r)
        seen_domains.add(r.domain)

    return selected
