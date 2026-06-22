"""SearchProvider — fixture implementation for Phase 1/tests.

Real providers (SearxngSearchProvider, ApiSearchProvider) are added in Phase 4.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .schema import SearchQuery, SearchResult


class SearchProvider(Protocol):
    name: str

    def search(
        self,
        query: SearchQuery,
        *,
        max_results: int = 10,
        timeout_sec: int = 15,
    ) -> list[SearchResult]:
        ...


@dataclass
class FixtureSearchProvider:
    """Returns pre-baked SearchResults from a fixture index file.

    The fixture index is a JSON file at ``fixture_dir/fixture_index.json``
    with shape::

        {
          "<normalized query text>": [
            {
              "title": "...",
              "url": "...",
              "snippet": "...",
              "published_at": "2024-01-25T00:00:00Z",
              "domain": "example.com"
            }
          ]
        }

    Queries that have no entry return an empty list (no error).
    """

    name: str = "fixture"
    fixture_dir: str = "tests/fixtures/evidence_harness/search"

    def search(
        self,
        query: SearchQuery,
        *,
        max_results: int = 10,
        timeout_sec: int = 15,
    ) -> list[SearchResult]:
        index = self._load_index()
        normalized = _normalize_query(query.text)
        entries = index.get(normalized, [])[:max_results]

        results = []
        for rank, entry in enumerate(entries, start=1):
            url = entry.get("url", "")
            raw_payload = {"title": entry.get("title"), "url": url, "rank": rank}
            result_id = _result_id(query.query_id, url, rank)
            results.append(
                SearchResult(
                    query_id=query.query_id,
                    result_id=result_id,
                    provider=self.name,
                    rank=rank,
                    title=entry.get("title", ""),
                    url=url,
                    snippet=entry.get("snippet"),
                    published_at=entry.get("published_at"),
                    domain=entry.get("domain", _domain_from(url)),
                    raw=raw_payload,
                )
            )
        return results

    def _load_index(self) -> dict:
        path = Path(self.fixture_dir) / "fixture_index.json"
        if path.exists():
            return json.loads(path.read_text())
        return {}


def _normalize_query(text: str) -> str:
    import re
    return re.sub(r"\s+", " ", text.lower().strip())


def _result_id(query_id: str, url: str, rank: int) -> str:
    payload = f"{query_id}|{url}|{rank}"
    return "res_" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def _domain_from(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc
    except Exception:
        return ""
