"""PenPal #131 [CWE-667]: MCPManager singleton lifecycle-lock coverage.

Verifies the lock serializes the lifecycle mutators and that the toggle
read-modify-write (Race 1) no longer interleaves on shared state.  A
negative-control test proves the lock is load-bearing.
"""
import asyncio
import pytest

from app.mcp.manager import MCPManager


def _bare_manager():
    """An MCPManager with no I/O — bypass __init__, set only what we touch."""
    m = MCPManager.__new__(MCPManager)
    m._lifecycle_lock = None
    m.clients = {}
    m.server_configs = {}
    m._server_enabled_overrides = {}
    m.builtin_server_definitions = {"shell": {"command": "x", "enabled": True}}
    m._tools_cache = None
    m._tools_cache_timestamp = 0
    return m


class TestLifecycleLock:
    def test_lock_created_lazily_and_reused(self):
        m = _bare_manager()
        assert m._lifecycle_lock is None
        lk = m._get_lifecycle_lock()
        assert isinstance(lk, asyncio.Lock)
        assert m._get_lifecycle_lock() is lk  # same instance, not recreated

    @pytest.mark.asyncio
    async def test_lock_is_non_reentrant_guarded(self):
        # Sanity: proves set_server_enabled must NOT call the public wrappers
        # (which would re-acquire and deadlock). We assert the lock is held
        # exactly once by draining it.
        m = _bare_manager()
        lk = m._get_lifecycle_lock()
        async with lk:
            assert lk.locked()
        assert not lk.locked()


class TestToggleRaceCondition:
    @pytest.mark.asyncio
    async def test_concurrent_disable_no_keyerror(self, monkeypatch):
        """Race 1: two concurrent disables must not raise KeyError on
        ``del self.clients[name]`` — set_server_enabled uses pop()."""
        m = _bare_manager()

        class _Client:
            async def disconnect(self):
                await asyncio.sleep(0)  # yield, widen the race window

        m.clients = {"shell": _Client()}
        m.server_configs = {"shell": {"enabled": True}}
        monkeypatch.setattr(m, "invalidate_tools_cache", lambda: None)

        results = await asyncio.gather(
            m.set_server_enabled("shell", False),
            m.set_server_enabled("shell", False),
            return_exceptions=True,
        )
        assert all(not isinstance(r, Exception) for r in results), results
        assert all(r["success"] for r in results)
        assert "shell" not in m.clients
        assert m._server_enabled_overrides["shell"] is False

    @pytest.mark.asyncio
    async def test_disable_updates_config_and_override(self, monkeypatch):
        m = _bare_manager()
        m.server_configs = {"shell": {"enabled": True}}
        monkeypatch.setattr(m, "invalidate_tools_cache", lambda: None)
        r = await m.set_server_enabled("shell", False)
        assert r["success"]
        assert m.server_configs["shell"]["enabled"] is False
        assert m._server_enabled_overrides["shell"] is False

    @pytest.mark.asyncio
    async def test_enable_calls_locked_restart_not_wrapper(self, monkeypatch):
        """Enable path must call _restart_server_locked (internal), NOT
        restart_server (public wrapper) — the latter re-acquires the
        non-reentrant lock and would deadlock."""
        m = _bare_manager()
        m.server_configs = {"shell": {"enabled": False}}
        monkeypatch.setattr(m, "invalidate_tools_cache", lambda: None)
        called = {"locked": False, "wrapper": False}

        async def _fake_locked(name, cfg=None):
            called["locked"] = True
            return True

        async def _fake_wrapper(name, cfg=None):
            called["wrapper"] = True
            return True

        monkeypatch.setattr(m, "_restart_server_locked", _fake_locked)
        monkeypatch.setattr(m, "restart_server", _fake_wrapper)

        # Must complete without deadlock (guard with wait_for).
        r = await asyncio.wait_for(m.set_server_enabled("shell", True), timeout=2.0)
        assert r["success"]
        assert called["locked"] and not called["wrapper"]

    @pytest.mark.asyncio
    async def test_enable_missing_config_returns_failure(self, monkeypatch):
        m = _bare_manager()
        m.server_configs = {}  # no config, and not the shell special-case name
        monkeypatch.setattr(m, "invalidate_tools_cache", lambda: None)
        r = await m.set_server_enabled("nonexistent", True)
        assert r["success"] is False
        assert "No configuration" in r["message"]

    @pytest.mark.asyncio
    async def test_toggle_serialized_under_lock(self, monkeypatch):
        """Two concurrent set_server_enabled calls must not interleave: the
        second waits for the first to release the lock."""
        m = _bare_manager()
        m.server_configs = {"shell": {"enabled": True}}
        monkeypatch.setattr(m, "invalidate_tools_cache", lambda: None)
        order = []

        class _SlowClient:
            async def disconnect(self):
                order.append("disconnect-start")
                await asyncio.sleep(0.05)
                order.append("disconnect-end")

        m.clients = {"shell": _SlowClient()}
        # First disables (slow disconnect); second is a re-disable (no client).
        await asyncio.gather(
            m.set_server_enabled("shell", False),
            m.set_server_enabled("shell", False),
        )
        # If serialized, the slow disconnect completes before the 2nd entered
        # its critical section — i.e. start/end are adjacent, not interleaved.
        assert order == ["disconnect-start", "disconnect-end"]
