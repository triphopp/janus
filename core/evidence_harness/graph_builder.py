"""graph_builder — convert HarnessRunResult into the evidence graph payload.

Produces a dict with keys: case, nodes, edges, checks, queries, timeline.
This is pure computation — no I/O, no DB, no filesystem.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from .schema import HarnessRunResult, SourceRegistryRecord, EvidenceClaim, ExtractedDocument


# ── Public entry point ────────────────────────────────────────────────────────

def build_graph(result: HarnessRunResult) -> dict:
    """Convert a HarnessRunResult into the graph payload dict.

    Returns::

        {
          "case":    {...},          # evidence_cases row
          "sources": [...],          # evidence_sources rows (deduped by source_id)
          "nodes":   [...],          # evidence_nodes rows
          "edges":   [...],          # evidence_edges rows
          "checks":  [...],          # evidence_checks rows
          "queries": [...],          # evidence_search_queries rows
          "timeline": [...],         # sorted node list for UI
          "audit":   {...},
        }
    """
    case_row = _build_case(result)
    source_rows = _build_sources(result)
    nodes = _build_nodes(result)
    edges = _build_edges(result, nodes)
    check_rows = _build_checks(result)
    query_rows = _build_queries(result)
    timeline = _build_timeline(nodes)

    return {
        "case": case_row,
        "sources": source_rows,
        "nodes": nodes,
        "edges": edges,
        "checks": check_rows,
        "queries": query_rows,
        "timeline": timeline,
        "audit": {
            "schema_version": "evidence.case.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "harness_run_id": result.harness_run_id,
        },
    }


# ── Case row ─────────────────────────────────────────────────────────────────

def _build_case(result: HarnessRunResult) -> dict:
    from .schema import OutlierCasePackage
    return {
        "case_id": result.case_id,
        "run_id": result.run_id,
        "instrument": _first_attr(result, "instrument"),
        "family": _first_attr(result, "family"),
        "as_of_date": _first_attr(result, "as_of_date"),
        "signal_type": _first_attr(result, "signal_type"),
        "severity": _first_attr(result, "severity"),
        "status": "unreviewed",
        "verdict": result.verdict,
        "confidence": result.confidence,
        "event_type": _dominant_event_type(result.claims),
        "artifact_path": result.artifact_paths.get("verdict"),
        "payload": {
            "harness_run_id": result.harness_run_id,
            "limitations": result.limitations,
            "z_score": _first_attr(result, "z_score"),
            "observed_value": _first_attr(result, "observed_value"),
        },
    }


# ── Sources ───────────────────────────────────────────────────────────────────

def _build_sources(result: HarnessRunResult) -> list[dict]:
    seen: set[str] = set()
    rows: list[dict] = []
    doc_map = {d.document_id: d for d in result.documents}

    for src in result.sources:
        if src.source_id in seen:
            continue
        seen.add(src.source_id)
        doc = doc_map.get(src.document_id)
        rows.append({
            "source_id": src.source_id,
            "url": src.url,
            "canonical_url": src.final_url or src.url,
            "domain": src.domain,
            "source_tier": src.source_tier,
            "title": src.title,
            "published_at": _parse_dt(src.published_at),
            "fetched_at": _parse_dt(src.fetched_at),
            "accessed_at": _parse_dt(src.accessed_at),
            "content_hash": src.content_hash,
            "extracted_text": doc.extracted_text if doc else None,
            "summary": doc.excerpt if doc else None,
            "payload": {
                "extract_hash": src.extract_hash,
                "provider": src.provider,
                "query_ids": src.query_ids,
                "document_id": src.document_id,
            },
        })
    return rows


# ── Nodes ─────────────────────────────────────────────────────────────────────

_TIER_TO_NODE_TYPE = {
    "tier1_official": "filing",
    "tier2_reputable": "news_article",
    "tier3_general": "news_article",
    "tier4_social": "news_article",
    "unknown": "news_article",
}


def _build_nodes(result: HarnessRunResult) -> list[dict]:
    nodes: list[dict] = []

    # Root outlier node
    nodes.append({
        "node_id": f"node_root_{result.case_id}",
        "case_id": result.case_id,
        "source_id": None,
        "node_type": "outlier",
        "source_tier": None,
        "title": f"Outlier: {result.case_id}",
        "observed_at": _first_attr(result, "as_of_date"),
        "published_at": None,
        "effective_at": None,
        "confidence": None,
        "summary": f"signal_type={_first_attr(result, 'signal_type')} z_score={_first_attr(result, 'z_score')}",
        "payload": {
            "z_score": _first_attr(result, "z_score"),
            "observed_value": _first_attr(result, "observed_value"),
            "signal_type": _first_attr(result, "signal_type"),
        },
    })

    # Source nodes
    src_map = {s.document_id: s for s in result.sources}
    doc_map = {d.document_id: d for d in result.documents}

    for src in result.sources:
        doc = doc_map.get(src.document_id)
        node_type = _TIER_TO_NODE_TYPE.get(src.source_tier or "unknown", "news_article")
        # Macro releases from tier1 domains like eia.gov / cmegroup.com
        if src.source_tier == "tier1_official":
            domain = src.domain or ""
            if any(d in domain for d in ("eia.gov", "opec.org", "cmegroup.com")):
                node_type = "macro_release"
            else:
                node_type = "filing"

        claim_support = _max_support(src.document_id, result.claims)
        nodes.append({
            "node_id": f"node_{src.source_id}",
            "case_id": result.case_id,
            "source_id": src.source_id,
            "node_type": node_type,
            "source_tier": src.source_tier,
            "title": src.title,
            "observed_at": None,
            "published_at": _parse_dt(src.published_at),
            "effective_at": None,
            "confidence": claim_support,
            "summary": doc.excerpt if doc else None,
            "payload": {
                "url": src.url,
                "domain": src.domain,
                "document_id": src.document_id,
                "provider": src.provider,
            },
        })

    return nodes


# ── Edges ─────────────────────────────────────────────────────────────────────

def _build_edges(result: HarnessRunResult, nodes: list[dict]) -> list[dict]:
    edges: list[dict] = []
    root_id = f"node_root_{result.case_id}"
    src_map = {s.document_id: s for s in result.sources}
    node_ids = {n["node_id"] for n in nodes}

    for claim in result.claims:
        src = src_map.get(claim.document_id)
        if src is None:
            continue
        src_node_id = f"node_{src.source_id}"
        if src_node_id not in node_ids:
            continue

        if claim.support_score >= 0.5:
            relation = "supports"
            confidence = claim.support_score
            check_name = "source_quality"
        elif claim.contradiction_score >= 0.5:
            relation = "contradicts"
            confidence = claim.contradiction_score
            check_name = "contradiction_detection"
        else:
            continue

        edge_id = _edge_id(src_node_id, root_id, relation)
        edges.append({
            "edge_id": edge_id,
            "case_id": result.case_id,
            "from_node": src_node_id,
            "to_node": root_id,
            "relation": relation,
            "confidence": confidence,
            "check_name": check_name,
            "rationale": claim.claim_text[:300] if claim.claim_text else None,
            "payload": {
                "claim_id": claim.claim_id,
                "claim_type": claim.claim_type,
                "event_type": claim.event_type,
                "llm_generated": claim.llm_generated,
            },
        })

    # Temporal edges from checks
    temporal_check = next(
        (c for c in result.checks if c.get("name") == "temporal_consistency"), None
    )
    if temporal_check:
        for src in result.sources:
            src_node_id = f"node_{src.source_id}"
            if src_node_id not in node_ids or not src.published_at:
                continue
            relation = "temporally_precedes" if temporal_check.get("status") == "pass" else "temporally_follows"
            edge_id = _edge_id(src_node_id, root_id, relation)
            edges.append({
                "edge_id": edge_id,
                "case_id": result.case_id,
                "from_node": src_node_id,
                "to_node": root_id,
                "relation": relation,
                "confidence": temporal_check.get("score"),
                "check_name": "temporal_consistency",
                "rationale": temporal_check.get("rationale"),
                "payload": {},
            })

    return edges


# ── Checks ────────────────────────────────────────────────────────────────────

def _build_checks(result: HarnessRunResult) -> list[dict]:
    rows = []
    for i, chk in enumerate(result.checks):
        check_id = _stable_id("chk", {"case_id": result.case_id, "name": chk.get("name", ""), "i": i})
        rows.append({
            "check_id": check_id,
            "case_id": result.case_id,
            "name": chk.get("name", ""),
            "status": chk.get("status", "unknown"),
            "score": chk.get("score"),
            "rationale": chk.get("rationale"),
            "payload": {k: v for k, v in chk.items()
                        if k not in ("name", "status", "score", "rationale")},
        })
    return rows


# ── Queries ───────────────────────────────────────────────────────────────────

def _build_queries(result: HarnessRunResult) -> list[dict]:
    rows = []
    for q in result.queries:
        rows.append({
            "query_id": q.query_id,
            "case_id": result.case_id,
            "query": q.text,
            "provider": None,
            "window_start": q.date_start,
            "window_end": q.date_end,
            "result_count": len([r for r in result.search_results
                                  if r.query_id == q.query_id]),
            "payload": {},
        })
    return rows


# ── Timeline ──────────────────────────────────────────────────────────────────

_NODE_TYPE_ORDER = {
    "outlier": 0,
    "macro_release": 2,
    "filing": 3,
    "news_article": 4,
    "llm_summary": 6,
    "human_decision": 7,
}

_TIER_ORDER = {
    "tier1_official": 0,
    "tier2_reputable": 1,
    "tier3_general": 2,
    "tier4_social": 3,
    "unknown": 4,
}


def _build_timeline(nodes: list[dict]) -> list[dict]:
    def _sort_key(n: dict):
        ts = n.get("published_at") or n.get("observed_at") or n.get("effective_at") or ""
        type_ord = _NODE_TYPE_ORDER.get(n.get("node_type", ""), 5)
        tier_ord = _TIER_ORDER.get(n.get("source_tier") or "", 4)
        conf = -(n.get("confidence") or 0.0)
        return (type_ord, ts, tier_ord, conf, n.get("node_id", ""))

    return sorted(nodes, key=_sort_key)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _first_attr(result: HarnessRunResult, attr: str):
    """Pull an attribute from result.audit or case payload."""
    return result.audit.get(attr) if result.audit else None


def _dominant_event_type(claims) -> str | None:
    counts: dict[str, int] = {}
    for c in claims:
        if c.event_type:
            counts[c.event_type] = counts.get(c.event_type, 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.__getitem__)


def _max_support(doc_id: str, claims) -> float | None:
    scores = [c.support_score for c in claims if c.document_id == doc_id and c.support_score]
    return max(scores) if scores else None


def _parse_dt(value: str | None) -> str | None:
    if not value:
        return None
    return value


def _edge_id(from_node: str, to_node: str, relation: str) -> str:
    return _stable_id("edge", {"f": from_node, "t": to_node, "r": relation})


def _stable_id(prefix: str, payload: dict) -> str:
    import json
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return f"{prefix}_{hashlib.sha256(raw).hexdigest()[:16]}"
