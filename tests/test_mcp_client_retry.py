"""
Retry-semantics tests for the single-reader MCP transport.

HISTORY: the original version of this file mocked ``process.stdout.readline``
to return a canned response on EVERY call and NEVER signal EOF.  Under the
single-reader rewrite of ``app/mcp/client.py`` (one background ``_reader_loop``
that owns stdout and loops until ``readline()`` returns ``b""``), an
always-returning mock makes the reader spin forever and the test hangs.

The reader-model-correct way to assert retry behavior is NOT to count
``readline`` calls (the caller never calls ``readline`` now — the background
reader does) but to count REQUESTS WRITTEN TO STDIN.  A retry == a second
write with an incremented JSON-RPC id.  These tests use a queue-backed fake
stdout that the reader drains and an auto-responder on ``stdin.write`` that
enqueues the configured response for each request id, so the reader always
reaches a terminal state and the test completes.
"""

import asyncio
import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.mcp.client import MCPClient


# ---------------------------------------------------------------------------
# Fake async subprocess (queue-backed stdout the reader can drain + terminate)
# ---------------------------------------------------------------------------

class _FakeStdout:
    """asyncio.StreamReader-shaped fake: ``readline()`` awaits a queue, so the
    reader loop parks (not spins) when no data is available and terminates
    cleanly when fed ``b""`` (EOF)."""

    def __init__(self):
        self._q: asyncio.Queue = asyncio.Queue()

    async def readline(self) -> bytes:
        return await self._q.get()

    def feed_line(self, data: bytes):
        self._q.put_nowait(data)


def _make_client(on_write):
    """Build a real MCPClient whose ``stdin.write`` invokes
    ``on_write(parsed_request, stdout)`` so a test can auto-enqueue the matching
    response onto the fake stdout."""
    client = MCPClient({"name": "test-server", "command": ["echo"]})
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
    """Stop the background reader so no task leaks past the test."""
    t = getattr(client, "_reader_task", None)
    if t and not t.done():
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass


def _err(rid, code, message):
    return (json.dumps({"jsonrpc": "2.0", "id": rid,
                        "error": {"code": code, "message": message}}) + "\n").encode()


def _ok(rid, text="success"):
    return (json.dumps({"jsonrpc": "2.0", "id": rid,
                        "result": {"content": [{"type": "text", "text": text}]}}) + "\n").encode()


