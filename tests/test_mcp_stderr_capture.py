"""
Tests for MCP server stderr capture during a failed startup.

Two readers previously consumed process.stderr concurrently:

  - ``_capture_logs()``, a background task started right after spawn, looping
    on ``stderr.readline()``
  - the init-failure path in ``connect()``, calling ``stderr.read()``

Whichever won got the bytes; the other got nothing. A server that printed a
real error could therefore be reported as "No output from server process" while
its error text sat in the other reader's buffer — sending users to look for a
logging fault instead of reading the error in front of them.

The fix cancels the background reader before draining the stream, making the
failure path the sole reader, and stops claiming silence when lines were
already captured.
"""

import asyncio

import pytest

from app.mcp.client import MCPClient


class _FakeStream:
    """Minimal asyncio.StreamReader stand-in over a fixed byte list.

    readline() and read() draw from the same buffer, so a test that lets two
    readers run concurrently reproduces the real split-output behaviour rather
    than hiding it.
    """

    def __init__(self, lines):
        self._buf = bytearray(b"".join(lines))
        self.readline_calls = 0
        self.read_calls = 0

    async def readline(self):
        self.readline_calls += 1
        if not self._buf:
            return b""
        idx = self._buf.find(b"\n")
        if idx == -1:
            out, self._buf = bytes(self._buf), bytearray()
            return out
        out = bytes(self._buf[: idx + 1])
        del self._buf[: idx + 1]
        return out

    async def read(self, n=-1):
        self.read_calls += 1
        out = bytes(self._buf)
        self._buf = bytearray()
        return out


class _FakeProcess:
    def __init__(self, stderr_lines=(), stdout_lines=(), returncode=1):
        self.stderr = _FakeStream(list(stderr_lines))
        self.stdout = _FakeStream(list(stdout_lines))
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


class TestCaptureLogsReader:
    """The background reader on its own must behave correctly."""

    @pytest.mark.asyncio
    async def test_captures_stderr_lines(self):
        client = MCPClient({"name": "s", "command": ["echo"]})
        client.process = _FakeProcess(
            stderr_lines=[b"boom: missing module\n", b"stack line 2\n"]
        )
        await client._capture_logs()

        joined = "\n".join(client.logs)
        assert "boom: missing module" in joined
        assert "stack line 2" in joined

    @pytest.mark.asyncio
    async def test_log_buffer_is_bounded(self):
        """A chatty server must not grow logs without limit."""
        client = MCPClient({"name": "s", "command": ["echo"]})
        client.process = _FakeProcess(
            stderr_lines=[f"line {i}\n".encode() for i in range(250)]
        )
        await client._capture_logs()

        assert len(client.logs) <= 100
        # The most recent lines are the useful ones; the cap must drop the
        # oldest, not the newest.
        assert any("line 249" in entry for entry in client.logs)

    @pytest.mark.asyncio
    async def test_cancellation_is_clean(self):
        """disconnect() cancels this task; that must not append a spurious
        error to the log the user reads."""
        client = MCPClient({"name": "s", "command": ["echo"]})

        class _Blocking(_FakeStream):
            async def readline(self):
                await asyncio.sleep(3600)

        proc = _FakeProcess()
        proc.stderr = _Blocking([])
        client.process = proc

        task = asyncio.create_task(client._capture_logs())
        await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert not any("Failed to capture logs" in entry for entry in client.logs), (
            "cancellation during shutdown must not look like a capture failure"
        )


class TestNoConcurrentReaders:
    """The race itself: two readers on one stream split the output."""

    @pytest.mark.asyncio
    async def test_background_reader_and_read_split_output(self):
        """Documents the underlying hazard: if both readers run, neither sees
        the whole stream. This is why the fix must cancel before draining."""
        stream = _FakeStream([b"first\n", b"second\n", b"third\n"])

        line = await stream.readline()
        remainder = await stream.read()

        assert line == b"first\n"
        assert b"first" not in remainder, (
            "a line consumed by readline() is gone from the stream — a "
            "concurrent read() cannot recover it"
        )
        assert b"second" in remainder

    @pytest.mark.asyncio
    async def test_cancelling_reader_leaves_remaining_bytes_readable(self):
        """After cancelling the background reader, the failure path can still
        drain whatever the server had not yet emitted line-by-line."""
        client = MCPClient({"name": "s", "command": ["echo"]})
        proc = _FakeProcess(stderr_lines=[b"early\n", b"late\n"])
        client.process = proc

        # Simulate the background reader having consumed one line.
        first = await proc.stderr.readline()
        client.logs.append(f"STDERR: {first.decode().strip()}")

        rest = (await proc.stderr.read()).decode()

        assert "early" in "\n".join(client.logs)
        assert "late" in rest, "remaining bytes must still be drainable"


class TestSilenceIsNotMisreported:
    """"No output" must only be claimed when there genuinely was none."""

    def test_already_captured_lines_suppress_no_output_claim(self):
        client = MCPClient({"name": "s", "command": ["echo"]})
        client.logs.append("STDERR: ENOENT: no such file")

        already_captured = any(
            line.startswith(("STDOUT:", "STDERR:")) for line in client.logs
        )
        assert already_captured is True, (
            "with STDERR lines present, the code must not report 'no output'"
        )

    def test_genuinely_empty_logs_permit_no_output_claim(self):
        client = MCPClient({"name": "s", "command": ["echo"]})
        client.logs.append("ERROR: Failed to initialize MCP server connection")

        already_captured = any(
            line.startswith(("STDOUT:", "STDERR:")) for line in client.logs
        )
        assert already_captured is False, (
            "an ERROR: entry is Ziya's own message, not server output, so it "
            "must not suppress the 'no output' explanation"
        )
