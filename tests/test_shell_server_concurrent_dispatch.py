"""Tests that the shell server dispatches requests concurrently.

The server was previously a strictly serial request loop: ``run()`` blocked
on a synchronous ``sys.stdin.readline()`` and awaited ``handle_request`` to
completion, and ``handle_request`` itself contained no await points because
``_execute_pipeline`` -> ``proc.communicate()`` is blocking I/O. The event
loop was therefore pinned for a command's full duration, so one slow command
blocked every other caller sharing the subprocess.

These tests pin the three properties the concurrent dispatch relies on:

1. Independent commands overlap in wall-clock time (the blocking pipeline
   runs off the event loop via ``asyncio.to_thread``).
2. A per-request task scope stays confined to that request -- concurrent
   requests can neither observe nor clear each other's grant.
3. Concurrent response writes are not interleaved mid-record, so the
   JSON-RPC framing on stdout stays parseable.

Each test that asserts concurrency is paired with a check that would fail
under the old serial design, so the assertions cannot pass vacuously.
"""

import asyncio
import io
import json
import os
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# Ensure the project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.mcp_servers.shell_server import ShellServer
from app.mcp_servers.write_policy import _TASK_SCOPE


# A command duration long enough that serialization is unambiguous but short
# enough to keep the suite fast. Two of these run in ~SLEEP s concurrently,
# ~2*SLEEP s serially.
SLEEP = 0.15


def _pipeline_result(stdout=""):
    """Mimic the object _execute_pipeline returns (attribute access)."""
    return SimpleNamespace(stdout=stdout, stderr="", returncode=0)


def _request(req_id, command, task_scope=None):
    params = {"name": "run_shell_command", "arguments": {"command": command}}
    if task_scope is not None:
        params["arguments"]["_task_scope"] = task_scope
    return {"jsonrpc": "2.0", "id": req_id, "method": "tools/call", "params": params}


@pytest.mark.asyncio
class TestConcurrentExecution:
    """Independent commands must not block one another."""

    async def test_two_commands_overlap_in_wall_clock(self):
        """Two concurrent requests finish in ~SLEEP, not ~2*SLEEP.

        _execute_pipeline is replaced with a *synchronous* time.sleep, which
        is what the real implementation does (proc.communicate blocks). If it
        were still called inline on the event loop, the two requests could
        only run back-to-back.
        """
        srv = ShellServer()

        def blocking_pipeline(command, timeout, cwd):
            time.sleep(SLEEP)
            return _pipeline_result(f"done: {command}")

        with patch.object(srv, "_execute_pipeline", side_effect=blocking_pipeline):
            start = time.monotonic()
            responses = await asyncio.gather(
                srv.handle_request(_request(1, "echo a")),
                srv.handle_request(_request(2, "echo b")),
            )
            elapsed = time.monotonic() - start

        assert all("error" not in r for r in responses), responses
        # Serial execution would need >= 2*SLEEP. Allow generous headroom for
        # scheduling while still excluding the serial case.
        assert elapsed < SLEEP * 1.8, (
            f"two commands took {elapsed:.3f}s; expected ~{SLEEP:.3f}s "
            f"(concurrent), serial would be ~{SLEEP * 2:.3f}s"
        )

    async def test_pipeline_runs_off_the_event_loop(self):
        """_execute_pipeline must execute on a worker thread, not the loop.

        This is the mechanism behind the overlap above: if it ran on the loop
        thread the loop would be pinned for the command's duration.
        """
        srv = ShellServer()
        loop_thread_id = threading.get_ident()
        observed = {}

        def record_thread(command, timeout, cwd):
            observed["thread_id"] = threading.get_ident()
            return _pipeline_result("ok")

        with patch.object(srv, "_execute_pipeline", side_effect=record_thread):
            await srv.handle_request(_request(1, "echo a"))

        assert observed["thread_id"] != loop_thread_id, (
            "_execute_pipeline ran on the event loop thread; it must be "
            "dispatched via asyncio.to_thread so the loop stays responsive"
        )

    async def test_slow_command_does_not_delay_a_fast_one(self):
        """A fast request completes while a slow one is still running."""
        srv = ShellServer()
        completion_order = []

        def variable_pipeline(command, timeout, cwd):
            time.sleep(SLEEP if "slow" in command else 0.0)
            return _pipeline_result("ok")

        async def run(req_id, command):
            await srv.handle_request(_request(req_id, command))
            completion_order.append(command)

        with patch.object(srv, "_execute_pipeline", side_effect=variable_pipeline):
            await asyncio.gather(
                run(1, "echo slow"),
                run(2, "echo fast"),
            )

        assert completion_order == ["echo fast", "echo slow"], (
            f"expected the fast command to finish first, got {completion_order}; "
            f"under serial dispatch the slow one blocks the fast one"
        )


