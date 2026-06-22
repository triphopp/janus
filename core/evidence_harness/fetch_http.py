"""HttpxFetchProvider — live HTTP fetch using httpx with rate-limit and domain policy.

Used only when config.mode == "live" and config.fetch_provider == "httpx".
Import-guarded so tests that don't need live HTTP never pay the httpx import cost.
"""

from __future__ import annotations

import hashlib
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import FetchResult


_PER_DOMAIN_LAST_FETCH: dict[str, float] = {}


class HttpxFetchProvider:
    """Fetch a URL over HTTPS with rate-limiting, size cap, and domain allow/deny policy."""

    name: str = "httpx"

    def __init__(
        self,
        *,
        min_delay_ms: int = 1000,
        allow_domains: list[str] | None = None,
        deny_domains: list[str] | None = None,
        allowed_schemes: list[str] | None = None,
    ) -> None:
        self._min_delay_ms = min_delay_ms
        self._allow_domains: list[str] = allow_domains or []
        self._deny_domains: list[str] = deny_domains or []
        self._allowed_schemes: set[str] = set(allowed_schemes or ["https"])

    # ── Public interface ──────────────────────────────────────────────────────

    def fetch(
        self, url: str, *, timeout_sec: int = 15, max_bytes: int = 2_000_000
    ) -> FetchResult:
        import httpx

        fetch_id = _fid(url)
        fetched_at = datetime.now(timezone.utc).isoformat()

        # ── Scheme check ──────────────────────────────────────────────────────
        scheme = url.split("://")[0].lower() if "://" in url else ""
        if scheme not in self._allowed_schemes:
            return FetchResult(
                fetch_id=fetch_id, url=url, final_url=url,
                status_code=0, fetched_at=fetched_at, bytes_read=0, content_hash="",
                blocked_reason=f"scheme not allowed: {scheme!r}",
            )

        # ── Domain check ─────────────────────────────────────────────────────
        domain = _extract_domain(url)
        blocked = self._check_domain_policy(domain)
        if blocked:
            return FetchResult(
                fetch_id=fetch_id, url=url, final_url=url,
                status_code=0, fetched_at=fetched_at, bytes_read=0, content_hash="",
                blocked_reason=blocked,
            )

        # ── Rate limit ────────────────────────────────────────────────────────
        self._rate_limit(domain)

        # ── HTTP fetch ────────────────────────────────────────────────────────
        try:
            with httpx.Client(follow_redirects=True, timeout=timeout_sec) as client:
                response = client.get(url, headers=_HEADERS)

            final_url = str(response.url)
            status_code = response.status_code
            content_type = response.headers.get("content-type", "")

            if status_code != 200:
                return FetchResult(
                    fetch_id=fetch_id, url=url, final_url=final_url,
                    status_code=status_code, fetched_at=fetched_at,
                    bytes_read=0, content_hash="",
                    blocked_reason=f"HTTP {status_code}",
                )

            raw = response.content[:max_bytes]
            content_hash = "sha256:" + hashlib.sha256(raw).hexdigest()

            # Write to temp file so the rest of the pipeline can use a path
            suffix = ".html" if "html" in content_type else ".txt"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            tmp.write(raw)
            tmp.close()

            return FetchResult(
                fetch_id=fetch_id, url=url, final_url=final_url,
                status_code=200, content_type=content_type,
                fetched_at=fetched_at,
                bytes_read=len(raw),
                content_hash=content_hash,
                text_or_html_path=tmp.name,
            )

        except httpx.TimeoutException:
            return FetchResult(
                fetch_id=fetch_id, url=url, final_url=url,
                status_code=0, fetched_at=fetched_at, bytes_read=0, content_hash="",
                blocked_reason=f"timeout after {timeout_sec}s",
            )
        except httpx.RequestError as exc:
            return FetchResult(
                fetch_id=fetch_id, url=url, final_url=url,
                status_code=0, fetched_at=fetched_at, bytes_read=0, content_hash="",
                blocked_reason=f"request error: {exc}",
            )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _check_domain_policy(self, domain: str) -> str | None:
        if self._deny_domains and _matches_any(domain, self._deny_domains):
            return f"domain denied: {domain}"
        if self._allow_domains and not _matches_any(domain, self._allow_domains):
            return f"domain not in allow list: {domain}"
        return None

    def _rate_limit(self, domain: str) -> None:
        last = _PER_DOMAIN_LAST_FETCH.get(domain, 0.0)
        wait = self._min_delay_ms / 1000.0 - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        _PER_DOMAIN_LAST_FETCH[domain] = time.monotonic()


# ── Helpers ───────────────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; Janus/1.0; evidence-research; "
        "+https://github.com/janus)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def _fid(url: str) -> str:
    return "fetch_" + hashlib.sha256(url.encode()).hexdigest()[:16]


def _extract_domain(url: str) -> str:
    try:
        return url.split("://", 1)[1].split("/")[0].lower()
    except IndexError:
        return ""


def _matches_any(domain: str, patterns: list[str]) -> bool:
    domain = domain.lstrip("www.")
    for pat in patterns:
        pat = pat.lstrip("www.")
        if domain == pat or domain.endswith("." + pat):
            return True
    return False
