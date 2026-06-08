"""
Timeout-alignment tests for the single-reader MCP transport.

HISTORY: the original version mocked ``process.stdout.readline`` to return a
canned response on every call (and one variant that raised TimeoutError).
Under the single-reader rewrite of ``app/mcp/client.py``:
  * an always-returning ``readline`` mock spins the background ``_reader_loop``
    forever (the loop only stops on EOF ``b""``), hanging the test; and
  * a ``readline`` that raises is now handled by the reader (which fails all
    pending futures with EOF), so the old per-request "timed out after N
    seconds" message no longer comes from that path.

What is PRESERVED and still worth pinning:
  * the ``timeout_duration`` COMPUTATION (default 30s; external-server floor
    60s; ``tools/call`` extends to ``arg + 10``; string/invalid handling).
    ``_send_request`` still passes that value to ``asyncio.wait_for`` — now
    wrapping the response *future* rather than ``readline()`` — so spying
    ``asyncio.wait_for`` still captures it.
  * the per-request timeout MESSAGE ("Request timed out after N seconds"),
    which now fires when the future await times out.

These tests therefore keep the original assertions but drive the new
transport: responses are fed through a queue the reader drains (so it parks,
never spins), and the future-await timeout is exercised directly.
"""

import asyncio
import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.mcp.client import MCPClient


# ---------------------------------------------------------------------------
# Queue-backed fake subprocess (reader drains it and can reach EOF).
# ---------------------------------------------------------------------------

class _FakeStdout:
    def __init__(self):
        self._q: asyncio.Queue = asyncio.Queue()

    async def readline(self) -> bytes:
        return await self._q.get()

    def feed_line(self, data: bytes):
        self._q.put_nowait(data)


def _make_client(on_write, server_name="test-shell"):
    """Real MCPClient whose stdin.write invokes on_write(req, stdout)."""
    client = MCPClient({"name": server_name, "command": ["echo"]})
    client.is_connected = True

    stdout = _FakeStdout()
    proc = MagicMock()
    proc.returncode = None
    proc.stdin = MagicMock()

    def _write(data: bytes):
        req = json.loads(data.decode("utf-8"))
        on_write(req, stdout)

    proc.stdin.write = _write
    proc.stdin.drain = AsyncMock()
    proc.stdout = stdout
    client.process = proc
    return client, stdout


async def _shutdown(client):
    t = getattr(client, "_reader_task", None)
    if t and not t.done():
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass


def _ok(rid, text="ok"):
    return (json.dumps({"jsonrpc": "2.0", "id": rid,
                        "result": {"content": [{"type": "text", "text": text}]}}) + "\n").encode()


async def _capture_timeout(client, method, params):
    """Run _send_request while spying asyncio.wait_for; return the list of
    timeout values it was called with.  The only wait_for in the request path
    is the one wrapping the response future, so this captures timeout_duration.
    An auto-responder feeds the matching response so the call completes fast.
    """
    captured = []
    original_wait_for = asyncio.wait_for

    async def spy_wait_for(coro, timeout=None):
        captured.append(timeout)
        return await original_wait_for(coro, timeout=timeout)

    try:
        with patch("asyncio.wait_for", side_effect=spy_wait_for):
            await client._send_request(method, params)
    finally:
        await _shutdown(client)
    return captured


