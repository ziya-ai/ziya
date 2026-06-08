#!/usr/bin/env python3
"""
Live concurrency check for the MCP shell transport — REAL subprocess, no mocks.

Spawns the actual app/mcp_servers/shell_server.py via the real MCPClient and
measures wall-clock timing of concurrent tool calls.

IMPORTANT — honest framing of what this can and cannot show:

  The shell server's run() loop is SERIAL: it does a blocking
  sys.stdin.readline(), then `await self.handle_request(request)` inline,
  then loops. Command execution itself is blocking (subprocess.run). So a
  single server subprocess processes requests one-at-a-time, in arrival
  order. The client-side single-reader rewrite removes CLIENT-side
  serialization (the old _io_lock) and prevents the readuntil() crash on
  concurrent callers, but it cannot make a serial SERVER answer out of order.

Therefore:

  Scenario 1 (same subprocess): EXPECT serial ordering — the fast call
  completes AFTER the in-flight slow call. The thing we assert here is the
  real, achievable property: concurrent callers on one client do not crash
  and each receives its own correct, distinct result.

  Scenario 2 (two subprocesses = two sessions): EXPECT genuine parallelism —
  the fast call on client B completes while the slow call on client A is
  still running. This is what "non-blocking reentrant multisession" actually
  means in this architecture (per-workspace subprocesses).

Run:  python3 scripts/mcp_live_concurrency_check.py
"""

import asyncio
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from app.mcp.client import MCPClient  # noqa: E402
from app.mcp.manager import MCPManager  # noqa: E402

SERVER = os.path.join(REPO, "app", "mcp_servers", "shell_server.py")

SLOW_CMD = "python3 -c \"import time; time.sleep(2.5); print('SLOW-DONE')\""
FAST_CMD = "python3 -c \"print('FAST-DONE')\""


def _cfg(name):
    return {
        "name": name,
        "command": ["python3"],
        "args": [SERVER],
        "env": {"ZIYA_USER_CODEBASE_DIR": REPO},
    }


async def _make_client(name):
    client = MCPClient(_cfg(name))
    ok = await client.connect()
    if not ok or not client.is_connected:
        raise RuntimeError(f"failed to connect client {name}; logs tail: {client.logs[-5:]}")
    return client


def _text(result):
    """Pull the text payload out of a tool result dict (best-effort)."""
    if not isinstance(result, dict):
        return repr(result)
    if result.get("error"):
        return f"ERROR: {result.get('message')}"
    content = result.get("content")
    if isinstance(content, list) and content:
        return content[0].get("text", repr(result))
    return repr(result)


async def scenario_same_subprocess():
    print("\n=== Scenario 1: SAME subprocess (expect SERIAL: fast completes AFTER slow) ===")
    client = await _make_client("shell-live-same")
    times = {}
    t0 = time.monotonic()

    async def slow():
        r = await client.call_tool("run_shell_command", {"command": SLOW_CMD, "timeout": 10})
        times["slow"] = time.monotonic() - t0
        return _text(r)

    async def fast():
        r = await client.call_tool("run_shell_command", {"command": FAST_CMD, "timeout": 10})
        times["fast"] = time.monotonic() - t0
        return _text(r)

    slow_task = asyncio.create_task(slow())
    await asyncio.sleep(0.15)  # ensure slow is written/in-flight first
    fast_task = asyncio.create_task(fast())

    slow_text, fast_text = await asyncio.gather(slow_task, fast_task)

    print(f"  slow result: {slow_text!r} at t={times['slow']:.3f}s")
    print(f"  fast result: {fast_text!r} at t={times['fast']:.3f}s")
    overtake = times["slow"] - times["fast"]
    if overtake > 0:
        print(f"  fast finished {overtake:.3f}s BEFORE slow (out-of-order / parallel)")
    else:
        print(f"  fast finished {-overtake:.3f}s AFTER slow (serial ordering)")

    # Honest, achievable assertions: no crash, each call got its OWN correct result.
    ok = ("SLOW-DONE" in slow_text) and ("FAST-DONE" in fast_text)
    print(f"  RESULT: each call returned its own correct result, no crash → {'PASS' if ok else 'FAIL'}")
    await client.disconnect()
    return ok, overtake


