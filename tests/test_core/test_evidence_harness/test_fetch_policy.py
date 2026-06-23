"""Fetch policy tests — all offline via mocked HTTP responses.

Tests confirm that the live fetch gateway enforces:
- scheme allowlist
- private/loopback IP block
- redirect re-validation
- content type block
- byte cap before writing usable cache
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


def _provider(**kwargs):
    from core.evidence_harness.fetch_http import HttpxFetchProvider
    kwargs.setdefault("dns_resolver", lambda _host: [])
    return HttpxFetchProvider(min_delay_ms=0, **kwargs)


def _mock_response(url: str, status: int = 200, content_type: str = "text/html",
                   content: bytes = b"<html>test</html>", redirect_to: str | None = None):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"content-type": content_type}
    if redirect_to:
        resp.status_code = 302
        resp.headers = {"content-type": content_type, "location": redirect_to}
    resp.url = url
    return resp


class TestSchemePolicy:
    def test_blocks_http_scheme(self):
        p = _provider()
        result = p.fetch("http://example.com/page")
        assert result.blocked_reason is not None
        assert "scheme" in result.blocked_reason

    def test_blocks_file_scheme(self):
        p = _provider()
        result = p.fetch("file:///etc/passwd")
        assert result.blocked_reason is not None

    def test_allows_https(self):
        p = _provider()
        blocked = p._validate_url("https://example.com/page")
        assert blocked is None


class TestPrivateIpPolicy:
    def test_blocks_localhost(self):
        p = _provider()
        result = p.fetch("https://localhost/secret")
        assert result.blocked_reason is not None
        assert "localhost" in result.blocked_reason or "blocked" in result.blocked_reason

    def test_blocks_127_0_0_1(self):
        p = _provider()
        result = p.fetch("https://127.0.0.1/secret")
        assert result.blocked_reason is not None

    def test_blocks_10_x_private(self):
        from core.evidence_harness.fetch_http import _check_hostname
        assert _check_hostname("10.0.0.1") is not None

    def test_blocks_192_168_private(self):
        from core.evidence_harness.fetch_http import _check_hostname
        assert _check_hostname("192.168.1.1") is not None

    def test_blocks_172_16_private(self):
        from core.evidence_harness.fetch_http import _check_hostname
        assert _check_hostname("172.16.0.1") is not None

    def test_blocks_link_local(self):
        from core.evidence_harness.fetch_http import _check_hostname
        assert _check_hostname("169.254.0.1") is not None

    def test_allows_public_ip(self):
        from core.evidence_harness.fetch_http import _check_hostname
        assert _check_hostname("8.8.8.8") is None

    def test_allows_public_domain(self):
        from core.evidence_harness.fetch_http import _check_hostname
        assert _check_hostname("reuters.com") is None

    def test_blocks_domain_resolving_to_private_ip(self):
        p = _provider(dns_resolver=lambda _host: ["127.0.0.1"])
        result = p.fetch("https://metadata.example.com/latest")
        assert result.blocked_reason is not None
        assert "resolved blocked address" in result.blocked_reason


class TestRedirectPolicy:
    def test_blocks_redirect_to_localhost(self):
        p = _provider()

        import httpx
        with patch("httpx.Client") as mock_client_cls:
            instance = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=instance)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            # First response: redirect to localhost
            redir = MagicMock()
            redir.status_code = 302
            redir.headers = {"location": "https://localhost/secret"}
            instance.get.return_value = redir

            result = p.fetch("https://example.com/page")
            assert result.blocked_reason is not None
            assert "redirect_blocked" in result.blocked_reason or "blocked" in result.blocked_reason

    def test_blocks_redirect_to_private_ip(self):
        p = _provider()

        with patch("httpx.Client") as mock_client_cls:
            instance = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=instance)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            redir = MagicMock()
            redir.status_code = 301
            redir.headers = {"location": "https://192.168.1.1/admin"}
            instance.get.return_value = redir

            result = p.fetch("https://example.com/page")
            assert result.blocked_reason is not None

    def test_blocks_too_many_redirects(self):
        from core.evidence_harness.fetch_http import _MAX_REDIRECTS
        p = _provider()

        call_count = 0
        def _get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            r.status_code = 302
            # redirect to a valid URL — just keep looping
            r.headers = {"location": f"https://example.com/hop{call_count}"}
            return r

        with patch("httpx.Client") as mock_client_cls:
            instance = MagicMock()
            instance.get.side_effect = _get
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=instance)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

            result = p.fetch("https://example.com/start")
            assert result.blocked_reason == "too_many_redirects"
            assert call_count > _MAX_REDIRECTS


class TestContentTypePolicy:
    def test_blocks_binary_content_type(self):
        from core.evidence_harness.fetch_http import _check_content_type
        assert _check_content_type("application/octet-stream") is not None

    def test_blocks_zip(self):
        from core.evidence_harness.fetch_http import _check_content_type
        assert _check_content_type("application/zip") is not None

    def test_blocks_pdf(self):
        from core.evidence_harness.fetch_http import _check_content_type
        assert _check_content_type("application/pdf") is not None

    def test_blocks_image(self):
        from core.evidence_harness.fetch_http import _check_content_type
        assert _check_content_type("image/png") is not None

    def test_blocks_video(self):
        from core.evidence_harness.fetch_http import _check_content_type
        assert _check_content_type("video/mp4") is not None

    def test_allows_text_html(self):
        from core.evidence_harness.fetch_http import _check_content_type
        assert _check_content_type("text/html") is None

    def test_allows_text_plain(self):
        from core.evidence_harness.fetch_http import _check_content_type
        assert _check_content_type("text/plain") is None

    def test_allows_application_json(self):
        from core.evidence_harness.fetch_http import _check_content_type
        assert _check_content_type("application/json") is None

    def test_allows_content_type_with_charset(self):
        from core.evidence_harness.fetch_http import _check_content_type
        assert _check_content_type("text/html; charset=utf-8") is None

    def test_blocks_streaming_response_content_type_drift(self):
        p = _provider()

        head_resp = MagicMock()
        head_resp.status_code = 200
        head_resp.headers = {"content-type": "text/html"}

        stream_resp = MagicMock()
        stream_resp.__enter__ = MagicMock(return_value=stream_resp)
        stream_resp.__exit__ = MagicMock(return_value=False)
        stream_resp.status_code = 200
        stream_resp.headers = {"content-type": "application/pdf"}
        stream_resp.iter_bytes.return_value = iter([b"%PDF"])

        with patch("httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = head_resp
            mock_client.stream.return_value = stream_resp
            mock_cls.return_value = mock_client

            result = p.fetch("https://example.com/report")

        assert result.blocked_reason is not None
        assert "blocked content type" in result.blocked_reason

    def test_blocks_streaming_response_status_drift(self):
        p = _provider()

        head_resp = MagicMock()
        head_resp.status_code = 200
        head_resp.headers = {"content-type": "text/html"}

        stream_resp = MagicMock()
        stream_resp.__enter__ = MagicMock(return_value=stream_resp)
        stream_resp.__exit__ = MagicMock(return_value=False)
        stream_resp.status_code = 403
        stream_resp.headers = {"content-type": "text/html"}
        stream_resp.iter_bytes.return_value = iter([b"forbidden"])

        with patch("httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = head_resp
            mock_client.stream.return_value = stream_resp
            mock_cls.return_value = mock_client

            result = p.fetch("https://example.com/report")

        assert result.status_code == 403
        assert result.blocked_reason == "HTTP 403"


class TestByteCap:
    def test_blocks_oversized_response_before_cache(self):
        """Stream stops and returns blocked before writing usable cache."""
        import httpx

        p = _provider()
        max_bytes = 100
        large_chunk = b"x" * (max_bytes + 1)

        stream_resp = MagicMock()
        stream_resp.__enter__ = MagicMock(return_value=stream_resp)
        stream_resp.__exit__ = MagicMock(return_value=False)
        stream_resp.iter_bytes.return_value = iter([large_chunk])
        stream_resp.headers = {"content-type": "text/html"}
        stream_resp.status_code = 200

        first_resp = MagicMock()
        first_resp.status_code = 200
        first_resp.headers = {"content-type": "text/html"}
        first_resp.url = "https://example.com/big"

        with patch("httpx.Client") as mock_cls:
            def _enter(self_):
                inst = MagicMock()
                inst.get.return_value = first_resp
                inst.stream.return_value = stream_resp
                return inst
            mock_cls.return_value.__enter__ = _enter
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)

            result = p.fetch("https://example.com/big", max_bytes=max_bytes)

        assert result.blocked_reason == "max_page_bytes_exceeded"
        assert result.text_or_html_path is None


class TestAllowedPublicUrl:
    def test_public_https_html_is_allowed_by_policy(self):
        p = _provider()
        # URL validation only — no actual network call
        blocked = p._validate_url("https://reuters.com/article/tsla-q1")
        assert blocked is None

    def test_domain_deny_list_blocks(self):
        p = _provider(deny_domains=["blocked.example.com"])
        blocked = p._validate_url("https://blocked.example.com/page")
        assert blocked is not None

    def test_domain_allow_list_blocks_unlisted(self):
        p = _provider(allow_domains=["sec.gov", "reuters.com"])
        blocked = p._validate_url("https://evil.com/page")
        assert blocked is not None

    def test_domain_allow_list_permits_listed(self):
        p = _provider(allow_domains=["sec.gov", "reuters.com"])
        blocked = p._validate_url("https://sec.gov/filing/abc")
        assert blocked is None
