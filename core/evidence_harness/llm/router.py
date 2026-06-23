"""LLMRouter — builds the correct LLMClient from HarnessConfig.

Also houses the three high-level LLM task functions used by the controller:
  - run_claim_extraction(document, case, registry, client) -> list[EvidenceClaim]
  - run_query_expansion(case, existing_queries, budget, client) -> list[str]
  - run_evidence_summary(case, verdict, claims, checks, registry, client) -> dict

All three validate the LLM response before returning — no raw output escapes this module.
"""

from __future__ import annotations

import json
from typing import Any

from ..schema import ExtractedDocument, EvidenceClaim, SourceRegistryRecord, OutlierCasePackage
from ..config import HarnessConfig
from ..ids import stable_id
from .client import LLMClient, LLMJsonError


# ── Router ─────────────────────────────────────────────────────────────────────

def build_llm_client(config: HarnessConfig) -> LLMClient:
    """Instantiate the correct LLM provider from config."""
    provider = getattr(config, "llm_provider", "mock")
    model = getattr(config, "llm_model", "mock-v1")
    base_url = getattr(config, "llm_base_url", "http://localhost:11434")
    api_key = getattr(config, "llm_api_key", None)
    timeout = getattr(config, "llm_timeout_sec", 60)

    if provider == "mock":
        from .providers.mock import MockLLMClient
        return MockLLMClient(model=model)
    if provider == "ollama":
        from .providers.ollama import OllamaClient
        return OllamaClient(base_url=base_url, model=model, timeout_sec=timeout)
    if provider == "openai_compat":
        from .providers.openai_compat import OpenAICompatClient
        return OpenAICompatClient(
            base_url=base_url, api_key=api_key, model=model, timeout_sec=timeout
        )
    raise ValueError(f"Unknown llm_provider: {provider!r}. Must be mock | ollama | openai_compat")


# ── Claim extraction ──────────────────────────────────────────────────────────

def run_claim_extraction(
    document: ExtractedDocument,
    case: OutlierCasePackage,
    registry: list[SourceRegistryRecord],
    client: LLMClient,
) -> list[EvidenceClaim]:
    """Ask the LLM to extract claims from a single document.

    Citation safety: the LLM is given the full list of registered document_ids
    and any doc_id it cites that isn't in the registry is stripped out.
    """
    from .prompts import claim_extraction_messages

    registered_ids = [r.document_id for r in registry]
    case_ctx = _case_context(case)
    messages = claim_extraction_messages(
        document_text=document.extracted_text,
        document_id=document.document_id,
        case_context=case_ctx,
        registered_document_ids=registered_ids,
    )

    try:
        response = client.complete_json(messages, schema={})
    except (LLMJsonError, Exception):
        return []

    # Accept "claims" or common fallback keys some models use ("analysis", "results", "items")
    raw_claims = (
        response.get("claims")
        or response.get("analysis")
        or response.get("results")
        or response.get("items")
        or []
    )
    claims: list[EvidenceClaim] = []
    for item in raw_claims:
        # Strip hallucinated document_ids
        cited = [d for d in item.get("document_ids", []) if d in registered_ids]
        claim_id = stable_id("clm", {"doc": document.document_id, "case": case.case_id,
                                      "text": item.get("claim_text", "")[:80]})
        try:
            claims.append(EvidenceClaim(
                claim_id=claim_id,
                case_id=case.case_id,
                document_id=document.document_id,
                claim_type=item.get("claim_type", "market_context"),
                claim_text=str(item.get("claim_text") or item.get("claim") or "")[:500],
                support_score=float(item.get("support_score", 0.0)),
                contradiction_score=float(item.get("contradiction_score", 0.0)),
                confidence=item.get("confidence", "low"),
                event_type=item.get("event_type"),
                citations=[{"document_id": d} for d in cited],
                llm_generated=True,
            ))
        except (TypeError, ValueError):
            continue
    return claims


# ── Query expansion ───────────────────────────────────────────────────────────