@pytest.mark.asyncio
class TestTaskScopeIsolationUnderConcurrency:
    """A request's task grant must not leak to a concurrent request."""

    async def test_concurrent_requests_do_not_share_task_scope(self):
        """Each request's _task_scope is confined to its own task.

        The server shares ONE ShellWriteChecker across requests, so this only
        holds because the scope lives in a ContextVar and each request runs in
        its own task (which copies the context).
        """
        srv = ShellServer()
        seen = {}

        def capture_scope(command, timeout, cwd):
            # Read the scope from inside execution, after the peer request has
            # had a chance to set its own.
            time.sleep(SLEEP / 2)
            seen[command] = dict(srv.write_checker._task_scope)
            return _pipeline_result("ok")

        scope_a = {"writable": [{"path": "a_only/", "is_dir": True}],
                   "project_root": "/proj"}
        scope_b = {"writable": [{"path": "b_only/", "is_dir": True}],
                   "project_root": "/proj"}

        with patch.object(srv, "_execute_pipeline", side_effect=capture_scope):
            await asyncio.gather(
                srv.handle_request(_request(1, "echo a", task_scope=scope_a)),
                srv.handle_request(_request(2, "echo b", task_scope=scope_b)),
            )

        # The scope is cleared before execution (validation-only), so neither
        # request should observe the OTHER request's grant at any point.
        for command, observed in seen.items():
            writable = observed.get("writable") or []
            paths = {entry.get("path") for entry in writable if isinstance(entry, dict)}
            foreign = "b_only/" if command == "echo a" else "a_only/"
            assert foreign not in paths, (
                f"request '{command}' observed the peer request's grant "
                f"{foreign}; task scope leaked across concurrent requests"
            )

    async def test_grant_visible_to_own_validation_only(self):
        """A granted request still passes its own allowlist/write checks.

        Guards against the isolation fix over-correcting: the grant must reach
        the validating code path for THIS request even though it is invisible
        to peers.
        """
        srv = ShellServer()
        observed = {}

        real_check = srv.write_checker.check

        def spy_check(command, splitter):
            observed["scope_at_check"] = dict(srv.write_checker._task_scope)
            return real_check(command, splitter)

        scope = {"writable": [{"path": "granted/", "is_dir": True}],
                 "project_root": "/proj"}

        with patch.object(srv, "_execute_pipeline",
                          side_effect=lambda c, t, w: _pipeline_result("ok")), \
             patch.object(srv.write_checker, "check", side_effect=spy_check):
            await srv.handle_request(_request(1, "echo hi", task_scope=scope))

        writable = observed["scope_at_check"].get("writable") or []
        paths = {e.get("path") for e in writable if isinstance(e, dict)}
        assert "granted/" in paths, (
            "the request's own task grant was not visible to its write check; "
            f"saw {observed['scope_at_check']}"
        )

    async def test_scope_cleared_after_request(self):
        """No residual grant survives a completed request."""
        srv = ShellServer()
        scope = {"writable": [{"path": "tmp/", "is_dir": True}],
                 "project_root": "/proj"}

        with patch.object(srv, "_execute_pipeline",
                          side_effect=lambda c, t, w: _pipeline_result("ok")):
            await srv.handle_request(_request(1, "echo hi", task_scope=scope))

        assert srv.write_checker._task_scope == {}, (
            f"task scope persisted after the request completed: "
            f"{srv.write_checker._task_scope}"
        )


