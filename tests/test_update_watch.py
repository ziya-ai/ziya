"""
Header update chip, server side (app/utils/update_watch.py).

Reported 2026-11-11: an upgrade left a running server on the old version
with nothing telling the user; the first sign was a failed response.  The
watcher must offer the new version and restart into it, and must never
restart while a chat turn, Task Card run or delegate plan is running in
this process -- that would lose the work the restart exists to protect.
"""
import asyncio
import dataclasses
import inspect
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.plugins.interfaces import UpdateInfo, UpdateProvider
from app.utils import update_watch as uw


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    import app.plugins as plugins
    from app.agents import chat_turn_relay, delegate_manager, live_runs
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(plugins, "_update_providers", [])
    monkeypatch.setattr(chat_turn_relay, "_turns", {})
    monkeypatch.setattr(live_runs, "_LIVE", {})
    monkeypatch.setattr(delegate_manager, "_instances", {})
    uw._reset_for_tests()
    yield
    uw._reset_for_tests()


def _info(**kw):
    base = dict(installed_version="2.0", running_version="1.0",
                exec_path="/tools/ziya/2.0/bin/ziya",
                argv=["/tools/ziya/2.0/bin/ziya", "--port", "7000"],
                env={"TOOLBOX_TOOL_VERSION": "2.0"})
    base.update(kw)
    return UpdateInfo(**base)


class _Provider(UpdateProvider):
    provider_id = "test"

    def __init__(self, info=None, prepare_ok=True):
        self.info, self.prepare_ok, self.prepared = info, prepare_ok, []

    def check(self):
        return self.info

    def prepare(self, info):
        self.prepared.append(info.installed_version)
        if self.prepare_ok:
            self.info = dataclasses.replace(info, ready=True)
        return self.prepare_ok


class _Server:
    def __init__(self):
        self.should_exit = False
        self.config = SimpleNamespace(timeout_graceful_shutdown=None)


def _setup(provider):
    from app.plugins import register_update_provider
    register_update_provider(provider)
    server = _Server()
    uw.set_server(server)
    return server


def _tick():
    asyncio.run(uw.tick())


def test_requested_restart_waits_for_a_running_chat_turn():
    from app.agents import chat_turn_relay as relay
    server = _setup(_Provider(_info()))
    turn = relay._Turn("conv-1", None)
    relay._turns["conv-1"] = turn
    uw.request_restart()
    _tick()
    assert server.should_exit is False
    assert uw.status()["busy"] == [{"kind": "chat", "id": "conv-1"}]
    turn.done = True
    _tick()
    assert server.should_exit is True                       # positive control
    assert server.config.timeout_graceful_shutdown == uw.SHUTDOWN_GRACE_SECONDS
    assert uw.status()["restarting"] is True


def test_task_runs_and_delegate_plans_count_as_busy(monkeypatch):
    from app.agents import delegate_manager, live_runs

    async def busy_while_a_run_is_live():
        task = asyncio.create_task(asyncio.sleep(30))
        live_runs.register("run-1", task)
        try:
            return uw.busy_items()
        finally:
            task.cancel()
            live_runs.unregister("run-1")

    assert asyncio.run(busy_while_a_run_is_live()) == [{"kind": "task_run", "id": "run-1"}]
    mgr = SimpleNamespace(_running={"plan-1": {"d1"}, "plan-2": set()})
    monkeypatch.setattr(delegate_manager, "_instances", {"p": mgr})
    assert uw.busy_items() == [{"kind": "delegates", "id": "plan-1"}]


def test_restart_execs_the_new_version_once_the_server_has_stopped(monkeypatch):
    calls = []
    monkeypatch.setattr(uw.os, "execve", lambda path, argv, env: calls.append((path, argv, env)))
    monkeypatch.setenv("ZIYA_AUTH_CHECKED", "true")
    monkeypatch.setenv("ZIYA_PORT", "7000")
    _setup(_Provider(_info()))
    uw.exec_if_pending()
    assert calls == []                                      # nothing begun yet
    uw.request_restart()
    _tick()
    uw.exec_if_pending()
    [(path, argv, env)] = calls
    assert path == "/tools/ziya/2.0/bin/ziya" and argv[1:] == ["--port", "7000"]
    assert env["TOOLBOX_TOOL_VERSION"] == "2.0" and env["ZIYA_PORT"] == "7000"
    assert "ZIYA_AUTH_CHECKED" not in env


