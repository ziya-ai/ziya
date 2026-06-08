"""
Concurrency + demux tests for the single-reader MCP transport.

These pin the behavior introduced when app/mcp/client.py was rewritten from
a "every caller reads stdout under a shared _io_lock" model to a single
background reader task (_reader_loop) plus a future-per-request demux
(_pending keyed by JSON-RPC id).

The decisive property is test_slow_request_does_not_block_fast_request:
under the OLD model a slow in-flight request serialized all others behind
the lock; under the new model concurrent requests are mutually
non-blocking and each is bounded only by its own timeout.

NOTE on mocking model: because the background reader owns stdout, tests
drive a fake process whose stdin write callback decides how/when to push
responses onto a stdout queue — they do NOT mock readline to always-return
(that would feed the continuous reader loop indefinitely). This is the
correct model for the single-reader transport and differs from the older
test_mcp_client_retry.py / test_mcp_client_timeout.py mocking style.
"""

import asyncio
import json
import time
import types

import pytest
from unittest.mock import AsyncMock, patch


# ---------------------------------------------------------------------------
# Fake async subprocess: a stdin whose writes invoke a server callback, and a
# stdout backed by a queue the callback (or test) pushes response lines onto.
# ---------------------------------------------------------------------------

class _FakeStdout:
    def __init__(self):
        self.q: asyncio.Queue = asyncio.Queue()

    async def readline(self) -> bytes:
        return await self.q.get()

    def push(self, line: bytes):
        self.q.put_nowait(line)


class _FakeStdin:
    def __init__(self, on_write):
        self._on_write = on_write

    def write(self, data: bytes):
        self._on_write(data)

    async def drain(self):
        await asyncio.sleep(0)


def _make_client(server_name="test-shell"):
    """Real MCPClient wired to a controllable fake subprocess."""
    from app.mcp.client import MCPClient

    holder = {}

    def on_write(data: bytes):
        req = json.loads(data.decode())
        handler = holder.get("handler")
        if handler:
            handler(req)

    client = MCPClient({"name": server_name, "command": ["echo"]})
    client.is_connected = True
    proc = types.SimpleNamespace()
    proc.returncode = None
    proc.stdout = _FakeStdout()
    proc.stdin = _FakeStdin(on_write)
    proc.stderr = None
    client.process = proc
    return client, proc, holder


def _resp(req_id, text):
    return (json.dumps({
        "jsonrpc": "2.0", "id": req_id,
        "result": {"content": [{"type": "text", "text": text}]},
    }) + "\n").encode()


def _err(req_id, code, message):
    return (json.dumps({
        "jsonrpc": "2.0", "id": req_id,
        "error": {"code": code, "message": message},
    }) + "\n").encode()


# ---------------------------------------------------------------------------
# Non-blocking concurrency
# ---------------------------------------------------------------------------

