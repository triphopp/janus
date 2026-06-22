"""Deterministic checks — produce machine-verifiable outcomes before any LLM summary.

Each check returns a dict with: name, status (pass/warn/fail), score, rationale.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .schema import (
    OutlierCasePackage,
    SearchQuery,
    FetchResult,
    ExtractedDocument,
    SourceRegistryRecord,
    EvidenceClaim,
)
from .config import HarnessConfig
from .source_tier import TIER_SCORES


def _check(name: str, status: str, score: float, rationale: str) -> dict:
    return {"name": name, "status": status, "score": score, "rationale": rationale}


# ── Budget checks ──────────────────────────────────────────────────────────────

def check_query_budget(
    queries_run: int,
    iterations: int,
    cfg: HarnessConfig,
) -> dict:
    if queries_run > cfg.max_queries:
        return _check("query_budget", "fail", 0.0,
                      f"ran {queries_run} queries, limit {cfg.max_queries}")
    if iterations > cfg.max_iterations:
        return _check("query_budget", "fail", 0.0,
                      f"ran {iterations} iterations, limit {cfg.max_iterations}")
    return _check("query_budget", "pass", 1.0,
                  f"{queries_run}/{cfg.max_queries} queries used")


def check_fetch_budget(
    fetched: list[FetchResult],
    elapsed_sec: float,
    cfg: HarnessConfig,
) -> dict:
    total_bytes = sum(f.bytes_read for f in fetched)
    if len(fetched) > cfg.max_fetches:
        return _check("fetch_budget", "fail", 0.0,
                      f"fetched {len(fetched)}, limit {cfg.max_fetches}")
    if elapsed_sec > cfg.max_runtime_sec:
        return _check("fetch_budget", "fail", 0.0,
                      f"runtime {elapsed_sec:.0f}s exceeded {cfg.max_runtime_sec}s")
    return _check("fetch_budget", "pass", 1.0,
                  f"{len(fetched)} fetches, {total_bytes:,} bytes")


# ── Source policy ──────────────────────────────────────────────────────────────

def check_source_policy(fetched: list[FetchResult], cfg: HarnessConfig) -> dict:
    blocked = [f for f in fetched if f.blocked_reason]
    violations = [f for f in fetched
                  if not f.blocked_reason
                  and not f.url.startswith("https://")
                  and not f.url.startswith("fixture://")]
    if violations:
        return _check("source_policy", "fail", 0.0,
                      f"{len(violations)} non-HTTPS fetches detected")
    if blocked:
        return _check("source_policy", "warn", 0.6,
                      f"{len(blocked)} URLs blocked: {blocked[0].blocked_reason}")
    return _check("source_policy", "pass", 1.0, "all fetches policy-compliant")


# ── Source quality ──────────────────────────────────────────────────────────────

def check_source_quality(sources: list[SourceRegistryRecord]) -> dict:
    if not sources:
        return _check("source_quality", "fail", 0.0, "no sources registered")

    tier1 = [s for s in sources if s.source_tier == "tier1_official"]
    tier2 = [s for s in sources if s.source_tier == "tier2_reputable"]
    social = [s for s in sources if s.source_tier == "tier4_social"]

    if tier1:
        return _check("source_quality", "pass", 1.0,
                      f"{len(tier1)} tier1_official source(s) found")
    if len(tier2) >= 2:
        return _check("source_quality", "pass", 0.8,
                      f"{len(tier2)} tier2_reputable sources found")
    if tier2:
        return _check("source_quality", "warn", 0.5,
                      "only one tier2_reputable source; more would strengthen case")
    if social:
        return _check("source_quality", "warn", 0.2,
                      "only social/low-tier sources found")
    return _check("source_quality", "warn", 0.3,
                  f"{len(sources)} sources below tier2")


# ── Temporal consistency ────────────────────────────────────────────────────────

def _classify_timing(published_at: str | None, case_date: str) -> str:
    if not published_at:
        return "unknown_time"
    try:
        pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        ref = datetime.fromisoformat(case_date + "T00:00:00+00:00")
        delta = (pub - ref).total_seconds() / 3600

        if -24 <= delta <= 24:
            return "same_session"
        if -72 <= delta <= 72:
            return "near_event"
        if delta > 72:
            return "late_commentary"
        return "stale_source"
    except (ValueError, OverflowError):
        return "unknown_time"


def check_temporal_consistency(
    sources: list[SourceRegistryRecord],
    case_date: str,
) -> dict:
    if not sources:
        return _check("temporal_consistency", "fail", 0.0, "no sources to check")

    timings = [_classify_timing(s.published_at, case_date) for s in sources]
    same = timings.count("same_session")
    near = timings.count("near_event")
    late = timings.count("late_commentary")
    unknown = timings.count("unknown_time")

    if same + near > 0:
        return _check("temporal_consistency", "pass", 1.0,
                      f"{same} same_session, {near} near_event sources")
    if late > 0:
        return _check("temporal_consistency", "warn", 0.4,
                      f"only late_commentary sources ({late}); may not confirm event timing")
    if unknown > 0:
        return _check("temporal_consistency", "warn", 0.3,
                      f"{unknown} sources have unknown publication time")
    return _check("temporal_consistency", "warn", 0.2, "no sources within event window")


# ── Instrument relevance ────────────────────────────────────────────────────────

def _text_mentions(text: str, terms: list[str]) -> bool:
    t = text.lower()
    return any(term.lower() in t for term in terms if term)


def check_instrument_relevance(
    documents: list[ExtractedDocument],
    case: OutlierCasePackage,
) -> dict:
    if not documents:
        return _check("instrument_relevance", "fail", 0.0, "no documents to check")

    identity_terms = [t for t in [case.symbol, case.instrument] if t]
    if not identity_terms:
        return _check("instrument_relevance", "warn", 0.5, "no instrument/symbol to match")

    relevant = [
        d for d in documents
        if _text_mentions(d.extracted_text + " " + (d.title or ""), identity_terms)
    ]
    if relevant:
        return _check("instrument_relevance", "pass", 1.0,
                      f"{len(relevant)}/{len(documents)} documents mention instrument")
    return _check("instrument_relevance", "warn", 0.2,
                  "no documents mention the instrument by name")


# ── Event relevance ─────────────────────────────────────────────────────────────

def check_event_relevance(
    documents: list[ExtractedDocument],
    case: OutlierCasePackage,
) -> dict:
    if not documents:
        return _check("event_relevance", "fail", 0.0, "no documents to check")

    event_terms = list(case.candidate_terms or [])
    signal = case.signal_type or ""
    if "outlier" in signal:
        event_terms.extend(["price", "move", "return", "gain", "loss", "change"])

    relevant = [
        d for d in documents
        if event_terms and _text_mentions(
            d.extracted_text[:2000] + " " + (d.title or ""), event_terms
        )
    ]
    if relevant:
        return _check("event_relevance", "pass", 0.8,
                      f"{len(relevant)}/{len(documents)} documents contain event terms")
    return _check("event_relevance", "warn", 0.3, "no documents contain event-specific terms")


# ── Cross-source consistency ────────────────────────────────────────────────────

def check_cross_source_consistency(claims: list[EvidenceClaim]) -> dict:
    if not claims:
        return _check("cross_source_consistency", "warn", 0.3, "no claims to compare")

    supporting = [c for c in claims if c.support_score > 0.5]
    contradicting = [c for c in claims if c.contradiction_score > 0.5]

    if supporting and contradicting:
        return _check("cross_source_consistency", "warn", 0.3,
                      f"{len(supporting)} supporting vs {len(contradicting)} contradicting claims")
    if supporting:
        return _check("cross_source_consistency", "pass", 0.9,
                      f"{len(supporting)} supporting claim(s), no contradictions")
    return _check("cross_source_consistency", "warn", 0.3, "no strongly supporting claims")


# ── Contradiction detection ─────────────────────────────────────────────────────

def check_contradiction(claims: list[EvidenceClaim]) -> dict:
    credible_contradictions = [
        c for c in claims
        if c.contradiction_score > 0.6 and c.confidence in ("medium", "high")
    ]
    if credible_contradictions:
        return _check("contradiction_detection", "warn", 0.2,
                      f"{len(credible_contradictions)} credible contradicting claim(s)")
    return _check("contradiction_detection", "pass", 1.0, "no credible contradictions found")


# ── Policy consistency ─────────────────────────────────────────────────────────

def check_policy_consistency(
    verdict: str,
    sources: list[SourceRegistryRecord],
    case: OutlierCasePackage,
) -> dict:
    if verdict == "supported_event":
        tier1 = [s for s in sources if s.source_tier == "tier1_official"]
        tier2 = [s for s in sources if s.source_tier == "tier2_reputable"]
        if tier1 or len(tier2) >= 2:
            return _check("policy_consistency", "pass", 1.0,
                          "supported_event backed by qualifying sources")
        return _check("policy_consistency", "fail", 0.0,
                      "supported_event requires tier1 or two tier2 sources")
    return _check("policy_consistency", "pass", 1.0,
                  f"verdict={verdict!r} requires no additional source policy")


# ── Run all checks ─────────────────────────────────────────────────────────────

def run_all_checks(
    *,
    case: OutlierCasePackage,
    cfg: HarnessConfig,
    queries_run: int,
    iterations: int,
    fetched: list[FetchResult],
    elapsed_sec: float,
    sources: list[SourceRegistryRecord],
    documents: list[ExtractedDocument],
    claims: list[EvidenceClaim],
    verdict: str,
) -> list[dict]:
    return [
        check_query_budget(queries_run, iterations, cfg),
        check_fetch_budget(fetched, elapsed_sec, cfg),
        check_source_policy(fetched, cfg),
        check_source_quality(sources),
        check_temporal_consistency(sources, case.as_of_date),
        check_instrument_relevance(documents, case),
        check_event_relevance(documents, case),
        check_cross_source_consistency(claims),
        check_contradiction(claims),
        check_policy_consistency(verdict, sources, case),
    ]
