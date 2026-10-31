"""
End to end: MCPClient's remote path against a REAL in-process MCP server
built with the installed SDK (FastMCP on 1.x, MCPServer on 2.x), over the
SDK's real StreamableHTTP transport.  No SDK object is faked.

The HTTP client reaches the server through an ASGI transport, injected the
way production injects an authenticated client: a RemoteAuthProvider.  That
also exercises the provider seam (the engine must accept the client type the
installed SDK expects).

Run under both lines:
    python -m pytest tests/test_mcp_remote_sdk_e2e.py
    PYTHONPATH=<dir with mcp 2.x> python -m pytest tests/test_mcp_remote_sdk_e2e.py
"""

import asyncio
import base64

import pytest

from app.mcp.client import MCPClient
from app.plugins import _remote_auth_providers
from app.plugins.interfaces import RemoteAuthProvider
from app.utils import mcp_sdk_compat as compat

URL = "http://127.0.0.1:8000/mcp"
GIF = base64.b64decode("R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==")


def _build_server():
    # ToolError, not a plain exception: 2.x reports a plain exception as
    # "Error executing tool <name>" without its text.
    if compat.sdk_major() == 2:
        from mcp.server.mcpserver import MCPServer as Server, Image
        from mcp.server.mcpserver.exceptions import ToolError
        import mcp_types as T
    else:
        from mcp.server.fastmcp import FastMCP as Server, Image
        from mcp.server.fastmcp.exceptions import ToolError
        import mcp.types as T

    srv = Server("e2e", json_response=True, stateless_http=True) \
        if compat.sdk_major() == 1 else Server("e2e")

    @srv.tool(annotations=T.ToolAnnotations(readOnlyHint=True))
    def echo(text: str) -> str:
        return f"echo:{text}"

    @srv.tool()
    def pixel() -> Image:
        return Image(data=GIF, format="gif")

    @srv.tool()
    def boom() -> str:
        raise ToolError("kaboom")

    if compat.sdk_major() == 2:
        app = srv.streamable_http_app(json_response=True, stateless_http=True)
    else:
        app = srv.streamable_http_app()
    return srv, app


class _AsgiProvider(RemoteAuthProvider):
    provider_id = "asgi-e2e"
    auth_type = "asgi-e2e"
    http_libraries = ("httpx", "httpx2")

    def __init__(self, app):
        self.app = app
        self.built = None

    async def build_http_client(self, url, server_config, *, headers=None, timeout=None):
        h = compat.http_module()
        self.built = h.AsyncClient(transport=h.ASGITransport(app=self.app),
                                   headers=headers or {}, timeout=timeout)
        return self.built


@pytest.fixture
def remote(monkeypatch):
    srv, app = _build_server()
    provider = _AsgiProvider(app)
    monkeypatch.setitem(_remote_auth_providers, "asgi-e2e", provider)
    # Loopback is refused by the SSRF guard; the server is in-process.
    monkeypatch.setattr("app.utils.net_guard.validate_outbound_url",
                        lambda url, **_kw: url)
    return srv, provider


def _run(srv, scenario):
    async def outer():
        async with srv.session_manager.run():
            client = MCPClient({"name": "e2e", "url": URL,
                                "transport": "streamable-http",
                                "auth": {"type": "asgi-e2e"}})
            try:
                return client, await scenario(client)
            finally:
                await client.disconnect()
    return asyncio.run(outer())


def test_connect_lists_tools_with_schema_and_wire_annotations(remote):
    srv, provider = remote

    async def scenario(client):
        return await client.connect()

    client, ok = _run(srv, scenario)
    assert ok is True, client.logs
    assert client.startup_failure is None
    assert provider.built is not None and compat.is_compatible_client(provider.built)
    tools = {t.name: t for t in client.tools}
    assert set(tools) == {"echo", "pixel", "boom"}
    assert tools["echo"].inputSchema["properties"]["text"]["type"] == "string"
    assert tools["echo"].annotations.get("readOnlyHint") is True


def test_call_tool_text_image_and_error(remote):
    srv, _ = remote

    async def scenario(client):
        assert await client.connect() is True, client.logs
        return (await client.call_tool("echo", {"text": "hi"}),
                await client.call_tool("pixel", {}),
                await client.call_tool("boom", {}))

    _, (echo, pixel, boom) = _run(srv, scenario)

    assert not echo.get("error"), echo
    assert echo["content"][0] == {"type": "text", "text": "echo:hi"}

    assert not pixel.get("error"), pixel
    img = pixel["content"][0]
    assert img["type"] == "image" and img["mimeType"] == "image/gif"
    assert base64.b64decode(img["data"]) == GIF

    # Silent on 2.x with bare getattr(result, "isError", False).
    assert boom.get("error") is True, boom
    assert "kaboom" in boom["message"]


def test_provider_that_cannot_build_this_sdks_client_is_refused(monkeypatch, remote):
    """A provider declaring only the OTHER library fails with a named cause
    before any connection is attempted."""
    srv, provider = remote
    other = "httpx" if compat.http_library_name() == "httpx2" else "httpx2"
    monkeypatch.setattr(provider, "http_libraries", (other,), raising=False)

    async def scenario(client):
        return await client.connect()

    client, ok = _run(srv, scenario)
    assert ok is False
    assert client.startup_failure["code"] == "remote_auth_sdk_mismatch"
    assert provider.built is None
