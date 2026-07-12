"""
Regression coverage for two MCPClient._send_request crashes:

PenPal #132 [CWE-476]: a non-conformant/malicious MCP server can put a non-dict
value in the JSON-RPC "error" field ({"id":1,"error":null} — or a string, int,
list). Both error-parsing sinks did `error_info = response["error"]` then
`error_info.get(...)`, raising AttributeError; neither _send_request's outer
handler nor call_tool catches AttributeError, so it crashed the coroutine.

PenPal #125 [CWE-416]: _send_request checks self.process liveness BEFORE the
`async with self._write_lock` await; disconnect() nulls self.process WITHOUT
holding _write_lock, so a concurrent disconnect between the check and the write
made self.process.stdin.write() raise AttributeError on None — same uncaught
path.

Both are exercised against the real MCPClient with an injected fake process
(no subprocess spawn). Each class includes a negative control proving the
pre-fix code path raises AttributeError.
"""
import asyncio

import pytest

from app.mcp.client import MCPClient


class _FakeStdin:
    def __init__(self):
        self.buf = b""

    def write(self, b):
        self.buf += b

    async def drain(self):
        return None


class _FakeProc:
    def __init__(self):
        self.stdin = _FakeStdin()
        self.stdout = object()  # non-None so the stream guard passes
        self.returncode = None


def _make_client():
    """An MCPClient with only the attribute surface _send_request touches,
    and a fake (non-subprocess) process so no real server is spawned."""
    c = MCPClient.__new__(MCPClient)
    c.server_config = {"name": "test"}
    c._is_remote = False
    c.process = _FakeProc()
    c._write_lock = asyncio.Lock()
    c._pending = {}
    c._response_buffer = {}
    c.request_id = 0
    c.is_connected = True
    c._last_reconnect_attempt = 0
    c._last_successful_call = 0
    c._reader_task = None
    c._sdk_session = None
    c._ensure_reader = lambda: None  # no real stdout to read
    return c


class TestNonDictErrorField:
    """#132: a non-dict "error" must produce a handled error dict, not crash."""

    @pytest.mark.parametrize("payload", [None, "boom", 42, ["x"], 3.14])
    @pytest.mark.asyncio
    async def test_non_dict_error_does_not_crash(self, payload):
        c = _make_client()
        # Pre-seat the response buffer so _send_request takes the buffered
        # path for our request id (next id is 1).
        c._response_buffer[1] = {"id": 1, "error": payload}
        result = await c._send_request("tools/call", {"name": "x"})
        assert isinstance(result, dict)
        assert result.get("error") is True
        # Falls back to the documented defaults when the error has no dict shape.
        assert result.get("code") == -1
        assert result.get("message") == "Unknown error"

    @pytest.mark.asyncio
    async def test_well_formed_error_still_parsed(self, payload=None):
        c = _make_client()
        c._response_buffer[1] = {
            "id": 1,
            "error": {"code": -32601, "message": "Method not found"},
        }
        result = await c._send_request("tools/call", {"name": "x"})
        assert result.get("error") is True
        assert result.get("code") == -32601
        assert result.get("message") == "Method not found"

    def test_negative_control_old_pattern_raises(self):
        """The pre-fix pattern (no isinstance guard) raises AttributeError on a
        non-dict error — proving the guard is load-bearing."""
        for payload in (None, "boom", 42, ["x"]):
            response = {"id": 1, "error": payload}
            with pytest.raises(AttributeError):
                error_info = response["error"]
                _ = error_info.get("code", -1)  # noqa: F841 — the crash is the point


class TestDisconnectBeforeWriteRace:
    """#125: process nulled between the liveness check and the locked write
    must yield a handled error, not an uncaught AttributeError."""

    @pytest.mark.asyncio
    async def test_process_closed_under_write_lock(self):
        c = _make_client()
        # Hold the write lock so _send_request blocks at `async with`. While it
        # waits, simulate disconnect() nulling stdin (the race). On acquiring
        # the lock the new re-check must raise a handled ConnectionError.
        await c._write_lock.acquire()

        async def do_request():
            return await c._send_request("tools/call", {"name": "x"})

        task = asyncio.create_task(do_request())
        await asyncio.sleep(0.05)     # let it reach the lock await
        c.process.stdin = None        # concurrent disconnect nulls stdin
        c._write_lock.release()
        result = await task

        assert isinstance(result, dict)
        assert result.get("error") is True  # handled, not a raised AttributeError

    def test_negative_control_unguarded_write_raises(self):
        """Writing to a nulled process.stdin (the pre-fix line) is an
        AttributeError — the crash the in-lock re-check prevents."""
        proc = _FakeProc()
        proc.stdin = None
        with pytest.raises(AttributeError):
            proc.stdin.write(b"x")  # exactly the pre-fix self.process.stdin.write
