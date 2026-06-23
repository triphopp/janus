"""HarnessCache — file-based cache for search results, fetched pages, and extracts.

Layout::

    <cache_dir>/
      search/<provider>/<query_hash>.json
      pages/<content_hash>.html   (or .txt)
      extracts/<content_hash>.<extractor_version>.json
      llm/<prompt_version>/<provider>/<model>/<input_hash>.json  (Phase 5)

All cache paths use the content or query hash as the filename so the same
content is never stored twice regardless of which case triggered the fetch.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .schema import SearchQuery, SearchResult, FetchResult, ExtractedDocument, to_json_safe


class HarnessCache:
    def __init__(self, cache_dir: str) -> None:
        self.root = Path(cache_dir)

    # ── Search cache ─────────────────────────────────────────────────────────

    def search_path(self, query: SearchQuery) -> Path:
        qhash = query.query_id.replace("query_", "")
        provider_slug = "default"
        return self.root / "search" / provider_slug / f"{qhash}.json"

    def load_search(self, query: SearchQuery) -> list[SearchResult] | None:
        path = self.search_path(query)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [SearchResult(**r) for r in data]
        except (json.JSONDecodeError, TypeError):
            return None

    def save_search(self, query: SearchQuery, results: list[SearchResult], provider: str = "default") -> str:
        path = self.search_path(query)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([to_json_safe(r) for r in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(path)

    # ── Page cache ───────────────────────────────────────────────────────────

    def page_path(self, content_hash: str, ext: str = ".html") -> Path:
        clean = content_hash.replace("sha256:", "")
        return self.root / "pages" / f"{clean}{ext}"

    def load_page(self, content_hash: str) -> bytes | None:
        for ext in (".html", ".txt"):
            path = self.page_path(content_hash, ext)
            if path.exists():
                return path.read_bytes()
        return None

    def save_page(self, content: bytes, content_hash: str, content_type: str | None = None) -> str:
        ext = ".txt" if content_type and "plain" in content_type else ".html"
        path = self.page_path(content_hash, ext)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return str(path)

    def page_path_for_hash(self, content_hash: str) -> Path | None:
        for ext in (".html", ".txt"):
            path = self.page_path(content_hash, ext)
            if path.exists():
                return path
        return None

    # ── Extract cache ─────────────────────────────────────────────────────────

    def extract_path(self, content_hash: str, extractor_version: str) -> Path:
        clean = content_hash.replace("sha256:", "")
        return self.root / "extracts" / f"{clean}.{extractor_version}.json"

    def load_extract(self, content_hash: str, extractor_version: str) -> ExtractedDocument | None:
        path = self.extract_path(content_hash, extractor_version)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ExtractedDocument(**data)
        except (json.JSONDecodeError, TypeError, KeyError):
            return None

    def save_extract(self, doc: ExtractedDocument) -> str:
        path = self.extract_path(doc.content_hash, doc.extraction_version)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(to_json_safe(doc), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(path)

    # ── Manifest entry helpers ────────────────────────────────────────────────

    def search_entry(self, query: SearchQuery, provider: str) -> dict:
        return {
            "type": "search",
            "query_id": query.query_id,
            "query_text": query.text,
            "provider": provider,
            "cache_path": str(self.search_path(query).relative_to(self.root)),
        }

    def page_entry(self, url: str, content_hash: str) -> dict:
        cached_path = self.page_path_for_hash(content_hash)
        rel = str(cached_path.relative_to(self.root)) if cached_path else ""
        return {
            "type": "page",
            "url": url,
            "content_hash": content_hash,
            "cache_path": rel,
        }


# ── Caching provider wrappers ─────────────────────────────────────────────────

class CachingSearchProvider:
    """Wraps any SearchProvider: hit cache first, fall through to inner, write back."""

    def __init__(self, inner: Any, cache: HarnessCache) -> None:
        self._inner = inner
        self._cache = cache
        self.name: str = getattr(inner, "name", "unknown")
        self._entries: list[dict] = []

    def search(
        self,
        query: SearchQuery,
        *,
        max_results: int = 10,
        timeout_sec: int = 15,
    ) -> list[SearchResult]:
        cached = self._cache.load_search(query)
        if cached is not None:
            return cached
        results = self._inner.search(query, max_results=max_results, timeout_sec=timeout_sec)
        self._cache.save_search(query, results, provider=self.name)
        self._entries.append(self._cache.search_entry(query, self.name))
        return results

    def cache_entries(self) -> list[dict]:
        return list(self._entries)


class CachingFetchProvider:
    """Wraps any FetchProvider: save raw content to page cache after fetching."""

    def __init__(self, inner: Any, cache: HarnessCache) -> None:
        self._inner = inner
        self._cache = cache
        self.name: str = getattr(inner, "name", "unknown")
        self._entries: list[dict] = []

    def fetch(self, url: str, *, timeout_sec: int = 15, max_bytes: int = 2_000_000) -> FetchResult:
        result: FetchResult = self._inner.fetch(url, timeout_sec=timeout_sec, max_bytes=max_bytes)
        if result.status_code == 200 and result.text_or_html_path:
            try:
                content = Path(result.text_or_html_path).read_bytes()
                cached_path = self._cache.save_page(content, result.content_hash, result.content_type)
                self._entries.append(self._cache.page_entry(url, result.content_hash))
                # point text_or_html_path at the cache copy
                result = _replace_path(result, cached_path)
            except OSError:
                pass
        return result

    def cache_entries(self) -> list[dict]:
        return list(self._entries)


def _replace_path(fr: FetchResult, new_path: str) -> FetchResult:
    from dataclasses import replace
    return replace(fr, text_or_html_path=new_path)


# ── Replay providers ──────────────────────────────────────────────────────────

class ReplaySearchProvider:
    """Read-only search provider: raises if a query is not in cache."""

    name: str = "replay"

    def __init__(self, cache: HarnessCache) -> None:
        self._cache = cache

    def search(
        self,
        query: SearchQuery,
        *,
        max_results: int = 10,
        timeout_sec: int = 15,
    ) -> list[SearchResult]:
        cached = self._cache.load_search(query)
        if cached is None:
            raise ReplayCacheMiss(f"search cache miss for query_id={query.query_id!r} text={query.text!r}")
        return cached[:max_results]


class ReplayFetchProvider:
    """Read-only fetch provider: reconstructs FetchResult from page cache."""

    name: str = "replay"

    def __init__(self, cache: HarnessCache, manifest_entries: list[dict]) -> None:
        self._cache = cache
        self._url_to_hash: dict[str, str] = {
            e["url"]: e["content_hash"]
            for e in manifest_entries
            if e.get("type") == "page"
        }

    def fetch(self, url: str, *, timeout_sec: int = 15, max_bytes: int = 2_000_000) -> FetchResult:
        from datetime import datetime, timezone
        import hashlib as _hl

        content_hash = self._url_to_hash.get(url)
        if content_hash is None:
            return FetchResult(
                fetch_id=_fid(url), url=url, final_url=url,
                status_code=404, fetched_at=datetime.now(timezone.utc).isoformat(),
                bytes_read=0, content_hash="",
                blocked_reason="replay: url not in manifest",
            )

        cached_path = self._cache.page_path_for_hash(content_hash)
        if cached_path is None:
            raise ReplayCacheMiss(f"page cache miss for url={url!r} hash={content_hash!r}")

        content = cached_path.read_bytes()
        ext = cached_path.suffix
        return FetchResult(
            fetch_id=_fid(url), url=url, final_url=url,
            status_code=200,
            content_type="text/html" if ext == ".html" else "text/plain",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            bytes_read=len(content),
            content_hash=content_hash,
            text_or_html_path=str(cached_path),
        )


def _fid(url: str) -> str:
    return "fetch_" + hashlib.sha256(url.encode()).hexdigest()[:16]


class ReplayCacheMiss(RuntimeError):
    """Raised when replay mode requires a cache entry that does not exist."""
