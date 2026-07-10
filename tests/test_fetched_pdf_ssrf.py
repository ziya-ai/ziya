"""
Regression coverage for PenPal #76/#126 [LOW, CWE-918]: blind SSRF in
tool_execution._maybe_extract_fetched_pdf.

Scope (per the tracker's split-the-finding rule): the heavy post-DNS
allowlist is intentionally NOT implemented — the sink is blind (response
discarded unless it parses as a PDF) and the agent's by-design fetch/curl
tools already reach internal URLs, so it grants no incremental capability
there. What IS closed: (1) automatic redirect-following (our code silently
hopping a 302 to an internal target after the PDF-signature gate passed),
and (2) a direct literal-IP fetch into a loopback/link-local/private range.
"""
import inspect
import pytest

from app.tool_execution import (
    _url_host_is_blocked_literal_ip,
    _maybe_extract_fetched_pdf,
)


class TestLiteralIpGuard:
    @pytest.mark.parametrize("url", [
        "http://169.254.169.254/latest/meta-data/",   # EC2 IMDS
        "http://127.0.0.1/api/debug/mcp-state",
        "http://[::1]/x",
        "http://10.0.0.5/internal.pdf",
        "http://172.16.0.1/x",
        "http://192.168.1.1/x",
    ])
    def test_internal_literal_ips_blocked(self, url):
        assert _url_host_is_blocked_literal_ip(url) is True

    @pytest.mark.parametrize("url", [
        "https://example.com/doc.pdf",
        "http://8.8.8.8/doc.pdf",          # public literal IP — not blocked
        "https://cdn.example.org/a.pdf",
    ])
    def test_public_urls_not_blocked(self, url):
        assert _url_host_is_blocked_literal_ip(url) is False

    def test_hostname_not_resolved_here(self):
        # A hostname that *would* resolve to loopback is NOT blocked by this
        # literal-only guard — documented scope, not an oversight.
        assert _url_host_is_blocked_literal_ip("http://localhost/x") is False

    def test_no_host_is_not_blocked(self):
        assert _url_host_is_blocked_literal_ip("not a url") is False


class TestRedirectsDisabled:
    def test_source_uses_follow_redirects_false(self):
        # The re-fetch must not follow redirects (the incremental SSRF hop).
        src = inspect.getsource(_maybe_extract_fetched_pdf)
        assert "follow_redirects=False" in src
        assert "follow_redirects=True" not in src


class TestBlockedUrlShortCircuits:
    @pytest.mark.asyncio
    async def test_internal_url_returns_original_unchanged(self):
        # A PDF-signature result + an internal literal-IP url must return the
        # original text unchanged (the guard returns before any fetch — and
        # since no server is listening in-test, a regression that removed the
        # guard would surface as a connection error, not a pass).
        result = "%PDF-1.7 fake signature to pass the gate"
        out = await _maybe_extract_fetched_pdf(
            result, {"url": "http://169.254.169.254/latest/meta-data/"}
        )
        assert out == result

    @pytest.mark.asyncio
    async def test_non_pdf_result_never_processes_url(self):
        # No PDF signature → immediate return, url never examined.
        out = await _maybe_extract_fetched_pdf(
            "just some markdown", {"url": "http://169.254.169.254/"}
        )
        assert out == "just some markdown"