def run_query_expansion(
    case: OutlierCasePackage,
    existing_queries: list[str],
    budget_remaining: int,
    date_start: str | None,
    date_end: str | None,
    client: LLMClient,
    validate_fn=None,
) -> list[str]:
    """Ask the LLM to propose additional search queries within the date window.

    validate_fn: optional callable(query_text, case, existing_normalized) -> bool
    Uses planner.validate_llm_expansion when not provided.
    """
    from .prompts import query_expansion_messages

    if budget_remaining <= 0:
        return []

    messages = query_expansion_messages(
        case_context=_case_context(case),
        existing_queries=existing_queries,
        budget_remaining=budget_remaining,
        date_start=date_start,
        date_end=date_end,
    )

    try:
        response = client.complete_json(messages, schema={})
    except (LLMJsonError, Exception):
        return []

    raw_queries = response.get("queries", [])
    result: list[str] = []
    existing_norm = {_norm(q) for q in existing_queries}

    for item in raw_queries:
        text = str(item.get("text", "")).strip()
        if not text or len(text) > 200:
            continue
        if _norm(text) in existing_norm:
            continue
        if validate_fn is not None:
            try:
                if not validate_fn(text, case, existing_norm):
                    continue
            except Exception:
                continue
        existing_norm.add(_norm(text))
        result.append(text)

    return result[:budget_remaining]


# ── Evidence summary ──────────────────────────────────────────────────────────

def run_evidence_summary(
    case: OutlierCasePackage,
    verdict: str,
    confidence: float,
    claims: list[EvidenceClaim],
    checks: list[dict],
    registry: list[SourceRegistryRecord],
    client: LLMClient,
) -> dict:
    """Ask the LLM to write a human-readable evidence summary.

    Citation safety: cited_document_ids in the response are filtered to only
    include IDs that exist in the registry.
    """
    from .prompts import evidence_summary_messages
    from ..citations import verify_citations

    registered_ids = [r.document_id for r in registry]
    claims_text = [c.claim_text for c in claims if c.claim_text]
    check_summaries = [
        f"{c['name']}: {c['status']} (score={c.get('score', '?')})"
        for c in checks
    ]

    messages = evidence_summary_messages(
        case_context=_case_context(case),
        verdict=verdict,
        confidence=confidence,
        claims_text=claims_text,
        check_summaries=check_summaries,
        registered_document_ids=registered_ids,
    )

    try:
        response = client.complete_json(messages, schema={})
    except (LLMJsonError, Exception):
        return {
            "summary": "",
            "key_findings": [],
            "limitations": ["llm_unavailable"],
            "supporting_document_ids": [],
            "contradicting_document_ids": [],
            "cited_document_ids": [],
            "llm_error": True,
        }

    # Normalize: prefer supporting_document_ids; fall back to legacy cited_document_ids.
    raw_supporting = (
        response.get("supporting_document_ids")
        or response.get("cited_document_ids", [])
    )
    raw_contradicting = response.get("contradicting_document_ids", [])

    # Strip hallucinated citations — only IDs that exist in the registry.
    supporting = [d for d in raw_supporting if d in registered_ids]
    contradicting = [d for d in raw_contradicting if d in registered_ids]

    return {
        "summary": str(response.get("summary", ""))[:1000],
        "key_findings": [str(f)[:200] for f in response.get("key_findings", [])[:5]],
        "limitations": [str(l)[:200] for l in response.get("limitations", [])[:5]],
        "supporting_document_ids": supporting,
        "contradicting_document_ids": contradicting,
        "cited_document_ids": supporting,  # legacy alias for read-side compat
        "llm_error": False,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _case_context(case: OutlierCasePackage) -> dict:
    return {
        "symbol": case.symbol or "",
        "instrument": case.instrument or "",
        "signal_type": case.signal_type or "",
        "as_of_date": case.as_of_date or "",
        "z_score": case.z_score,
        "direction": case.severity or "",
    }


def _norm(text: str) -> str:
    import re
    return re.sub(r"\s+", " ", text.lower().strip())
