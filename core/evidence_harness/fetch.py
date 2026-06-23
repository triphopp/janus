"""FetchProvider — fixture implementation for Phase 1/tests.

HttpxFetchProvider (live) is added in Phase 4 after gateway policy enforcement
is in place. Fixture provider is the default for all offline tests.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .schema import FetchResult


class FetchProvider(Protocol):
    name: str

    def fetch(self, url: str, *, timeout_sec: int = 15, max_bytes: int = 2_000_000) -> FetchResult:
        ...


@dataclass
class FixtureFetchProvider:
    """Returns pre-baked FetchResults from a fixture directory.

    Fixture files are named by a sha256 of the URL (first 16 hex chars) or
    stored in a manifest JSON that maps url -> fixture filename.
    Falls back to a minimal 'not found' result rather than erroring.
    """

    name: str = "fixture"
    fixture_dir: str = "tests/fixtures/evidence_harness/pages"

    def fetch(self, url: str, *, timeout_sec: int = 15, max_bytes: int = 2_000_000) -> FetchResult:
        from datetime import datetime, timezone

        fetched_at = datetime.now(timezone.utc).isoformat()
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        fetch_id = f"fetch_{url_hash}"

        fixture_path = self._resolve_fixture(url, url_hash)
        if fixture_path and fixture_path.exists():
            content = fixture_path.read_bytes()
            if len(content) > max_bytes:
                return FetchResult(
                    fetch_id=fetch_id,
                    url=url,
                    final_url=url,
                    status_code=200,
                    fetched_at=fetched_at,
                    bytes_read=0,
                    content_hash="",
                    blocked_reason=f"fixture exceeds max_bytes ({len(content)} > {max_bytes})",
                )
            content_hash = "sha256:" + hashlib.sha256(content).hexdigest()
            suffix = fixture_path.suffix
            text_path = str(fixture_path) if suffix in (".html", ".txt") else None
            return FetchResult(
                fetch_id=fetch_id,
                url=url,
                final_url=url,
                status_code=200,
                content_type="text/html" if suffix == ".html" else "text/plain",
                fetched_at=fetched_at,
                bytes_read=len(content),
                content_hash=content_hash,
                text_or_html_path=text_path,
            )

        return FetchResult(
            fetch_id=fetch_id,
            url=url,
            final_url=url,
            status_code=404,
            fetched_at=fetched_at,
            bytes_read=0,
            content_hash="",
            blocked_reason="fixture not found",
        )

    def _resolve_fixture(self, url: str, url_hash: str) -> Path | None:
        base = Path(self.fixture_dir)
        manifest_path = base / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            if url in manifest:
                return base / manifest[url]

        for ext in (".html", ".txt"):
            candidate = base / f"{url_hash}{ext}"
            if candidate.exists():
                return candidate
        return None