class TestSendRequestRetryLogic:
    """Retry behavior re-expressed as 'requests written to stdin', the
    model-independent ground truth (one write == one attempt)."""

    @pytest.mark.asyncio
    async def test_blocked_error_not_retried(self):
        """BLOCKED shell errors return immediately without a second write."""
        writes = {"n": 0}

        def on_write(req, stdout):
            writes["n"] += 1
            stdout.feed_line(_err(req["id"], -32602,
                                  "🚫 BLOCKED: '{' is not allowed\n\n📋 Allowed commands: ls, cat"))

        client, _ = _make_client(on_write)
        try:
            result = await client._send_request(
                "tools/call", {"name": "run_shell_command", "arguments": {"command": "{"}})
        finally:
            await _shutdown(client)

        assert result is not None
        assert result.get("error") is True
        assert result.get("policy_block") is True
        assert "BLOCKED" in result.get("message", "")
        assert writes["n"] == 1, f"BLOCKED must not retry; got {writes['n']} writes"

    @pytest.mark.asyncio
    async def test_write_blocked_error_not_retried(self):
        """WRITE BLOCKED errors also return immediately."""
        writes = {"n": 0}

        def on_write(req, stdout):
            writes["n"] += 1
            stdout.feed_line(_err(req["id"], -32602, "🚫 WRITE BLOCKED: sed -i is not allowed"))

        client, _ = _make_client(on_write)
        try:
            result = await client._send_request(
                "tools/call", {"name": "run_shell_command",
                               "arguments": {"command": "sed -i 's/a/b/' f.py"}})
        finally:
            await _shutdown(client)

        assert result.get("policy_block") is True
        assert writes["n"] == 1

    @pytest.mark.asyncio
    async def test_security_block_not_retried(self):
        """SECURITY BLOCK errors return immediately (pre-existing behavior)."""
        writes = {"n": 0}

        def on_write(req, stdout):
            writes["n"] += 1
            stdout.feed_line(_err(req["id"], -32602, "SECURITY BLOCK: dangerous operation"))

        client, _ = _make_client(on_write)
        try:
            result = await client._send_request(
                "tools/call", {"name": "run_shell_command", "arguments": {"command": "rm -rf /"}})
        finally:
            await _shutdown(client)

        assert result.get("error") is True
        assert writes["n"] == 1

    @pytest.mark.asyncio
    async def test_timeout_error_not_retried(self):
        """A timeout-coded error RESPONSE (not a readline timeout) returns
        immediately so the model can pick a lighter alternative."""
        writes = {"n": 0}

        def on_write(req, stdout):
            writes["n"] += 1
            stdout.feed_line(_err(req["id"], -32603, "Command timed out after 30 seconds"))

        client, _ = _make_client(on_write)
        try:
            result = await client._send_request(
                "tools/call", {"name": "run_shell_command", "arguments": {"command": "sleep 999"}})
        finally:
            await _shutdown(client)

        assert result.get("error") is True
        assert writes["n"] == 1, f"timeout error must not retry; got {writes['n']} writes"

    @pytest.mark.asyncio
    async def test_transient_error_retried_then_succeeds(self):
        """ExtractArticle.js transient failure: first two attempts error, the
        third succeeds.  Reader model: three distinct writes (ids 1,2,3)."""
        writes = {"n": 0}
        seen_ids = []

        def on_write(req, stdout):
            writes["n"] += 1
            seen_ids.append(req["id"])
            if writes["n"] <= 2:
                stdout.feed_line(_err(req["id"], -32603,
                                      "ExtractArticle.js failed with non-zero exit status"))
            else:
                stdout.feed_line(_ok(req["id"], "fetched"))

        client, _ = _make_client(on_write)
        try:
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await client._send_request(
                    "tools/call", {"name": "fetch", "arguments": {"url": "https://example.com"}})
        finally:
            await _shutdown(client)

        assert writes["n"] == 3, f"expected 2 retries then success; got {writes['n']} writes"
        assert seen_ids == [1, 2, 3], f"ids must be monotonic across retries; got {seen_ids}"
        assert result == {"content": [{"type": "text", "text": "fetched"}]}


class TestPatternMatchingUnchanged:
    """The two pure-pattern tests have no transport and must still hold
    verbatim — they pin the tightened external-error retry patterns."""

    def test_generic_patterns_dont_match_blocked(self):
        blocked_msg = (
            "🚫 BLOCKED: '{' is not allowed\n\n"
            "📋 Allowed commands: ls, cat, grep\n\n"
            "💡 Tip: configure in Shell Configuration settings."
        )
        external_server_errors = [
            "ExtractArticle.js", "non-zero exit status",
            "temporary failure", "temporarily unavailable", "server is busy",
        ]
        assert not any(p in blocked_msg for p in external_server_errors)

    def test_old_broad_patterns_match_unrelated_errors(self):
        old_patterns = [
            "ExtractArticle.js", "non-zero exit status", "Command", "returned",
            "cache", "processing", "temporary", "busy",
        ]
        new_patterns = [
            "ExtractArticle.js", "non-zero exit status",
            "temporary failure", "temporarily unavailable", "server is busy",
        ]
        non_transient_errors = [
            "Command not found: foobar",
            "Error processing request parameters",
            "Resource busy: file locked by user",
            "Invalid cache key format",
            "Function returned unexpected type",
        ]
        for error_msg in non_transient_errors:
            assert any(p in error_msg for p in old_patterns), error_msg
            assert not any(p in error_msg for p in new_patterns), error_msg