class TestNonBlockingConcurrency:
    @pytest.mark.asyncio
    async def test_slow_request_does_not_block_fast_request(self):
        """A slow in-flight request must not block a fast concurrent one.
        Under the old shared-read-lock model this serialized; now it does not."""
        client, proc, holder = _make_client()

        def handler(req):
            rid = req["id"]
            if rid == 1:  # answer A only after 0.5s
                async def late():
                    await asyncio.sleep(0.5)
                    proc.stdout.push(_resp(rid, "A-SLOW"))
                asyncio.create_task(late())
            else:  # answer everyone else immediately
                proc.stdout.push(_resp(rid, "B-FAST"))

        holder["handler"] = handler

        t0 = time.monotonic()
        a_task = asyncio.create_task(
            client._send_request("tools/call", {"name": "A", "arguments": {}}))
        await asyncio.sleep(0.05)  # ensure A is registered/written first
        b_result = await client._send_request("tools/call", {"name": "B", "arguments": {}})
        b_elapsed = time.monotonic() - t0

        assert b_result["content"][0]["text"] == "B-FAST"
        assert b_elapsed < 0.3, f"fast request blocked {b_elapsed:.3f}s behind slow one"
        assert not a_task.done(), "slow request should still be in flight"

        a_result = await asyncio.wait_for(a_task, timeout=5)
        assert a_result["content"][0]["text"] == "A-SLOW"

    @pytest.mark.asyncio
    async def test_many_concurrent_callers_get_own_responses_out_of_order(self):
        """N concurrent callers, answered out of request order, each receive
        exactly their own id's payload."""
        client, proc, holder = _make_client()

        def handler(req):
            rid = req["id"]
            async def answer():
                await asyncio.sleep((7 - rid % 7) * 0.01)  # scramble order
                proc.stdout.push(_resp(rid, f"R{rid}"))
            asyncio.create_task(answer())

        holder["handler"] = handler

        results = await asyncio.gather(*[
            client._send_request("tools/call", {"name": str(i), "arguments": {}})
            for i in range(25)
        ])
        assert [r["content"][0]["text"] for r in results] == [f"R{i}" for i in range(1, 26)]

    @pytest.mark.asyncio
    async def test_one_timeout_does_not_disturb_concurrent_caller(self):
        """A request that times out does not affect a concurrent request that
        is answered normally."""
        client, proc, holder = _make_client()

        def handler(req):
            rid = req["id"]
            if rid != 1:  # never answer id 1
                proc.stdout.push(_resp(rid, f"R{rid}"))

        holder["handler"] = handler

        a_task = asyncio.create_task(client._send_request(
            "tools/call", {"name": "A", "arguments": {"timeout": 0.2}}))
        await asyncio.sleep(0.02)
        b_result = await client._send_request("tools/call", {"name": "B", "arguments": {}})
        assert b_result["content"][0]["text"] == "R2"

        a_task.cancel()
        try:
            await a_task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# Demux correctness
# ---------------------------------------------------------------------------

class TestDemuxCorrectness:
    @pytest.mark.asyncio
    async def test_out_of_order_response_buffered_not_returned(self):
        client, proc, holder = _make_client()

        def handler(req):
            rid = req["id"]
            proc.stdout.push(_resp(999, "NOT-OURS"))  # unrelated id first
            proc.stdout.push(_resp(rid, "OURS"))

        holder["handler"] = handler
        result = await client._send_request("tools/call", {"name": "x", "arguments": {}})
        assert result["content"][0]["text"] == "OURS"
        assert 999 in client._response_buffer

    @pytest.mark.asyncio
    async def test_malformed_and_fragment_lines_skipped(self):
        """Stray log lines and chunked fragments are skipped by the reader and
        never misassembled into a wrong-id result."""
        client, proc, holder = _make_client()

        def handler(req):
            rid = req["id"]
            proc.stdout.push(b"stray log line, not json\n")
            full = json.dumps({"jsonrpc": "2.0", "id": rid,
                               "result": {"content": [{"type": "text", "text": "X"}]}})
            half = len(full) // 2
            proc.stdout.push(full[:half].encode() + b"\n")   # fragment 1
            proc.stdout.push(full[half:].encode() + b"\n")   # fragment 2
            proc.stdout.push(_resp(rid, "CLEAN"))

        holder["handler"] = handler
        result = await client._send_request("tools/call", {"name": "x", "arguments": {}})
        assert result["content"][0]["text"] == "CLEAN"

    @pytest.mark.asyncio
    async def test_late_response_after_timeout_buffered_not_poisoning(self):
        """A response arriving after its call timed out lands inert in the
        bounded buffer; a subsequent call (larger, monotonic id) is unaffected."""
        client, proc, holder = _make_client()
        deferred = {}

        def handler(req):
            rid = req["id"]
            if rid == 1:
                deferred["a_id"] = rid  # withhold A's answer
            else:
                proc.stdout.push(_resp(rid, f"R{rid}"))

        holder["handler"] = handler
        # The effective timeout floor is 30s (a 0.2 arg only ever extends,
        # never shrinks below the default), so clamp the response-future await
        # to a fraction of a second.  Only the future await is wrapped in the
        # request path, so this exercises the real timeout-handling code path
        # without a 30s wall-clock wait.
        _real_wait_for = asyncio.wait_for

        async def _clamped_wait_for(coro, timeout=None):
            if timeout is not None and timeout > 1:
                timeout = 0.2
            return await _real_wait_for(coro, timeout=timeout)

        with patch("asyncio.wait_for", side_effect=_clamped_wait_for):
            a_result = await client._send_request(
                "tools/call", {"name": "A", "arguments": {"timeout": 0.2}})
        assert a_result["error"] is True

        proc.stdout.push(_resp(deferred["a_id"], "LATE-A"))  # late arrival
        await asyncio.sleep(0.05)

        b_result = await client._send_request("tools/call", {"name": "B", "arguments": {}})
        assert b_result["content"][0]["text"] == "R2"
        assert deferred["a_id"] in client._response_buffer
        assert client._response_buffer[deferred["a_id"]]["result"]["content"][0]["text"] == "LATE-A"

    @pytest.mark.asyncio
    async def test_eof_fails_pending_request_cleanly(self):
        """EOF on stdout fails the awaiting request with a clear error instead
        of hanging it forever."""
        client, proc, holder = _make_client()
        holder["handler"] = lambda req: proc.stdout.push(b"")  # EOF
        result = await client._send_request("tools/call", {"name": "x", "arguments": {}})
        assert result["error"] is True
        assert "EOF" in result["message"] or "No response" in result["message"]

    @pytest.mark.asyncio
    async def test_response_buffer_is_bounded(self):
        """Unmatched responses cannot grow the buffer without bound."""
        from app.mcp.client import _MAX_RESPONSE_BUFFER
        client, proc, holder = _make_client()
        holder["handler"] = lambda req: None
        client._ensure_reader()
        for i in range(9000, 9000 + 400):
            proc.stdout.push(_resp(i, f"orphan{i}"))
        await asyncio.sleep(0.2)
        assert len(client._response_buffer) <= _MAX_RESPONSE_BUFFER


