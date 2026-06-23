"""Tests for HttpxFetchProvider — uses httpx mocking, no real network calls."""

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from core.evidence_harness.fetch_http import HttpxFetchProvider, _extract_domain, _matches_any


def _provider(**kwargs) -> HttpxFetchProvider:
    kwargs.setdefault("dns_resolver", lambda _host: [])
    return HttpxFetchProvider(**kwargs)


class TestHttpxFetchProvider:
    def test_blocks_non_https_scheme(self):
        provider = _provider()
        result = provider.fetch("http://example.com/page")
        assert result.status_code == 0
        assert "scheme not allowed" in (result.blocked_reason or "")

    def test_blocks_denied_domain(self):
        provider = _provider(deny_domains=["evil.com"])
        result = provider.fetch("https://evil.com/page")
        assert result.status_code == 0
        assert "denied" in (result.blocked_reason or "")

    def test_blocks_domain_not_in_allow_list(self):
        provider = _provider(allow_domains=["eia.gov"])
        result = provider.fetch("https://random.com/page")
        assert result.status_code == 0
        assert "not in allow list" in (result.blocked_reason or "")

    def test_passes_allowed_domain(self, tmp_path):
        provider = _provider(allow_domains=["eia.gov"])

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
        provider = _provider()
        content = b"<html>Reuters article</html>"

        # New impl: first GET checks status/content-type, then stream() reads bytes.
        head_resp = MagicMock()
        head_resp.status_code = 200
        head_resp.headers = {"content-type": "text/html"}
        head_resp.url = "https://reuters.com/article"

        stream_resp = MagicMock()
        stream_resp.__enter__ = MagicMock(return_value=stream_resp)
        stream_resp.__exit__ = MagicMock(return_value=False)
        stream_resp.iter_bytes.return_value = iter([content])

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = head_resp
            mock_client.stream.return_value = stream_resp
            mock_client_cls.return_value = mock_client

            result = provider.fetch("https://reuters.com/article")

        assert result.status_code == 200
        assert result.text_or_html_path is not None
        path = Path(result.text_or_html_path)
        assert path.exists()
        assert path.read_bytes() == content

    def test_returns_blocked_on_http_error(self):
        provider = _provider()

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
        provider = _provider()

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
        provider = _provider()

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
        # New policy: streaming stops and returns blocked when max bytes is exceeded.
        provider = _provider()
        big_chunk = b"X" * 100_000

        head_resp = MagicMock()
        head_resp.status_code = 200
        head_resp.headers = {"content-type": "text/html"}
        head_resp.url = "https://example.com/page"

        stream_resp = MagicMock()
        stream_resp.__enter__ = MagicMock(return_value=stream_resp)
        stream_resp.__exit__ = MagicMock(return_value=False)
        stream_resp.iter_bytes.return_value = iter([big_chunk])

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = head_resp
            mock_client.stream.return_value = stream_resp
            mock_client_cls.return_value = mock_client

            result = provider.fetch("https://example.com/page", max_bytes=50_000)

        assert result.blocked_reason == "max_page_bytes_exceeded"
        assert result.text_or_html_path is None


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