def test_auto_restart_needs_the_pref_and_a_quiet_period(monkeypatch):
    server = _setup(_Provider(_info()))
    _tick()
    assert server.should_exit is False                      # not asked, auto off
    uw.set_prefs(True)
    monkeypatch.setattr(uw, "_last_busy", time.monotonic())
    _tick()
    assert server.should_exit is False                      # something just finished
    monkeypatch.setattr(uw, "_last_busy", time.monotonic() - uw.AUTO_QUIET_SECONDS - 1)
    _tick()
    assert server.should_exit is True


def test_unready_update_is_prepared_then_restarted():
    provider = _Provider(_info(ready=False))
    server = _setup(provider)
    uw.request_restart()
    _tick()
    assert provider.prepared == ["2.0"] and server.should_exit is True


def test_failed_prepare_is_reported_and_not_retried():
    provider = _Provider(_info(ready=False), prepare_ok=False)
    server = _setup(provider)
    uw.request_restart()
    _tick()
    _tick()
    assert provider.prepared == ["2.0"] and server.should_exit is False
    assert uw.status()["prepare_failed"] is True


def test_auto_restart_pref_survives_a_restart():
    uw.set_prefs(True)
    uw._reset_for_tests()                                   # what a new process sees
    assert uw.get_prefs() == {"auto_restart": True}


def test_same_environment_fallback_sees_files_replaced_in_place(monkeypatch):
    import importlib.metadata as md
    from app.utils import install_lock
    versions = {"ziya": "1.0"}

    def fake_version(dist):
        if dist not in versions:
            raise md.PackageNotFoundError(dist)
        return versions[dist]

    monkeypatch.setattr(md, "version", fake_version)
    _setup(_Provider(None))
    uw._providers()                                         # startup snapshot
    assert uw.find_update() == (None, None)
    versions["ziya"] = "1.1"
    _, info = uw.find_update()
    assert (info.running_version, info.installed_version) == ("1.0", "1.1")
    assert info.exec_path == sys.executable and info.argv == install_lock.reexec_argv()


def test_a_broken_provider_does_not_hide_the_next_one():
    from app.plugins import register_update_provider

    class Broken(_Provider):
        priority = 50

        def check(self):
            raise RuntimeError("boom")

    register_update_provider(Broken())
    good = _Provider(_info())
    register_update_provider(good)
    provider, info = uw.find_update()
    assert provider is good and info.installed_version == "2.0"


def test_routes_drive_the_watcher():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routes.update_routes import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    s = client.get("/api/update/status").json()
    assert s["boot_id"] == uw.BOOT_ID and s["update"] is None and s["can_restart"] is False
    assert client.post("/api/update/restart", json={}).json()["restart_requested"] is True
    assert client.post("/api/update/restart", json={"cancel": True}).json()["restart_requested"] is False
    assert client.put("/api/update/prefs", json={"auto_restart": True}).json()["auto_restart"] is True


def test_start_server_execs_only_after_uvicorn_returns():
    import app.main as m
    src = inspect.getsource(m.start_server)
    assert "uvicorn.run(" not in src
    assert (src.index("update_watch.set_server(server)") < src.index("server.run()")
            < src.index("update_watch.exec_if_pending()"))


def test_ctrl_c_exits_quietly_and_never_restarts():
    # uvicorn re-raises SIGINT after its graceful shutdown.  start_server must
    # swallow it (no traceback) and return before exec_if_pending, so a queued
    # restart cannot bring back a server the user just stopped.
    import ast
    import textwrap
    import app.main as m
    tree = ast.parse(textwrap.dedent(inspect.getsource(m.start_server)))
    guarded = [t for t in ast.walk(tree) if isinstance(t, ast.Try)
               and any(ast.unparse(s) == "server.run()" for s in t.body)]
    assert guarded, "server.run() is not inside its own try block"
    handlers = [h for h in guarded[0].handlers if ast.unparse(h.type) == "KeyboardInterrupt"]
    assert handlers, "KeyboardInterrupt from server.run() is not handled"
    assert any(isinstance(s, ast.Return) for s in handlers[0].body)
    assert not any("exec_if_pending" in ast.unparse(s) for s in handlers[0].body)


def test_lifespan_starts_the_watcher_only_for_start_server():
    src = (Path(uw.__file__).parents[1] / "server.py").read_text()
    assert "_update_watch.server_attached()" in src and "_update_watch.run_forever()" in src
