"""Tests for HttpxFetchProvider — uses httpx mocking, no real network calls."""

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from core.evidence_harness.fetch_http import HttpxFetchProvider, _extract_domain, _matches_any


class TestHttpxFetchProvider:
    def test_blocks_non_https_scheme(self):
        provider = HttpxFetchProvider()
        result = provider.fetch("http://example.com/page")
        assert result.status_code == 0
        assert "scheme not allowed" in (result.blocked_reason or "")

    def test_blocks_denied_domain(self):
        provider = HttpxFetchProvider(deny_domains=["evil.com"])
        result = provider.fetch("https://evil.com/page")
        assert result.status_code == 0
        assert "denied" in (result.blocked_reason or "")

    def test_blocks_domain_not_in_allow_list(self):
        provider = HttpxFetchProvider(allow_domains=["eia.gov"])
        result = provider.fetch("https://random.com/page")
        assert result.status_code == 0
        assert "not in allow list" in (result.blocked_reason or "")

    def test_passes_allowed_domain(self, tmp_path):
        provider = HttpxFetchProvider(allow_domains=["eia.gov"])

        mock_response = MagicMock()
        mock_response.url = "https://eia.gov/report"
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}
        mock_response.content = b"<html>EIA content</html>"

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = provider.fetch("https://eia.gov/report")

        assert result.status_code == 200
        assert result.content_hash.startswith("sha256:")

    def test_successful_fetch_returns_temp_file_path(self):
        provider = HttpxFetchProvider()

        mock_response = MagicMock()
        mock_response.url = "https://reuters.com/article"
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}
        mock_response.content = b"<html>Reuters article</html>"

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = provider.fetch("https://reuters.com/article")

        assert result.status_code == 200
        assert result.text_or_html_path is not None
        path = Path(result.text_or_html_path)
        assert path.exists()
        assert path.read_bytes() == b"<html>Reuters article</html>"

    def test_returns_blocked_on_http_error(self):
        provider = HttpxFetchProvider()

        mock_response = MagicMock()
        mock_response.url = "https://example.com/page"
        mock_response.status_code = 404
        mock_response.headers = {}

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = provider.fetch("https://example.com/page")

        assert result.status_code == 404
        assert "404" in (result.blocked_reason or "")

    def test_returns_blocked_on_timeout(self):
        import httpx
        provider = HttpxFetchProvider()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = httpx.TimeoutException("timed out")
            mock_client_cls.return_value = mock_client

            result = provider.fetch("https://example.com/page")

        assert result.status_code == 0
        assert "timeout" in (result.blocked_reason or "")

    def test_content_hash_is_deterministic(self):
        provider = HttpxFetchProvider()

        mock_response = MagicMock()
        mock_response.url = "https://example.com/page"
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}
        mock_response.content = b"<html>same content</html>"

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client

            r1 = provider.fetch("https://example.com/page")
            r2 = provider.fetch("https://example.com/page")

        assert r1.content_hash == r2.content_hash

    def test_fetch_id_is_deterministic(self):
        from core.evidence_harness.fetch_http import _fid
        assert _fid("https://eia.gov/report") == _fid("https://eia.gov/report")

    def test_respects_max_bytes(self):
        provider = HttpxFetchProvider()

        big_content = b"X" * 100_000
        mock_response = MagicMock()
        mock_response.url = "https://example.com/page"
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}
        mock_response.content = big_content

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = provider.fetch("https://example.com/page", max_bytes=50_000)

        assert result.bytes_read == 50_000


class TestHelpers:
    def test_extract_domain_strips_www(self):
        assert _extract_domain("https://www.reuters.com/article") == "www.reuters.com"

    def test_extract_domain_basic(self):
        assert _extract_domain("https://eia.gov/report") == "eia.gov"

    def test_matches_any_subdomain(self):
        assert _matches_any("api.eia.gov", ["eia.gov"])

    def test_matches_any_exact(self):
        assert _matches_any("eia.gov", ["eia.gov"])

    def test_matches_any_no_match(self):
        assert not _matches_any("evil.com", ["eia.gov", "sec.gov"])