async def scenario_two_subprocesses():
    print("\n=== Scenario 2: TWO subprocesses / sessions (expect PARALLEL: fast overlaps slow) ===")
    client_a = await _make_client("shell-live-A")
    client_b = await _make_client("shell-live-B")
    times = {}
    t0 = time.monotonic()

    async def slow():
        r = await client_a.call_tool("run_shell_command", {"command": SLOW_CMD, "timeout": 10})
        times["slow"] = time.monotonic() - t0
        return _text(r)

    async def fast():
        r = await client_b.call_tool("run_shell_command", {"command": FAST_CMD, "timeout": 10})
        times["fast"] = time.monotonic() - t0
        return _text(r)

    slow_task = asyncio.create_task(slow())
    await asyncio.sleep(0.15)
    fast_task = asyncio.create_task(fast())

    slow_text, fast_text = await asyncio.gather(slow_task, fast_task)

    print(f"  slow result (session A): {slow_text!r} at t={times['slow']:.3f}s")
    print(f"  fast result (session B): {fast_text!r} at t={times['fast']:.3f}s")
    overtake = times["slow"] - times["fast"]
    # True multisession parallelism: fast (B) should complete well before slow (A).
    parallel = overtake > 1.0 and ("FAST-DONE" in fast_text) and ("SLOW-DONE" in slow_text)
    print(f"  fast finished {overtake:.3f}s before slow")
    print(f"  RESULT: cross-session parallelism (fast overlaps in-flight slow) → {'PASS' if parallel else 'FAIL'}")
    await client_a.disconnect()
    await client_b.disconnect()
    return parallel, overtake


async def scenario_same_workspace_two_sessions():
    """
    Scenario 3: Two sessions share the same workspace path but carry different
    session IDs. After the Option-B change the manager creates separate subprocess
    instances (one per instance_key) so the two sessions run in parallel instead
    of serialising behind a single subprocess.

    Expected: fast (sess-B) completes while slow (sess-A) is still running.
    """
    print("\n=== Scenario 3: SAME workspace, TWO session IDs via MCPManager (expect PARALLEL) ===")

    # Build a minimal manager with only the shell-server config so we
    # do not need a full app initialisation.
    manager = MCPManager()
    manager.server_configs = {
        "shell": {
            "command": [sys.executable],
            "args": [SERVER],
            "env": {"ZIYA_USER_CODEBASE_DIR": REPO},
            "workspace_scoped": True,
            "enabled": True,
        }
    }

    client_a = await manager._get_or_create_workspace_client(
        "shell", REPO, session_id="sess-A"
    )
    client_b = await manager._get_or_create_workspace_client(
        "shell", REPO, session_id="sess-B"
    )

    if client_a is None or client_b is None:
        print("  FAIL: manager returned None for one or both clients")
        return False, 0

    if client_a is client_b:
        print("  FAIL: same workspace + different session IDs returned the SAME client (Option A behaviour)")
        return False, 0

    print("  Two distinct MCPClient instances confirmed for the same workspace (Option B \u2713)")

    times = {}
    t0 = time.monotonic()

    async def run_slow():
        r = await client_a.call_tool("run_shell_command", {"command": SLOW_CMD, "timeout": 10})
        times["slow"] = time.monotonic() - t0
        return _text(r)

    async def run_fast():
        r = await client_b.call_tool("run_shell_command", {"command": FAST_CMD, "timeout": 10})
        times["fast"] = time.monotonic() - t0
        return _text(r)

    slow_task = asyncio.create_task(run_slow())
    await asyncio.sleep(0.15)
    fast_task = asyncio.create_task(run_fast())

    slow_text, fast_text = await asyncio.gather(slow_task, fast_task)

    print(f"  slow result (sess-A): {slow_text!r} at t={times['slow']:.3f}s")
    print(f"  fast result (sess-B): {fast_text!r} at t={times['fast']:.3f}s")
    overtake = times["slow"] - times["fast"]
    parallel = overtake > 1.0 and ("FAST-DONE" in fast_text) and ("SLOW-DONE" in slow_text)
    print(f"  fast finished {overtake:.3f}s before slow")
    print(f"  RESULT: same-workspace cross-session parallelism via manager \u2192 {'PASS' if parallel else 'FAIL'}")
    await client_a.disconnect()
    await client_b.disconnect()
    return parallel, overtake


async def main():
    print("Live MCP shell-transport concurrency check (real subprocess, no mocks)")
    print(f"server: {SERVER}")
    s1_ok = s2_ok = s3_ok = False
    try:
        s1_ok, s1_overtake = await scenario_same_subprocess()
    except Exception as e:
        print(f"  Scenario 1 raised: {e!r}")
    try:
        s2_ok, s2_overtake = await scenario_two_subprocesses()
    except Exception as e:
        print(f"  Scenario 2 raised: {e!r}", file=sys.stderr)
    try:
        s3_ok, s3_overtake = await scenario_same_workspace_two_sessions()
    except Exception as e:
        print(f"  Scenario 3 raised: {e!r}", file=sys.stderr)

    print("\n=== SUMMARY ===")
    print(f"  Scenario 1 (same subprocess, no-crash + own-results): {'PASS' if s1_ok else 'FAIL'}")
    print(f"  Scenario 2 (two sessions, true parallelism):          {'PASS' if s2_ok else 'FAIL'}")
    print(f"  Scenario 3 (manager same-workspace two session IDs):  {'PASS' if s3_ok else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())
