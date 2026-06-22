"""DuckDuckGoSearchProvider — live search via ddgs (formerly duckduckgo_search).

No API key required. Rate-limits by inserting a sleep between consecutive calls.
Used only when config.mode == "live" and config.search_provider == "duckduckgo".

Citation safety: every SearchResult gets a deterministic result_id derived from
the URL so document IDs produced downstream are stable across identical queries.
"""

from __future__ import annotations

import hashlib
import time

# The package was renamed from duckduckgo_search → ddgs; support both during transition.
try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS  # type: ignore[no-redef]

from .schema import SearchQuery, SearchResult


_LAST_CALL: dict[str, float] = {}
_MIN_DELAY_SEC = 1.0  # DDG fair-use: 1 request/sec per process


class DuckDuckGoSearchProvider:
    """Search the web via DuckDuckGo using the duckduckgo_search library."""

    name: str = "duckduckgo"

    def __init__(self, *, min_delay_sec: float = _MIN_DELAY_SEC) -> None:
        self._min_delay_sec = min_delay_sec

    def search(
        self,
        query: SearchQuery,
        *,
        max_results: int = 10,
        timeout_sec: int = 15,
    ) -> list[SearchResult]:
        self._rate_limit()

        try:
            with DDGS() as ddgs:
                raw_results = list(
                    ddgs.text(
                        query.text,
                        max_results=max_results,
                        timelimit=None,
                    )
                )[:max_results]
        except Exception:
            # DDG may raise on rate-limit or network error — degrade gracefully.
            return []

        results: list[SearchResult] = []
        for rank, item in enumerate(raw_results, start=1):
            url = item.get("href") or item.get("url") or ""
            if not url:
                continue
            domain = _extract_domain(url)
            title = (item.get("title") or "").strip()
            snippet = (item.get("body") or "").strip()

            result_id = _result_id(query.query_id, url, rank)
            results.append(
                SearchResult(
                    query_id=query.query_id,
                    result_id=result_id,
                    provider="duckduckgo",
                    rank=rank,
                    title=title,
                    url=url,
                    domain=domain,
                    snippet=snippet,
                    published_at=None,
                )
            )

        _LAST_CALL["global"] = time.monotonic()
        return results

    def _rate_limit(self) -> None:
        last = _LAST_CALL.get("global", 0.0)
        wait = self._min_delay_sec - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _result_id(query_id: str, url: str, rank: int) -> str:
    payload = f"{query_id}|{url}|{rank}".encode()
    return "res_" + hashlib.sha256(payload).hexdigest()[:16]


def _extract_domain(url: str) -> str:
    try:
        return url.split("://", 1)[1].split("/")[0].lower().lstrip("www.")
    except IndexError:
        return ""
