"""HarnessController — orchestrates the full evidence search loop.

Phase 2+: planner + scoring + extract + checks + verdict + citations.
Phase 3+: wraps providers with caching; writes cache entries to replay_manifest.
LLM is never called here; that path lives in llm/ and is gated by config.llm_enabled.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .schema import (
    OutlierCasePackage,
    HarnessRunResult,
    SearchQuery,
    SearchResult,
    FetchResult,
    ExtractedDocument,
    EvidenceClaim,
    SourceRegistryRecord,
    to_json_safe,
)
from .config import HarnessConfig
from .artifact import ArtifactWriter
from .ids import stable_id, source_id as make_source_id, document_id as make_document_id


def _harness_run_id(case_id: str, run_id: str, started_at: str) -> str:
    payload = {"case_id": case_id, "run_id": run_id, "started_at": started_at}
    return stable_id("hrn", payload)


def run_harness(
    case: OutlierCasePackage,
    config: HarnessConfig,
    *,
    search_provider=None,
    fetch_provider=None,
) -> HarnessRunResult:
    """Entry point for a single harness run against one case package."""
    case.validate()

    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()
    harness_run_id = _harness_run_id(case.case_id, case.run_id, started_at)

    writer = ArtifactWriter(
        artifact_dir=config.artifact_dir,
        run_id=case.run_id,
        case_id=case.case_id,
        harness_run_id=harness_run_id,
    )

    # ── Providers ──────────────────────────────────────────────────────────
    if search_provider is None:
        search_provider = _make_search_provider(config)
    if fetch_provider is None:
        fetch_provider = _make_fetch_provider(config)

    from .planner import QueryPlanner
    from .source_tier import SourceTierClassifier
    from .scoring import rank_candidates
    from .extract import PlainTextExtractor
    from .checks import run_all_checks
    from .verdict import reduce_verdict
    from .citations import verify_citations
    from .cache import HarnessCache, CachingSearchProvider, CachingFetchProvider

    classifier = SourceTierClassifier(config.source_tiers)
    extractor = PlainTextExtractor()
    planner = QueryPlanner(max_queries=config.max_queries)

    # ── Wrap providers with caching (skip in replay mode — providers already read from cache)
    cache = HarnessCache(config.cache_dir)
    if config.mode != "replay":
        search_provider = CachingSearchProvider(search_provider, cache)
        fetch_provider = CachingFetchProvider(fetch_provider, cache)

    # ── Query planning ──────────────────────────────────────────────────────
    queries = planner.plan(case)

    # ── Search loop ─────────────────────────────────────────────────────────
    all_results: list[SearchResult] = []
    for q in queries:
        results = search_provider.search(
            q,
            max_results=config.max_results_per_query,
            timeout_sec=config.request_timeout_sec,
        )
        all_results.extend(results)

    # ── Score + select fetch candidates ────────────────────────────────────
    candidates = rank_candidates(all_results, case, classifier, config.max_fetches)

    # ── Fetch ───────────────────────────────────────────────────────────────
    fetched: list[FetchResult] = []
    for result in candidates:
        if len(fetched) >= config.max_fetches:
            break
        fr = fetch_provider.fetch(
            result.url,
            timeout_sec=config.request_timeout_sec,
            max_bytes=config.max_page_bytes,
        )
        fetched.append(fr)

    # ── Extract + classify tier ─────────────────────────────────────────────
    accessed_at = datetime.now(timezone.utc).isoformat()
    documents: list[ExtractedDocument] = []
    sources: list[SourceRegistryRecord] = []

    url_to_result: dict[str, SearchResult] = {r.url: r for r in all_results}

    for fr in fetched:
        tier = classifier.classify(fr.url)
        doc = extractor.extract(fr, accessed_at=accessed_at, source_tier=tier)
        if doc is None:
            continue
        documents.append(doc)

        search_res = url_to_result.get(fr.url)
        query_ids = [search_res.query_id] if search_res else []

        src_id = make_source_id(fr.final_url or fr.url, fr.content_hash)
        import hashlib as _hl
        extract_hash = "sha256:" + _hl.sha256(doc.extracted_text.encode()).hexdigest()

        sources.append(
            SourceRegistryRecord(
                document_id=doc.document_id,
                source_id=src_id,
                url=fr.url,
                final_url=fr.final_url,
                domain=doc.domain,
                source_tier=tier,
                fetched_at=fr.fetched_at,
                accessed_at=accessed_at,
                content_hash=fr.content_hash,
                extract_hash=extract_hash,
                provider=search_provider.name if hasattr(search_provider, "name") else "unknown",
                title=doc.title,
                published_at=_published_at(fr.url, url_to_result),
                query_ids=query_ids,
                cache_paths={},
            )
        )

    # ── Claims — rule-based base, then LLM expansion if enabled ───────────
    claims = _generate_claims(documents, sources, case)

    llm_summary: dict = {}
    llm_client = None
    if config.llm_enabled:
        from .llm import build_llm_client, run_claim_extraction, run_query_expansion, run_evidence_summary
        llm_client = build_llm_client(config)
        # Replace rule-based claims with LLM-extracted claims per document
        llm_claims: list[EvidenceClaim] = []
        for doc in documents:
            llm_claims.extend(run_claim_extraction(doc, case, sources, llm_client))
        if llm_claims:
            claims = llm_claims

    # ── Checks ─────────────────────────────────────────────────────────────
    elapsed = time.monotonic() - t0
    # First pass verdict (for policy_consistency check)
    prelim_verdict, prelim_conf = reduce_verdict([], sources, claims, len(queries))
    checks = run_all_checks(
        case=case,
        cfg=config,
        queries_run=len(queries),
        iterations=1,
        fetched=fetched,
        elapsed_sec=elapsed,
        sources=sources,
        documents=documents,
        claims=claims,
        verdict=prelim_verdict,
    )

    # ── Final verdict ───────────────────────────────────────────────────────
    final_verdict, confidence = reduce_verdict(checks, sources, claims, len(queries))

    # ── LLM evidence summary (if enabled) ─────────────────────────────────
    if config.llm_enabled and llm_client is not None:
        from .llm import run_evidence_summary
        llm_summary = run_evidence_summary(
            case=case, verdict=final_verdict, confidence=confidence,
            claims=claims, checks=checks, registry=sources, client=llm_client,
        )

    # ── Citation verification ──────────────────────────────────────────────
    citation_report = verify_citations(
        summary=llm_summary, registry=sources, verdict=final_verdict
    )

    # ── Artifacts ──────────────────────────────────────────────────────────
    finished_at = datetime.now(timezone.utc).isoformat()

    writer.write_case_package(case)
    writer.write_config(_config_dict(config))
    writer.write_query_log(queries)
    writer.write_search_results(all_results)
    writer.write_fetch_log(fetched)
    writer.write_sources(sources)
    writer.write_claims(claims)
    writer.write_checks(checks)
    writer.write_citation_report(citation_report)

    supporting_doc_ids = [
        c.document_id for c in claims
        if c.support_score > 0.5
        and any(s.document_id == c.document_id
                and s.source_tier in ("tier1_official", "tier2_reputable")
                for s in sources)
    ]

    verdict_doc = {
        "schema_version": "evidence.verdict.v1",
        "run_id": case.run_id,
        "case_id": case.case_id,
        "harness_run_id": harness_run_id,
        "verdict": final_verdict,
        "confidence": confidence,
        "deterministic_verdict": final_verdict,
        "llm_summary": llm_summary.get("summary", "") if llm_summary else "",
        "llm_key_findings": llm_summary.get("key_findings", []) if llm_summary else [],
        "llm_summary_valid": bool(llm_summary and not llm_summary.get("llm_error")),
        "llm_provider": config.llm_provider if config.llm_enabled else None,
        "llm_model": config.llm_model if config.llm_enabled else None,
        "llm_prompt_version": config.llm_prompt_version if config.llm_enabled else None,
        "citation_status": citation_report["status"],
        "supporting_document_ids": supporting_doc_ids,
        "contradicting_document_ids": [],
        "limitations": _collect_limitations(checks, sources, documents),
        "artifact_paths": writer.relative_paths(),
        "started_at": started_at,
        "finished_at": finished_at,
    }
    writer.write_verdict(verdict_doc)

    cache_entries: list[dict] = []
    if hasattr(search_provider, "cache_entries"):
        cache_entries.extend(search_provider.cache_entries())
    if hasattr(fetch_provider, "cache_entries"):
        cache_entries.extend(fetch_provider.cache_entries())

    manifest = {
        "harness_run_id": harness_run_id,
        "case_id": case.case_id,
        "run_id": case.run_id,
        "config_hash": _hash_config(config),
        "planner_version": "evidence_harness.planner.v1",
        "extractor_version": "plaintext.v1",
        "scoring_version": "evidence_harness.scoring.v1",
        "llm_provider": config.llm_provider if config.llm_enabled else None,
        "llm_adapter": None,
        "llm_model": config.llm_model if config.llm_enabled else None,
        "llm_prompt_version": config.llm_prompt_version if config.llm_enabled else None,
        "created_at": finished_at,
        "cache_entries": cache_entries,
    }
    writer.write_replay_manifest(manifest)

    return HarnessRunResult(
        case_id=case.case_id,
        run_id=case.run_id,
        harness_run_id=harness_run_id,
        status=final_verdict,
        verdict=final_verdict,
        confidence=confidence,
        queries=queries,
        search_results=all_results,
        fetched=fetched,
        documents=documents,
        sources=sources,
        claims=claims,
        checks=checks,
        limitations=verdict_doc["limitations"],
        artifact_paths=writer.artifact_paths(),
        audit={
            "started_at": started_at,
            "finished_at": finished_at,
            # case fields for graph_builder
            "signal_type": case.signal_type,
            "as_of_date": case.as_of_date,
            "z_score": case.z_score,
            "instrument": case.instrument,
            "family": case.family,
            "severity": case.severity,
            "observed_value": case.observed_value,
        },
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _generate_claims(
    documents: list[ExtractedDocument],
    sources: list[SourceRegistryRecord],
    case: OutlierCasePackage,
) -> list[EvidenceClaim]:
    """Basic rule-based claim generation from extracted text. LLM expands this in Phase 5."""
    from .ids import stable_id
    claims = []
    identity_terms = [t.lower() for t in [case.symbol, case.instrument] if t]
    event_terms = [t.lower() for t in (case.candidate_terms or []) if t]

    for doc in documents:
        src = next((s for s in sources if s.document_id == doc.document_id), None)
        if src is None:
            continue

        text_lower = (doc.extracted_text + " " + (doc.title or "")).lower()

        mentions_instrument = any(t in text_lower for t in identity_terms) if identity_terms else True
        mentions_event = any(t in text_lower for t in event_terms) if event_terms else False

        if not mentions_instrument:
            continue

        support_score = 0.0
        contradiction_score = 0.0
        confidence = "low"

        if src.source_tier == "tier1_official":
            support_score = 0.85
            confidence = "high"
        elif src.source_tier == "tier2_reputable":
            support_score = 0.65
            confidence = "medium"
        elif mentions_event:
            support_score = 0.4
            confidence = "low"
        else:
            support_score = 0.2

        contradiction_keywords = ["incorrect", "erroneous", "retracted", "correction", "error",
                                   "misreported", "disputed", "false"]
        if any(kw in text_lower for kw in contradiction_keywords):
            contradiction_score = 0.7
            support_score = min(support_score, 0.3)
            confidence = "medium"

        claim_id = stable_id("clm", {"doc": doc.document_id, "case": case.case_id})
        claims.append(
            EvidenceClaim(
                claim_id=claim_id,
                case_id=case.case_id,
                document_id=doc.document_id,
                claim_type="market_context",
                claim_text=doc.excerpt,
                support_score=support_score,
                contradiction_score=contradiction_score,
                confidence=confidence,
                event_type=None,
                citations=[{"document_id": doc.document_id, "excerpt": doc.excerpt[:200]}],
            )
        )

    return claims


def _published_at(url: str, url_to_result: dict[str, SearchResult]) -> str | None:
    r = url_to_result.get(url)
    return r.published_at if r else None


def _collect_limitations(
    checks: list[dict],
    sources: list[SourceRegistryRecord],
    documents: list[ExtractedDocument],
) -> list[str]:
    limitations = []
    if not sources:
        limitations.append("no_sources_registered")
    if not documents:
        limitations.append("no_documents_extracted")
    for c in checks:
        if c.get("status") in ("warn", "fail"):
            limitations.append(f"{c['name']}:{c['status']}")
    return limitations


def _config_dict(cfg: HarnessConfig) -> dict:
    return asdict(cfg)


def _hash_config(cfg: HarnessConfig) -> str:
    raw = json.dumps(_config_dict(cfg), sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── Provider factories ─────────────────────────────────────────────────────────

def _make_search_provider(config: HarnessConfig):
    provider_name = config.search_provider
    if provider_name == "duckduckgo":
        from .search_ddg import DuckDuckGoSearchProvider
        return DuckDuckGoSearchProvider(
            min_delay_sec=config.min_delay_ms_per_domain / 1000.0
        )
    # fallback: fixture
    from .search import FixtureSearchProvider
    return FixtureSearchProvider()


def _make_fetch_provider(config: HarnessConfig):
    provider_name = config.fetch_provider
    if provider_name == "httpx":
        from .fetch_http import HttpxFetchProvider
        return HttpxFetchProvider(
            min_delay_ms=config.min_delay_ms_per_domain,
            allow_domains=config.allow_domains,
            deny_domains=config.deny_domains,
            allowed_schemes=config.allowed_schemes,
        )
    # fallback: fixture
    from .fetch import FixtureFetchProvider
    return FixtureFetchProvider()
