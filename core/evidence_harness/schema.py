"""Core data contracts for the Evidence Search Harness.

All types are serializable to/from JSON. Use dataclasses throughout;
Pydantic is not a dependency of this project.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from typing import Any


# ── Helpers ──────────────────────────────────────────────────────────────────

def _clean(v: Any) -> Any:
    """Replace NaN/Inf with None for JSON safety."""
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def to_json_safe(obj: Any) -> Any:
    """Recursively sanitize a dataclass or dict for JSON serialization."""
    if hasattr(obj, "__dataclass_fields__"):
        return {k: to_json_safe(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_json_safe(v) for v in obj]
    return _clean(obj)


# ── Case input ────────────────────────────────────────────────────────────────

@dataclass
class OutlierCasePackage:
    case_id: str
    run_id: str
    signal_type: str
    as_of_date: str

    family: str | None = None
    instrument: str | None = None
    symbol: str | None = None
    observed_at: str | None = None
    severity: str | None = None
    metric_name: str | None = None
    observed_value: float | int | str | None = None
    baseline_value: float | int | str | None = None
    z_score: float | None = None
    pct_change: float | None = None

    local_context: dict = field(default_factory=dict)
    protected_columns: list[str] = field(default_factory=list)
    candidate_terms: list[str] = field(default_factory=list)
    source_hints: list[str] = field(default_factory=list)

    def validate(self) -> None:
        required = ("case_id", "run_id", "as_of_date", "signal_type")
        for f in required:
            if not getattr(self, f):
                raise ValueError(f"OutlierCasePackage missing required field: {f}")

    @classmethod
    def from_dict(cls, d: dict) -> "OutlierCasePackage":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ── Search ────────────────────────────────────────────────────────────────────

@dataclass
class SearchQuery:
    query_id: str
    case_id: str
    text: str
    date_start: str | None = None
    date_end: str | None = None
    domains: list[str] = field(default_factory=list)
    reason: str = ""
    priority: int = 0
    template_id: str = ""
    evidence_goal: str = ""


@dataclass
class SearchResult:
    query_id: str
    result_id: str
    provider: str
    rank: int
    title: str
    url: str
    snippet: str | None = None
    published_at: str | None = None
    domain: str = ""
    raw: dict = field(default_factory=dict)


# ── Fetch ─────────────────────────────────────────────────────────────────────

@dataclass
class FetchResult:
    fetch_id: str
    url: str
    final_url: str
    status_code: int
    fetched_at: str
    bytes_read: int
    content_hash: str
    content_type: str | None = None
    text_or_html_path: str | None = None
    blocked_reason: str | None = None
    raw_headers: dict = field(default_factory=dict)


# ── Extract ───────────────────────────────────────────────────────────────────

@dataclass
class ExtractedDocument:
    document_id: str
    fetch_id: str
    canonical_url: str
    domain: str
    accessed_at: str
    extracted_text: str
    excerpt: str
    content_hash: str
    extraction_version: str
    source_tier: str = "unknown"
    title: str | None = None
    author: str | None = None
    published_at: str | None = None
    language: str | None = None
    metadata: dict = field(default_factory=dict)


# ── Claims ────────────────────────────────────────────────────────────────────

@dataclass
class EvidenceClaim:
    claim_id: str
    case_id: str
    document_id: str
    claim_type: str
    claim_text: str
    support_score: float = 0.0
    contradiction_score: float = 0.0
    confidence: str = "low"
    event_type: str | None = None
    event_time: str | None = None
    instrument: str | None = None
    citations: list[dict] = field(default_factory=list)
    llm_generated: bool = False


# ── Source registry ───────────────────────────────────────────────────────────

@dataclass
class SourceRegistryRecord:
    document_id: str
    source_id: str
    url: str
    final_url: str
    domain: str
    source_tier: str
    fetched_at: str
    accessed_at: str
    content_hash: str
    extract_hash: str
    provider: str
    schema_version: str = "evidence.source_registry.v1"
    canonical_url: str | None = None
    title: str | None = None
    published_at: str | None = None
    excerpt_hashes: list[str] = field(default_factory=list)
    query_ids: list[str] = field(default_factory=list)
    cache_paths: dict = field(default_factory=dict)


# ── Run result ────────────────────────────────────────────────────────────────

@dataclass
class HarnessRunResult:
    case_id: str
    run_id: str
    harness_run_id: str
    status: str
    verdict: str
    confidence: str

    queries: list[SearchQuery] = field(default_factory=list)
    search_results: list[SearchResult] = field(default_factory=list)
    fetched: list[FetchResult] = field(default_factory=list)
    documents: list[ExtractedDocument] = field(default_factory=list)
    sources: list = field(default_factory=list)   # list[SourceRegistryRecord]
    claims: list[EvidenceClaim] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    artifact_paths: dict = field(default_factory=dict)
    audit: dict = field(default_factory=dict)
