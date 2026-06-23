"""Citation verifier — ensures LLM summaries cite only registered documents.

All sources must be fetched and registered before they can be cited.
This prevents citation hallucination structurally.
"""

from __future__ import annotations

from .schema import SourceRegistryRecord


def verify_citations(
    *,
    summary: dict,
    registry: list[SourceRegistryRecord],
    verdict: str,
) -> dict:
    """Verify LLM summary citations against the source registry.

    Returns a citation report dict with status: pass | warn | fail.
    """
    if not summary:
        return {
            "status": "skip",
            "reason": "no llm summary to verify",
            "missing_document_ids": [],
            "unfetched_urls": [],
            "source_mismatches": [],
            "supporting_registered_sources": 0,
            "blocking_reason": None,
        }

    registry_by_doc: dict[str, SourceRegistryRecord] = {s.document_id: s for s in registry}
    registry_urls: set[str] = {s.url for s in registry} | {s.final_url for s in registry}

    supporting_ids: list[str] = summary.get("supporting_document_ids", [])
    contradicting_ids: list[str] = summary.get("contradicting_document_ids", [])
    all_cited_ids = set(supporting_ids) | set(contradicting_ids)

    missing_document_ids = [d for d in all_cited_ids if d not in registry_by_doc]
    unfetched_urls: list[str] = []
    source_mismatches: list[dict] = []

    for url in summary.get("source_urls", []):
        if url and url not in registry_urls:
            unfetched_urls.append(url)

    for doc_id in supporting_ids:
        if doc_id in registry_by_doc:
            rec = registry_by_doc[doc_id]
            summary_title = summary.get("source_titles", {}).get(doc_id)
            if summary_title and summary_title != rec.title:
                source_mismatches.append({
                    "document_id": doc_id,
                    "registry_title": rec.title,
                    "llm_title": summary_title,
                    "flag": "llm_source_mismatch",
                })

    supporting_registered = len([d for d in supporting_ids if d in registry_by_doc])

    blocking_reason = None
    if missing_document_ids:
        blocking_reason = f"cited document_ids not in registry: {missing_document_ids}"
    elif unfetched_urls:
        blocking_reason = f"LLM introduced unfetched URLs: {unfetched_urls}"
    elif verdict == "supported_event" and supporting_registered == 0:
        blocking_reason = "supported_event requires at least one registered supporting source"

    if blocking_reason:
        status = "fail"
    elif source_mismatches or (
        verdict == "supported_event" and supporting_registered < 1
    ):
        status = "warn"
    else:
        status = "pass"

    return {
        "status": status,
        "missing_document_ids": missing_document_ids,
        "unfetched_urls": unfetched_urls,
        "source_mismatches": source_mismatches,
        "supporting_registered_sources": supporting_registered,
        "blocking_reason": blocking_reason,
    }
