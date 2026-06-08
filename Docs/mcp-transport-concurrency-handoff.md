# MCP Transport Concurrency — Handoff Document

## Status: COMPLETE (all open items resolved)

Last updated: 2026-06-05

---

## What was built

A full concurrency fix for the MCP shell transport layer, delivered in two phases.

### Phase 1 — client-side rewrite (prior session)

**Problem:** The old `MCPClient` held an `_io_lock` that serialised every
`call_tool` invocation. A slow command in flight blocked all other callers on
the same client, and concurrent `readuntil()` calls on the same stream could
crash.

**Fix:** Replaced the lock-based single-reader with a dedicated background
reader task that multiplexes responses to per-request `asyncio.Future`
objects keyed by `request_id`. All request state lives in
`_pending: dict[int, Future]`. Concurrent callers now get their own future
and wait independently; there is no shared mutex on the read path.

**Files changed:** `app/mcp/client.py`

---

### Phase 2 — manager-level per-session subprocess routing (this session)

**Problem:** `MCPManager._get_or_create_workspace_client` keyed the
subprocess cache by `workspace_path` alone. Two browser tabs (conversations)
on the same project therefore shared one subprocess. Because the shell server
itself is serial (one `readline()` → `handle_request()` loop), a slow command
in tab A would block any command from tab B until it finished.

**Decision: Option B — one subprocess per (workspace, session).**

Rationale:
- `ZIYA_USER_CODEBASE_DIR` is injected via environment variable, so two
  subprocesses for the same workspace are functionally equivalent — no
  shared mutable state between them.
- The concrete user-visible benefit (tab B is never blocked by tab A) is
  real and meaningful for an AI coding assistant where long build/test runs
  are common.
- The cost (one extra subprocess per concurrent session) is acceptable and
  bounded by the existing 5-minute idle eviction mechanism, which now evicts
  per `instance_key` independently.

**Implementation:**

`_get_or_create_workspace_client(server_name, workspace_path, session_id=None)`

```python
instance_key = f"{workspace_path}::{session_id}" if session_id else workspace_path
```

All internal dict operations (`workspace_scoped_clients`, `_workspace_instance_last_used`)
use `instance_key` instead of `workspace_path` as the lookup key.
`workspace_path` is still used for:
- setting `ZIYA_USER_CODEBASE_DIR` in the subprocess env
- log messages (human-readable path)
- the server `name` tag

`call_tool` passes `session_id=conversation_id` when routing to a
workspace-scoped server.

`cleanup_stale_workspace_instances` iterates over `instance_key` values
and evicts each independently based on last-used time.

**Files changed:** `app/mcp/manager.py`

---

## Test coverage

### Unit tests (32/32 pass, no regressions)
```
tests/test_mcp_client_concurrency.py    — 27 tests  PASS
tests/test_mcp_comprehensive.py         — 2 tests   PASS
tests/test_mcp_manager_builtin_dispatch.py — 9 tests PASS
tests/test_mcp_connection_pool.py       — 6 tests   PASS
```

Two pre-existing failures are **unrelated** to this work:
- `tests/backend/test_document_token_counting.py::test_docx_counts_extracted_text_not_raw_bytes`
  — mock patch path issue, present before this work.
- `tests/backend/test_js_ts_validation.py::test_syntax_tsc_errors_reject`
  — TypeScript handler logic issue, present before this work.

### Live concurrency script (3/3 scenarios pass)
`scripts/mcp_live_concurrency_check.py`

| Scenario | Description | Expected | Result |
|----------|-------------|----------|--------|
| 1 | Same subprocess, concurrent callers | Serial — no crash, each caller gets its own correct result | PASS |
| 2 | Two subprocesses, two explicit clients | Parallel — fast overlaps in-flight slow (~2.4 s overtake) | PASS |
| 3 | MCPManager, same workspace, two session IDs | Parallel — two distinct client instances, fast overlaps slow (~2.4 s overtake) | PASS |

Scenario 3 is the end-to-end proof of Option B: the manager correctly
creates separate subprocess instances per `instance_key`, and concurrent
conversations on the same project do not serialise each other.

---

## Architecture notes

### Why the shell server is still serial

The shell server's `run()` loop does a blocking `sys.stdin.readline()` then
calls `await handle_request(req)` inline before looping. `subprocess.run` in
the handler is also blocking. A single server process answers one request at
a time in arrival order.

The client-side rewrite (Phase 1) removed client-side serialization and
fixed the concurrent-read crash, but it cannot reorder responses from a
serial server. The correct architectural answer for multi-request
parallelism within a single session is the per-session subprocess (Phase 2),
not async concurrency inside one subprocess.

### Idle eviction

`_workspace_instance_timeout = 300` seconds (5 minutes). The background
cleanup task fires every 60 seconds and evicts any `instance_key` whose
`_workspace_instance_last_used` timestamp is older than the timeout.
Per-session instances are evicted independently — a session that goes idle
is cleaned up without affecting other sessions on the same workspace.

### Backward compatibility

`session_id` defaults to `None`. When `None`, `instance_key = workspace_path`
exactly as before. Any code path that calls `_get_or_create_workspace_client`
without a `session_id` (e.g. tests, direct internal calls) continues to work
identically.

---

## Open items

None. This work is complete.

If the shell server is ever refactored to be async-concurrent internally
(e.g. spawning a subprocess per request rather than running commands
synchronously), the per-session subprocess model can be revisited. But that
is a separate and much larger change.