class TestReadlineTimeoutAlignment:
    """The timeout_duration computation is unchanged by the reader rewrite;
    it is still handed to asyncio.wait_for (now wrapping the future)."""

    @pytest.mark.asyncio
    async def test_default_timeout_is_30s(self):
        """Non-tool requests use the default 30s timeout."""
        client, _ = _make_client(lambda req, out: out.feed_line(_ok(req["id"])))
        captured = await _capture_timeout(client, "initialize", {})
        assert any(t == 30.0 for t in captured), f"Expected 30.0s, got: {captured}"

    @pytest.mark.asyncio
    async def test_tool_timeout_extends_readline(self):
        """tools/call with timeout=120 extends the wait to 130 (120 + 10)."""
        client, _ = _make_client(lambda req, out: out.feed_line(_ok(req["id"])))
        captured = await _capture_timeout(client, "tools/call", {
            "name": "run_shell_command",
            "arguments": {"command": "sleep 100", "timeout": 120},
        })
        assert any(t == 130.0 for t in captured), f"Expected 130.0s, got: {captured}"

    @pytest.mark.asyncio
    async def test_tool_timeout_string_converted(self):
        """timeout passed as a string is converted to float (60 -> 70)."""
        client, _ = _make_client(lambda req, out: out.feed_line(_ok(req["id"])))
        captured = await _capture_timeout(client, "tools/call", {
            "name": "run_shell_command",
            "arguments": {"command": "echo hi", "timeout": "60"},
        })
        assert any(t == 70.0 for t in captured), f"Expected 70.0s for '60', got: {captured}"

    @pytest.mark.asyncio
    async def test_tool_timeout_does_not_shrink_external_default(self):
        """A small tool timeout doesn't shrink below the external 60s floor."""
        client, _ = _make_client(lambda req, out: out.feed_line(_ok(req["id"])),
                                  server_name="fetch-external")
        captured = await _capture_timeout(client, "tools/call", {
            "name": "fetch",
            "arguments": {"url": "https://example.com", "timeout": 5},
        })
        assert any(t == 60.0 for t in captured), \
            f"Expected 60.0s (external floor preserved), got: {captured}"

    @pytest.mark.asyncio
    async def test_invalid_tool_timeout_uses_default(self):
        """Invalid timeout value falls back to default 30s."""
        client, _ = _make_client(lambda req, out: out.feed_line(_ok(req["id"])))
        captured = await _capture_timeout(client, "tools/call", {
            "name": "run_shell_command",
            "arguments": {"command": "echo hi", "timeout": "not-a-number"},
        })
        assert any(t == 30.0 for t in captured), f"Expected 30.0s fallback, got: {captured}"

    @pytest.mark.asyncio
    async def test_no_tool_timeout_uses_default(self):
        """tools/call without a timeout param uses default 30s."""
        client, _ = _make_client(lambda req, out: out.feed_line(_ok(req["id"])))
        captured = await _capture_timeout(client, "tools/call", {
            "name": "run_shell_command",
            "arguments": {"command": "echo hi"},
        })
        assert any(t == 30.0 for t in captured), f"Expected 30.0s (no param), got: {captured}"

    @pytest.mark.asyncio
    async def test_timeout_error_message_reflects_actual_duration(self):
        """When the response future times out, the error message reports the
        computed duration (130 = 120 + 10), not a hardcoded 30s.  We drive the
        future-await timeout directly by patching asyncio.wait_for to raise."""
        client, _ = _make_client(lambda req, out: None)  # never responds

        async def raise_timeout(coro, timeout=None):
            # Close the un-awaited coroutine to avoid a 'never awaited' warning.
            if asyncio.iscoroutine(coro):
                coro.close()
            raise asyncio.TimeoutError()

        try:
            with patch("asyncio.wait_for", side_effect=raise_timeout):
                result = await client._send_request("tools/call", {
                    "name": "run_shell_command",
                    "arguments": {"command": "sleep 200", "timeout": 120},
                })
        finally:
            await _shutdown(client)

        assert result["error"] is True
        assert "130 seconds" in result["message"], \
            f"Expected '130 seconds' in message, got: {result['message']}"


class TestTimeoutChainDocumentation:
    """Verify the timeout layers are aligned.  These do not touch the
    transport and are preserved verbatim from the original suite."""

    def test_shell_server_accepts_timeout_param(self):
        """Shell server schema advertises the timeout parameter."""
        import importlib
        shell_mod = importlib.import_module("app.mcp_servers.shell_server")
        server = shell_mod.ShellServer()

        loop = asyncio.new_event_loop()
        try:
            response = loop.run_until_complete(
                server.handle_request({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/list",
                    "params": {},
                })
            )
        finally:
            loop.close()

        tools = response["result"]["tools"]
        shell_tool = next(t for t in tools if t["name"] == "run_shell_command")
        props = shell_tool["inputSchema"]["properties"]

        assert "timeout" in props, "Shell tool must expose timeout parameter"
        assert props["timeout"]["type"] == "number"

    def test_shell_server_max_timeout_matches_tool_exec(self):
        """Shell server max_timeout should not exceed TOOL_EXEC_TIMEOUT."""
        import importlib
        import os

        shell_mod = importlib.import_module("app.mcp_servers.shell_server")
        server = shell_mod.ShellServer()

        tool_exec_timeout = int(os.environ.get('TOOL_EXEC_TIMEOUT', '300'))

        assert server.max_timeout <= tool_exec_timeout, \
            f"Shell max_timeout ({server.max_timeout}) exceeds TOOL_EXEC_TIMEOUT ({tool_exec_timeout})"