# ---------------------------------------------------------------------------
# Preserved retry / error semantics (regression guard against the rewrite)
# ---------------------------------------------------------------------------

class TestPreservedSemantics:
    @pytest.mark.asyncio
    async def test_blocked_error_not_retried(self):
        client, proc, holder = _make_client()
        counter = {"n": 0}

        def handler(req):
            counter["n"] += 1
            proc.stdout.push(_err(req["id"], -32602, "🚫 BLOCKED: '{' is not allowed"))

        holder["handler"] = handler
        result = await client._send_request(
            "tools/call", {"name": "run_shell_command", "arguments": {"command": "{"}})
        assert result["error"] is True
        assert result.get("policy_block") is True
        assert counter["n"] == 1, f"BLOCKED must not retry; got {counter['n']}"

    @pytest.mark.asyncio
    async def test_transient_error_retried_then_succeeds(self):
        client, proc, holder = _make_client()
        counter = {"n": 0}

        def handler(req):
            counter["n"] += 1
            rid = req["id"]
            if counter["n"] <= 2:
                proc.stdout.push(_err(rid, -32603,
                                      "ExtractArticle.js failed with non-zero exit status"))
            else:
                proc.stdout.push(_resp(rid, "success"))

        holder["handler"] = handler
        import app.mcp.client as client_mod
        with patch.object(client_mod.asyncio, "sleep", new_callable=AsyncMock):
            result = await client._send_request(
                "tools/call", {"name": "fetch", "arguments": {"url": "https://example.com"}})
        assert counter["n"] >= 3
        assert result["content"][0]["text"] == "success"

    @pytest.mark.asyncio
    async def test_timeout_message_reflects_extended_duration(self):
        """tools/call timeout arg extends the wait window; the timeout message
        reflects the extended COMPUTED duration (max(default, arg+10)), not a
        hardcoded default.  A timeout=120 arg computes to 130s; we clamp the
        ACTUAL future-await to a fraction of a second (the message reports
        the computed value, which is independent of the real wait), so the
        test exercises genuine extension without a 130s wall-clock wait."""
        client, proc, holder = _make_client()
        holder["handler"] = lambda req: None  # never answer
        _real_wait_for = asyncio.wait_for

        async def _clamped_wait_for(coro, timeout=None):
            if timeout is not None and timeout > 1:
                timeout = 0.2
            return await _real_wait_for(coro, timeout=timeout)

        with patch("asyncio.wait_for", side_effect=_clamped_wait_for):
            result = await client._send_request(
                "tools/call", {"name": "run_shell_command",
                               "arguments": {"command": "sleep 200", "timeout": 120}})
        assert result["error"] is True
        # Computed duration is max(30, 120+10) = 130 — genuinely extended.
        assert "130 seconds" in result["message"], \
            f"Expected '130 seconds' (extended), got: {result['message']}"
