"""HttpxFetchProvider — live HTTP fetch using httpx with rate-limit and domain policy.

Used only when config.mode == "live" and config.fetch_provider == "httpx".
Import-guarded so tests that don't need live HTTP never pay the httpx import cost.

Security contract:
- Only https is allowed by default.
- Private/loopback/link-local/multicast IPs are blocked before and after redirect.
- Redirects are followed manually so every hop is re-validated.
- Content is streamed with a hard byte cap; .content is never used.
- Binary and media content types are blocked.
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .schema import FetchResult


_PER_DOMAIN_LAST_FETCH: dict[str, float] = {}

_ALLOWED_CONTENT_TYPES = frozenset({
    "text/html",
    "text/plain",
    "application/xhtml+xml",
    "application/xml",
    "application/json",
    "text/xml",
})

_BLOCKED_CONTENT_TYPE_PREFIXES = (
    "application/octet-stream",
    "application/zip",
    "application/x-msdownload",
    "application/pdf",
    "image/",
    "video/",
    "audio/",
)

_MAX_REDIRECTS = 5


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
        resolve_dns: bool = True,
        dns_resolver: Any | None = None,
    ) -> None:
        self._min_delay_ms = min_delay_ms
        self._allow_domains: list[str] = allow_domains or []
        self._deny_domains: list[str] = deny_domains or []
        self._allowed_schemes: set[str] = set(allowed_schemes or ["https"])
        self._resolve_dns = resolve_dns
        self._dns_resolver = dns_resolver or _resolve_hostname_ips

    # ── Public interface ──────────────────────────────────────────────────────

    def fetch(
        self, url: str, *, timeout_sec: int = 15, max_bytes: int = 2_000_000
    ) -> FetchResult:
        import httpx

        fetch_id = _fid(url)
        fetched_at = datetime.now(timezone.utc).isoformat()

        blocked = self._validate_url(url)
        if blocked:
            return _blocked(fetch_id, url, fetched_at, blocked)

        domain = _extract_domain(url)
        self._rate_limit(domain)

        try:
            with httpx.Client(follow_redirects=False, timeout=timeout_sec) as client:
                current_url = url
                hops = 0
                response = None

                while True:
                    response = client.get(current_url, headers=_HEADERS)
                    if response.status_code in (301, 302, 303, 307, 308):
                        hops += 1
                        if hops > _MAX_REDIRECTS:
                            return _blocked(fetch_id, url, fetched_at, "too_many_redirects")
                        location = response.headers.get("location", "")
                        if not location:
                            return _blocked(fetch_id, url, fetched_at, "redirect_missing_location")
                        # Resolve relative redirects
                        if not location.startswith("http"):
                            parsed = urlparse(current_url)
                            location = f"{parsed.scheme}://{parsed.netloc}{location}"
                        blocked = self._validate_url(location)
                        if blocked:
                            return _blocked(fetch_id, url, fetched_at,
                                            f"redirect_blocked: {blocked}")
                        current_url = location
                    else:
                        break

            final_url = current_url
            status_code = response.status_code
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()

            if status_code != 200:
                return FetchResult(
                    fetch_id=fetch_id, url=url, final_url=final_url,
                    status_code=status_code, fetched_at=fetched_at,
                    bytes_read=0, content_hash="",
                    blocked_reason=f"HTTP {status_code}",
                )

            blocked = _check_content_type(content_type)
            if blocked:
                return _blocked(fetch_id, url, fetched_at, blocked)

            # Stream with hard byte cap — never read full body
            with httpx.Client(follow_redirects=False, timeout=timeout_sec) as stream_client:
                chunks: list[bytes] = []
                bytes_read = 0
                with stream_client.stream("GET", final_url, headers=_HEADERS) as stream_resp:
                    stream_status = getattr(stream_resp, "status_code", None)
                    if not isinstance(stream_status, int):
                        stream_status = status_code
                    stream_headers = getattr(stream_resp, "headers", None)
                    if not isinstance(stream_headers, dict):
                        stream_headers = {}
                    stream_content_type = (
                        stream_headers.get("content-type", "")
                        .split(";")[0].strip().lower()
                    ) or content_type

                    if stream_status != 200:
                        return FetchResult(
                            fetch_id=fetch_id, url=url, final_url=final_url,
                            status_code=stream_status, fetched_at=fetched_at,
                            bytes_read=0, content_hash="",
                            blocked_reason=f"HTTP {stream_status}",
                        )

                    blocked = _check_content_type(stream_content_type)
                    if blocked:
                        return _blocked(fetch_id, url, fetched_at, blocked)
                    content_type = stream_content_type

                    for chunk in stream_resp.iter_bytes():
                        bytes_read += len(chunk)
                        if bytes_read > max_bytes:
                            return _blocked(fetch_id, url, fetched_at,
                                            "max_page_bytes_exceeded")
                        chunks.append(chunk)

            raw = b"".join(chunks)
            content_hash = "sha256:" + hashlib.sha256(raw).hexdigest()

            suffix = ".html" if "html" in content_type else ".txt"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            tmp.write(raw)
            tmp.close()

            return FetchResult(
                fetch_id=fetch_id, url=url, final_url=final_url,
                status_code=200, content_type=content_type,
                fetched_at=fetched_at,
                bytes_read=bytes_read,
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

    def _validate_url(self, url: str) -> str | None:
        """Return a blocked reason string, or None if the URL is allowed."""
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or "").lower()

        if scheme not in self._allowed_schemes:
            return f"scheme not allowed: {scheme!r}"

        if not hostname:
            return "missing hostname"

        blocked = _check_hostname(hostname)
        if blocked:
            return blocked

        if self._deny_domains and _matches_any(hostname, self._deny_domains):
            return f"domain denied: {hostname}"

        if self._allow_domains and not _matches_any(hostname, self._allow_domains):
            return f"domain not in allow list: {hostname}"

        if self._resolve_dns:
            blocked = _check_resolved_hostname(hostname, self._dns_resolver)
            if blocked:
                return blocked

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

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local
    ipaddress.ip_network("::1/128"),            # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),           # IPv6 private
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
    ipaddress.ip_network("224.0.0.0/4"),       # multicast
    ipaddress.ip_network("0.0.0.0/8"),         # unspecified
]


def _check_hostname(hostname: str) -> str | None:
    """Return a blocked reason if hostname is localhost or a private IP."""
    if hostname in ("localhost", "localhost.localdomain", "ip6-localhost"):
        return f"blocked hostname: {hostname}"
    try:
        addr = ipaddress.ip_address(hostname)
        return _check_ip_address(addr, hostname)
    except ValueError:
        pass
    return None


def _check_resolved_hostname(hostname: str, resolver: Any) -> str | None:
    for ip in resolver(hostname):
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        blocked = _check_ip_address(addr, ip)
        if blocked:
            return f"resolved blocked address for {hostname}: {ip}"
    return None


def _check_ip_address(addr: ipaddress._BaseAddress, label: str) -> str | None:
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    ):
        return f"blocked private IP: {label}"
    for net in _PRIVATE_NETWORKS:
        if addr in net:
            return f"blocked private IP: {label}"
    return None


def _resolve_hostname_ips(hostname: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except OSError:
        return []
    ips = []
    for info in infos:
        sockaddr = info[4]
        if sockaddr:
            ips.append(sockaddr[0])
    return sorted(set(ips))


def _check_content_type(content_type: str) -> str | None:
    """Return a blocked reason if content type is not in the allowed set."""
    if not content_type:
        return None
    for prefix in _BLOCKED_CONTENT_TYPE_PREFIXES:
        if content_type.startswith(prefix):
            return f"blocked content type: {content_type}"
    # If content type is set but not in allowed list, block it
    base_type = content_type.split(";")[0].strip()
    if base_type and base_type not in _ALLOWED_CONTENT_TYPES:
        # Only block when we have an explicit non-text type — allow unknown/empty
        if "/" in base_type and not base_type.startswith("text/"):
            return f"blocked content type: {content_type}"
    return None


def _fid(url: str) -> str:
    return "fetch_" + hashlib.sha256(url.encode()).hexdigest()[:16]


def _extract_domain(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.hostname or "").lower()


def _matches_any(domain: str, patterns: list[str]) -> bool:
    domain = domain.lstrip("www.")
    for pat in patterns:
        pat = pat.lstrip("www.")
        if domain == pat or domain.endswith("." + pat):
            return True
    return False


def _blocked(fetch_id: str, url: str, fetched_at: str, reason: str) -> FetchResult:
    return FetchResult(
        fetch_id=fetch_id, url=url, final_url=url,
        status_code=0, fetched_at=fetched_at, bytes_read=0, content_hash="",
        blocked_reason=reason,
    )
