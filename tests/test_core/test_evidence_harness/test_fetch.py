"""Tests for FixtureFetchProvider."""

import json
import tempfile
from pathlib import Path

import pytest

from core.evidence_harness.fetch import FixtureFetchProvider
from core.evidence_harness.schema import FetchResult


class TestFixtureFetchProvider:
    def test_returns_fetch_result_for_missing_fixture(self):
        provider = FixtureFetchProvider(fixture_dir="/nonexistent/path")
        result = provider.fetch("https://example.com/article")
        assert isinstance(result, FetchResult)
        assert result.blocked_reason == "fixture not found"
        assert result.status_code == 404

    def test_returns_content_for_html_fixture(self, tmp_path):
        url = "https://example.com/article"
        import hashlib
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        html_file = tmp_path / f"{url_hash}.html"
        html_content = b"<html><body>WTI crude oil inventory</body></html>"
        html_file.write_bytes(html_content)

        provider = FixtureFetchProvider(fixture_dir=str(tmp_path))
        result = provider.fetch(url)
        assert result.status_code == 200
        assert result.bytes_read == len(html_content)
        assert result.content_hash.startswith("sha256:")
        assert result.text_or_html_path == str(html_file)

    def test_manifest_maps_url_to_fixture(self, tmp_path):
        url = "https://eia.gov/report"
        html_file = tmp_path / "eia_report.html"
        html_file.write_bytes(b"<html>EIA report content</html>")

        manifest = {url: "eia_report.html"}
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))

        provider = FixtureFetchProvider(fixture_dir=str(tmp_path))
        result = provider.fetch(url)
        assert result.status_code == 200
        assert result.bytes_read > 0

    def test_blocks_oversized_page(self, tmp_path):
        url = "https://example.com/big"
        import hashlib
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        html_file = tmp_path / f"{url_hash}.html"
        html_file.write_bytes(b"x" * 100)

        provider = FixtureFetchProvider(fixture_dir=str(tmp_path))
        result = provider.fetch(url, max_bytes=10)
        assert result.blocked_reason is not None
        assert "max_bytes" in result.blocked_reason

    def test_fetch_id_is_deterministic(self):
        provider = FixtureFetchProvider(fixture_dir="/nonexistent")
        url = "https://example.com/stable"
        r1 = provider.fetch(url)
        r2 = provider.fetch(url)
        assert r1.fetch_id == r2.fetch_id
