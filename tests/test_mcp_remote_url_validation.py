"""
ASR EGR-03 — the remote-MCP connect path must validate its URL.

``MCPClient._connect_remote`` previously took ``server_config["url"]``
verbatim and handed it to the SDK transport. On a corp developer desktop
internal hosts are reachable by default, so an unvalidated URL here is an
internal-pivot / IMDS-credential-vending SSRF, reachable from a registry
response or a hand-edited ``mcp_config.json``.

The refusal assertions are paired with a positive control: a legitimate public
URL must still reach transport construction. Without that pairing a guard that
refused *everything* would satisfy the rejection cases and silently break every
remote MCP server.
"""

import pytest

from app.mcp.client import MCPClient


class _TransportReached(Exception):
    """Marker raised in place of the real transport.

    Deliberately NOT one of (ImportError, OSError, RuntimeError,
    asyncio.TimeoutError, ConnectionError) -- the ones ``_connect_remote``
    catches -- so it propagates out of the coroutine and is unambiguous
    evidence that execution got past the URL guard.
    """


@pytest.fixture
def transport_marker(monkeypatch):
    """Replace the StreamableHTTP transport with the marker."""
    def _boom(*args, **kwargs):
        raise _TransportReached(args[0] if args else "")

    monkeypatch.setattr(
        "mcp.client.streamable_http.streamable_http_client", _boom
    )
    return _boom


def _client(url, name="remote-test"):
    return MCPClient({"name": name, "url": url})


REFUSED_URLS = [
    pytest.param("http://169.254.169.254/latest/meta-data/", id="imds"),
    pytest.param("http://127.0.0.1:6969/sse", id="loopback"),
    pytest.param("http://[::1]:6969/sse", id="loopback-v6"),
    pytest.param("http://10.1.2.3/mcp", id="rfc1918-10"),
    pytest.param("http://192.168.0.10/mcp", id="rfc1918-192"),
    pytest.param("http://172.20.0.5/mcp", id="rfc1918-172"),
    pytest.param("file:///etc/passwd", id="file-scheme"),
    pytest.param("gopher://example.com/", id="gopher-scheme"),
    pytest.param("example.com/mcp", id="schemeless"),
    pytest.param("http://", id="no-host"),
    pytest.param("", id="empty"),
    pytest.param(None, id="missing"),
]


class TestRefusedUrls:
    @pytest.mark.parametrize("url", REFUSED_URLS)
    async def test_connect_remote_refuses(self, url, transport_marker):
        client = _client(url)
        assert await client._connect_remote() is False

    @pytest.mark.parametrize("url", REFUSED_URLS)
    async def test_no_transport_constructed(self, url, transport_marker):
        """The marker must never fire -- the guard runs before the transport."""
        client = _client(url)
        result = await client._connect_remote()  # would raise if it got through
        assert result is False

    @pytest.mark.parametrize("url", REFUSED_URLS)
    async def test_no_session_left_behind(self, url, transport_marker):
        client = _client(url)
        await client._connect_remote()
        assert client.is_connected is False
        assert client._sdk_session is None
        assert client._sdk_exit_stack is None

    async def test_refusal_is_surfaced_in_client_logs(self, transport_marker):
        """The GUI reads client.logs to explain a startup failure, so a silent
        refusal would look like an unexplained dead server."""
        client = _client("http://169.254.169.254/")
        await client._connect_remote()
        joined = "\n".join(client.logs)
        assert "ERROR" in joined
        assert "169.254.169.254" in joined


class TestPositiveControl:
    async def test_public_url_reaches_transport_construction(self, transport_marker):
        """Proves the path runs at all, and that the guard is not a blanket
        refusal that happens to satisfy every rejection case above."""
        client = _client("https://mcp.example.com/mcp")
        with pytest.raises(_TransportReached) as exc:
            await client._connect_remote()
        assert "https://mcp.example.com/mcp" in str(exc.value)

    async def test_validated_url_passed_through_unmodified(self, transport_marker):
        """Whitespace is stripped but the destination is otherwise untouched --
        a guard that rewrote the URL would be a different bug."""
        client = _client("  https://mcp.example.com/mcp?x=1  ")
        with pytest.raises(_TransportReached) as exc:
            await client._connect_remote()
        assert str(exc.value) == "https://mcp.example.com/mcp?x=1"


class TestEntryPointSeam:
    """The guard has to sit on the path that actually runs.

    ``connect()`` dispatches to ``_connect_remote()`` for any config carrying a
    ``url``. Asserting through ``connect()`` as well pins that a remote config
    cannot reach the transport by some other route.
    """

    async def test_connect_refuses_internal_url(self, transport_marker):
        client = _client("http://169.254.169.254/")
        assert client._is_remote is True
        assert await client.connect() is False
        assert client.is_connected is False

    async def test_connect_routes_remote_configs_to_the_guarded_path(
        self, monkeypatch
    ):
        """A url-bearing config must be dispatched to ``_connect_remote``.

        Asserted with a spy rather than by letting an exception escape:
        ``connect()`` is the outer resilience boundary and catches broadly, so
        nothing thrown deeper is observable through it. What matters for the
        finding is that remote configs cannot reach a transport by any route
        that skips the guarded one.
        """
        client = _client("https://mcp.example.com/mcp")
        calls = []

        async def _spy():
            calls.append(client.server_config.get("url"))
            return True

        monkeypatch.setattr(client, "_connect_remote", _spy)
        assert await client.connect() is True
        assert calls == ["https://mcp.example.com/mcp"]
