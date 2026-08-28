"""
ASR EGR-03 — outbound-destination validation for URL-consuming sinks.

``app.utils.net_guard`` is the single definition of "internal destination",
shared by the fetched-PDF re-fetch path (PenPal #76/#126) and the remote-MCP
connect path (EGR-03). Before the fix the range list lived privately inside
``app.tool_execution`` and the remote-MCP path had no check at all.

Two properties are pinned here:
  1. the policy itself (scheme, host presence, internal ranges), and
  2. the *extraction* seam -- ``app.tool_execution`` must keep re-exporting the
     names the pre-existing ``test_fetched_pdf_ssrf`` suite imports, or the
     refactor silently breaks that finding's coverage instead of sharing it.
"""

import ipaddress

import pytest

from app.utils.net_guard import (
    ALLOWED_SCHEMES,
    BLOCKED_NETWORKS,
    host_is_blocked_literal_ip,
    validate_outbound_url,
)

SRC = "test-suite"


class TestSchemeRejection:
    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "file://localhost/etc/passwd",
        "gopher://example.com/",
        "ftp://example.com/x",
        "data:text/plain;base64,AAAA",
        "jar:http://example.com/a.jar!/",
        "ws://example.com/",
    ])
    def test_non_http_scheme_rejected(self, url):
        with pytest.raises(ValueError):
            validate_outbound_url(url, source=SRC)

    def test_schemeless_value_rejected(self):
        """urlparse() silently treats a schemeless string as a bare path, so
        without an explicit scheme check ``example.com/x`` would sail through
        with scheme='' and hostname=None."""
        with pytest.raises(ValueError):
            validate_outbound_url("example.com/mcp", source=SRC)

    def test_missing_host_rejected(self):
        with pytest.raises(ValueError):
            validate_outbound_url("http://", source=SRC)

    @pytest.mark.parametrize("bad", [None, "", "   ", 7, [], {"url": "x"}])
    def test_empty_or_non_string_rejected(self, bad):
        with pytest.raises(ValueError):
            validate_outbound_url(bad, source=SRC)

    def test_allowed_schemes_are_exactly_http_and_https(self):
        assert set(ALLOWED_SCHEMES) == {"http", "https"}


class TestInternalRangeRejection:
    @pytest.mark.parametrize("url", [
        "http://169.254.169.254/latest/meta-data/",        # EC2 IMDS
        "http://169.254.170.2/v2/credentials",             # ECS task metadata
        "http://127.0.0.1:6969/api/debug/mcp-state",       # Ziya's own API
        "http://127.1.2.3/",                               # rest of 127/8
        "http://[::1]:6969/",                              # IPv6 loopback
        "http://[fe80::1]/",                               # IPv6 link-local
        "http://10.0.0.5/",                                # RFC-1918
        "http://172.16.4.4/",                              # RFC-1918
        "http://172.31.255.254/",                          # RFC-1918 upper edge
        "http://192.168.1.1/",                             # RFC-1918
        "http://[fc00::1]/",                               # IPv6 ULA
        "https://169.254.169.254/",                        # https is no escape
    ])
    def test_literal_internal_address_rejected(self, url):
        with pytest.raises(ValueError):
            validate_outbound_url(url, source=SRC)
        assert host_is_blocked_literal_ip(url) is True

    @pytest.mark.parametrize("network", [str(n) for n in BLOCKED_NETWORKS])
    def test_every_declared_network_is_actually_enforced(self, network):
        """Guards against a range being added to the list but the check being
        wired to something else -- take a real address out of each declared
        network and confirm it is refused."""
        net = ipaddress.ip_network(network)
        host = next(net.hosts()) if net.num_addresses > 1 else net.network_address
        literal = f"[{host}]" if host.version == 6 else str(host)
        assert host_is_blocked_literal_ip(f"http://{literal}/") is True


class TestPublicDestinationsStillWork:
    @pytest.mark.parametrize("url", [
        "https://example.com/mcp",
        "http://example.com:8080/sse",
        "https://registry.modelcontextprotocol.io/v0/servers",
        "http://93.184.216.34/",          # public literal IP
        "http://[2606:2800:220:1::1]/",   # public literal IPv6
        "http://172.32.0.1/",             # just outside 172.16/12
        "http://11.0.0.1/",               # just outside 10/8
    ])
    def test_public_destination_accepted(self, url):
        """Paired with the rejection cases: a guard that blocked everything
        would satisfy those alone, and would also break every real server."""
        assert validate_outbound_url(url, source=SRC) == url

    def test_surrounding_whitespace_stripped(self):
        assert validate_outbound_url(
            "  https://example.com/mcp  ", source=SRC
        ) == "https://example.com/mcp"


class TestDocumentedLimitation:
    """The guard is literal-IP only, by explicit decision -- full DNS-rebind
    defence needs resolve-then-pin, which neither httpx nor the MCP SDK
    transports expose.

    Pinned so the limitation is a recorded property rather than an assumed
    one: if someone later adds resolution, this test fails and forces the
    ASR response's stated limitation to be updated with it.
    """

    @pytest.mark.parametrize("host", ["localhost", "internal.corp.example.com"])
    def test_hostname_resolving_internal_is_not_caught(self, host):
        url = f"http://{host}/mcp"
        assert host_is_blocked_literal_ip(url) is False
        assert validate_outbound_url(url, source=SRC) == url

    def test_non_url_input_is_not_treated_as_blocked(self):
        assert host_is_blocked_literal_ip("not a url") is False


class TestErrorMessagesAreActionable:
    def test_message_names_the_source(self):
        with pytest.raises(ValueError) as exc:
            validate_outbound_url("file:///etc/passwd", source="mcp-remote:acme")
        assert "mcp-remote:acme" in str(exc.value)

    def test_internal_address_message_names_the_host(self):
        with pytest.raises(ValueError) as exc:
            validate_outbound_url("http://169.254.169.254/", source=SRC)
        assert "169.254.169.254" in str(exc.value)


class TestExtractionSeam:
    """The range list and host check moved out of ``app.tool_execution``.

    ``tests/test_fetched_pdf_ssrf.py`` imports the private names from there,
    and the module re-exports them for exactly that reason. If a future tidy-up
    drops the re-export, that suite stops importing and PenPal #76/#126 loses
    its coverage silently -- so assert the seam, not just the two halves.
    """

    def test_tool_execution_still_exports_the_private_names(self):
        from app.tool_execution import (
            _SSRF_BLOCKED_NETWORKS,
            _url_host_is_blocked_literal_ip,
        )
        assert _SSRF_BLOCKED_NETWORKS is BLOCKED_NETWORKS
        assert _url_host_is_blocked_literal_ip is host_is_blocked_literal_ip

    def test_both_sinks_share_one_policy_object(self):
        """Same object, not a copy -- a copy would be free to drift."""
        import app.tool_execution as te

        assert te._SSRF_BLOCKED_NETWORKS is BLOCKED_NETWORKS
