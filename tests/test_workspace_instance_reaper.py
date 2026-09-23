"""
Idle workspace-scoped MCP subprocesses must actually get reaped.

Every conversation gets its own shell subprocess (instance key
``workspace::session_id``). ``cleanup_stale_workspace_instances`` existed with
a 5-minute idle timeout, but its only trigger was ``time.time() % 60 < 1``
after a tool call -- which fires only when a call happens to land in the first
second of a wall-clock minute, and never at all while the user is idle, which
is exactly when instances go stale. Observed 2026-09-22: ten shell_server.py
processes from the previous day still alive under three running servers.

These tests pin the reaper itself (idle -> disconnected and removed; busy or
fresh -> kept) and that creating an instance starts the background loop.
"""

import asyncio
import time

import pytest

from app.mcp.manager import MCPManager


class _FakeClient:
    def __init__(self, pending=None):
        self.disconnected = False
        self.is_connected = True
        self._pending = dict(pending or {})

    async def disconnect(self):
        self.disconnected = True
        self.is_connected = False

    def _is_process_healthy(self):
        return True


@pytest.fixture
def manager():
    m = MCPManager()
    m.workspace_scoped_clients = {}
    m._workspace_instance_last_used = {}
    m._workspace_instance_timeout = 300
    return m


def _register(m, key, client, last_used):
    m.workspace_scoped_clients.setdefault("shell", {})[key] = client
    m._workspace_instance_last_used.setdefault("shell", {})[key] = last_used


@pytest.mark.asyncio
async def test_idle_instance_is_disconnected_and_removed(manager):
    stale = _FakeClient()
    fresh = _FakeClient()
    now = time.time()
    _register(manager, "/p::old", stale, now - 301)
    _register(manager, "/p::new", fresh, now - 10)

    await manager.cleanup_stale_workspace_instances()

    assert stale.disconnected is True
    assert "/p::old" not in manager.workspace_scoped_clients["shell"]
    assert "/p::old" not in manager._workspace_instance_last_used["shell"]
    assert fresh.disconnected is False
    assert "/p::new" in manager.workspace_scoped_clients["shell"]


@pytest.mark.asyncio
async def test_idle_instance_with_request_in_flight_is_kept(manager):
    """last_used is stamped at hand-out, not at return: a long command that
    started just under the timeout must not be killed under the caller."""
    busy = _FakeClient(pending={7: object()})
    _register(manager, "/p::busy", busy, time.time() - 1000)

    await manager.cleanup_stale_workspace_instances()

    assert busy.disconnected is False
    assert "/p::busy" in manager.workspace_scoped_clients["shell"]


@pytest.mark.asyncio
async def test_cleanup_stamps_last_cleanup_time(manager):
    manager._last_workspace_cleanup = 0.0
    before = time.time()
    await manager.cleanup_stale_workspace_instances()
    assert manager._last_workspace_cleanup >= before


@pytest.mark.asyncio
async def test_reaper_loop_reaps_without_tool_calls(manager):
    """The seam that was missing: nothing calls the tool, the loop still runs."""
    stale = _FakeClient()
    _register(manager, "/p::old", stale, time.time() - 1000)
    manager._workspace_reaper_interval = 0.01

    manager._ensure_workspace_reaper()
    assert manager._workspace_reaper_task is not None

    for _ in range(100):
        if stale.disconnected:
            break
        await asyncio.sleep(0.01)
    assert stale.disconnected is True
    assert manager.workspace_scoped_clients["shell"] == {}

    manager._workspace_reaper_task.cancel()


@pytest.mark.asyncio
async def test_ensure_reaper_is_idempotent(manager):
    manager._workspace_reaper_interval = 60
    manager._ensure_workspace_reaper()
    first = manager._workspace_reaper_task
    manager._ensure_workspace_reaper()
    assert manager._workspace_reaper_task is first
    first.cancel()


@pytest.mark.asyncio
async def test_shutdown_cancels_reaper(manager):
    manager.clients = {}
    manager._workspace_reaper_interval = 60
    manager._ensure_workspace_reaper()
    task = manager._workspace_reaper_task
    await manager._shutdown_locked()
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()
    assert manager._workspace_reaper_task is None


@pytest.mark.asyncio
async def test_creating_instance_starts_reaper(manager, monkeypatch):
    """Seam: the reaper is started from the path that creates instances, so a
    server that has ever spawned one is guaranteed to reap it later."""
    manager.server_configs = {"shell": {"env": {}, "builtin": True, "workspace_scoped": True}}
    manager._workspace_reaper_interval = 60
    monkeypatch.setattr(manager, "_apply_escalation_overlay", lambda env, name: None)

    created = _FakeClient()

    async def _connect():
        return True
    created.connect = _connect

    import app.mcp.manager as mod
    monkeypatch.setattr(mod, "MCPClient", lambda cfg: created)

    client = await manager._get_or_create_workspace_client("shell", "/tmp/proj", session_id="s1")

    assert client is created
    assert manager._workspace_reaper_task is not None
    assert not manager._workspace_reaper_task.done()
    manager._workspace_reaper_task.cancel()
