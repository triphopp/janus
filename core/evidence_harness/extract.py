"""DocumentExtractor — strips HTML to clean text for evidence review.

Phase 2: plain-text extractor using stdlib html.parser (no extra dependencies).
Phase 4+ can swap in trafilatura or readability-lxml via the same protocol.
"""

from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from typing import Protocol

from .schema import FetchResult, ExtractedDocument
from .ids import document_id as make_document_id, source_id as make_source_id

EXTRACTION_VERSION = "plaintext.v1"
EXCERPT_MAX_CHARS = 400


class _HtmlStripper(HTMLParser):
    _SKIP_TAGS = {"script", "style", "noscript", "form", "input", "head"}

    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self._parts: list[str] = []
        self.title: str = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._SKIP_TAGS:
            self._skip += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        stripped = data.strip()
        if not stripped:
            return
        if self._in_title and not self.title:
            self.title = stripped
        self._parts.append(stripped)

    def get_text(self) -> str:
        raw = " ".join(self._parts)
        return re.sub(r"\s+", " ", raw).strip()


def _strip_html(html: str) -> tuple[str, str]:
    """Return (title, body_text)."""
    parser = _HtmlStripper()
    try:
        parser.feed(html)
    except Exception:
        pass
    return parser.title, parser.get_text()


def _content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class PlainTextExtractor:
    name: str = "plaintext"
    version: str = EXTRACTION_VERSION

    def extract(
        self,
        fetch: FetchResult,
        *,
        accessed_at: str,
        source_tier: str = "unknown",
    ) -> ExtractedDocument | None:
        if fetch.blocked_reason or fetch.status_code != 200:
            return None

        raw_text = ""
        extracted_title: str | None = None

        if fetch.text_or_html_path:
            try:
                with open(fetch.text_or_html_path, encoding="utf-8", errors="replace") as f:
                    raw = f.read()
                content_type = fetch.content_type or ""
                if "html" in content_type or fetch.text_or_html_path.endswith(".html"):
                    extracted_title, raw_text = _strip_html(raw)
                else:
                    raw_text = raw.strip()
            except OSError:
                return None

        if not raw_text:
            return None

        extract_hash = _content_hash(raw_text)
        src_id = make_source_id(fetch.final_url or fetch.url, fetch.content_hash)
        doc_id = make_document_id(src_id, extract_hash, self.version)

        excerpt = raw_text[:EXCERPT_MAX_CHARS]
        if len(raw_text) > EXCERPT_MAX_CHARS:
            excerpt = raw_text[:EXCERPT_MAX_CHARS].rsplit(" ", 1)[0] + " …"

        return ExtractedDocument(
            document_id=doc_id,
            fetch_id=fetch.fetch_id,
            canonical_url=fetch.final_url or fetch.url,
            domain=_domain_from(fetch.final_url or fetch.url),
            accessed_at=accessed_at,
            extracted_text=raw_text,
            excerpt=excerpt,
            content_hash=fetch.content_hash,
            extraction_version=self.version,
            source_tier=source_tier,
            title=extracted_title or None,
        )


def _domain_from(url: str) -> str:
    from urllib.parse import urlparse
    try:
        return urlparse(url).netloc
    except Exception:
        return ""