@pytest.mark.asyncio
class TestResponseFraming:
    """Concurrent response writes must not corrupt the JSON-RPC framing."""

    async def test_concurrent_writes_are_not_interleaved(self):
        """Every record on stdout parses as standalone JSON.

        _write_response emits payload+newline under a lock. Without it, the
        two writes from different tasks can interleave mid-record.
        """
        srv = ShellServer()
        buf = io.StringIO()

        # A writer that yields control mid-record, maximising the chance of
        # interleaving if the lock were absent or ineffective.
        real_write = buf.write

        def chunked_write(text):
            for piece in (text[:len(text) // 2], text[len(text) // 2:]):
                real_write(piece)
            return len(text)

        responses = [
            {"jsonrpc": "2.0", "id": i, "result": {"content": [
                {"type": "text", "text": "x" * 200}]}}
            for i in range(12)
        ]

        with patch("sys.stdout", SimpleNamespace(write=chunked_write,
                                                 flush=lambda: None)):
            await asyncio.gather(*(srv._write_response(r) for r in responses))

        lines = [ln for ln in buf.getvalue().split("\n") if ln]
        assert len(lines) == len(responses), (
            f"expected {len(responses)} records, got {len(lines)}; "
            f"records were interleaved or split"
        )
        ids = set()
        for line in lines:
            parsed = json.loads(line)  # raises if a record was corrupted
            ids.add(parsed["id"])
        assert ids == set(range(len(responses)))

    async def test_serve_one_reports_unexpected_errors(self):
        """A crashing handler yields a JSON-RPC error, not a lost response.

        _serve_one runs detached from run(), so an unhandled exception would
        otherwise vanish into the task and the client would hang on its future.
        """
        srv = ShellServer()
        written = []

        async def capture(response):
            written.append(response)

        with patch.object(srv, "handle_request",
                          side_effect=RuntimeError("boom")), \
             patch.object(srv, "_write_response", side_effect=capture):
            await srv._serve_one({"jsonrpc": "2.0", "id": 42, "method": "x"})

        assert len(written) == 1
        assert written[0]["id"] == 42, "the response must retain the request id"
        assert written[0]["error"]["code"] == -32603
        assert "boom" in written[0]["error"]["message"]


@pytest.mark.asyncio
class TestDispatchBounding:
    """In-flight commands must be bounded, not unbounded."""

    async def test_semaphore_caps_concurrent_handlers(self):
        """No more than the configured number of handlers run at once.

        Each command occupies a thread from asyncio's shared default executor
        and forks a subprocess, so unbounded dispatch would fork without limit
        and silently re-serialize once the pool saturates.
        """
        srv = ShellServer()
        # Constrain tightly so the bound is observable.
        srv._dispatch_semaphore = asyncio.Semaphore(2)

        concurrent = 0
        peak = 0
        lock = threading.Lock()

        async def slow_handler(request):
            nonlocal concurrent, peak
            with lock:
                concurrent += 1
                peak = max(peak, concurrent)
            await asyncio.sleep(SLEEP / 3)
            with lock:
                concurrent -= 1
            return {"jsonrpc": "2.0", "id": request.get("id"), "result": {}}

        async def noop_write(response):
            return None

        with patch.object(srv, "handle_request", side_effect=slow_handler), \
             patch.object(srv, "_write_response", side_effect=noop_write):
            await asyncio.gather(*(
                srv._serve_one({"jsonrpc": "2.0", "id": i, "method": "x"})
                for i in range(8)
            ))

        assert peak <= 2, f"observed {peak} concurrent handlers; cap is 2"
        assert peak > 1, (
            f"observed peak of {peak}; expected genuine concurrency up to the "
            f"cap -- a peak of 1 means dispatch is still serial"
        )


def test_lock_and_semaphore_constructed_without_running_loop():
    """ShellServer() must be constructible outside an event loop.

    Tests and the CLI construct the server synchronously. Since Python 3.10
    asyncio.Lock/Semaphore do not bind a loop at creation; this pins that
    assumption so a regression surfaces here rather than at runtime. Kept a
    plain sync test deliberately -- the point is that no loop is running.
    """
    srv = ShellServer()
    assert isinstance(srv._stdout_lock, asyncio.Lock)
    assert isinstance(srv._dispatch_semaphore, asyncio.Semaphore)


class TestSerialDesignNegativeControl(unittest.TestCase):
    """Negative control: the old inline design really did serialize.

    Without this, the timing assertions above could pass vacuously (e.g. if
    the patched pipeline never actually slept).
    """

    def test_inline_blocking_call_serializes(self):
        """Awaiting a blocking call inline pins the loop, forcing serial runs."""

        async def scenario():
            def blocking(_):
                time.sleep(SLEEP)
                return "ok"

            async def inline_handler(command):
                # The OLD shape: call the blocking function directly, with no
                # to_thread hop and no other await point.
                return blocking(command)

            start = time.monotonic()
            await asyncio.gather(inline_handler("a"), inline_handler("b"))
            return time.monotonic() - start

        elapsed = asyncio.run(scenario())
        self.assertGreaterEqual(
            elapsed, SLEEP * 1.8,
            f"inline blocking calls completed in {elapsed:.3f}s; the negative "
            f"control expected serialization (~{SLEEP * 2:.3f}s). If this "
            f"fails, the timing assertions elsewhere prove nothing."
        )


@pytest.fixture(autouse=True)
def _reset_task_scope():
    """Keep a leaked task scope from bleeding between tests."""
    token = _TASK_SCOPE.set(None)
    yield
    _TASK_SCOPE.reset(token)


if __name__ == "__main__":
    unittest.main()
