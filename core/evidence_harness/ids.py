"""Deterministic ID generation for cases, queries, sources, and documents.

All IDs are stable: same inputs always produce the same ID.
Uses SHA-256 over canonical JSON (sorted keys, UTF-8, no insignificant whitespace).
"""

from __future__ import annotations

import hashlib
import json


def canonical_json(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def stable_id(prefix: str, payload: dict, length: int = 16) -> str:
    body = canonical_json(payload)
    return f"{prefix}_{hashlib.sha256(body).hexdigest()[:length]}"


def case_id(run_id: str, signal_type: str, as_of_date: str, metric_name: str,
            family: str | None = None, symbol: str | None = None,
            instrument: str | None = None, identity_key: str | None = None) -> str:
    payload: dict = {
        "run_id": run_id,
        "signal_type": signal_type,
        "family": family or "",
        "as_of_date": as_of_date,
        "metric_name": metric_name or "",
    }
    if symbol:
        payload["symbol"] = symbol
    elif instrument:
        payload["instrument"] = instrument
    elif identity_key:
        payload["identity_key"] = identity_key
    return stable_id("case", payload)


def query_id(case_id_val: str, text: str, date_start: str | None,
             date_end: str | None, domains: list[str]) -> str:
    payload = {
        "case_id": case_id_val,
        "text": text.lower().strip(),
        "date_start": date_start or "",
        "date_end": date_end or "",
        "domains": sorted(domains),
    }
    return stable_id("query", payload)


def source_id(canonical_url: str, content_hash: str) -> str:
    payload = {"canonical_url": canonical_url, "content_hash": content_hash}
    return stable_id("src", payload)


def document_id(src_id: str, extract_hash: str, extraction_version: str) -> str:
    payload = {
        "source_id": src_id,
        "extract_hash": extract_hash,
        "extraction_version": extraction_version,
    }
    return stable_id("doc", payload)
