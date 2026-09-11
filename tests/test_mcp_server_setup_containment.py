"""
An unanticipated exception while preparing ONE MCP server must not cost the
user every other server.

Context. ``MCPManager._initialize_locked`` builds every server inside a single
``try/except Exception`` that returns False (manager.py). Any exception raised
while preparing entry N therefore abandons entries N+1..M as well, and the only
evidence is one terminal line::

    Error initializing MCP manager: 'NoneType' object is not iterable

That is how ``"env": null`` on one entry silently cost a user all of their MCP
tools (see test_mcp_config_none_normalization.py). Guarding ``env`` fixes that
instance; it does not fix the shape of the bug. The setup body touches
arbitrary user- and registry-supplied config at a dozen points -- bearer token
resolution, path expansion, escalation overlay, MCPClient construction -- and
the next unanticipated failure among them would abort the loop exactly the
same way.

The structural fix moves per-entry preparation behind a boundary, so a failure
is attributed to the entry that caused it:

  - initialization still succeeds, and healthy siblings still connect
  - the failing entry gets a stub client carrying a diagnostic, so
    ``GET /servers/{name}/details`` returns the reason instead of 404-ing into
    an empty Logs pane (the same mechanism preflight failures already use)
  - the stub is never spawned or connected

Injection point: ``_apply_escalation_overlay`` is called once per enabled
server from inside the setup body, which makes it a faithful stand-in for "an
unanticipated exception somewhere in per-entry setup". The test asserts the
*containment property*, not the specific call that failed.
"""

import json

import pytest

from app.mcp.manager import MCPManager


# Absolute path present on every POSIX box, so command preflight passes and
# execution reaches the setup body under test.
_REAL_COMMAND = "/bin/echo"

_BOOM = "synthetic per-entry setup failure"


@pytest.fixture
def manager(tmp_path):
    m = MCPManager()
    m.clients = {}
    m.server_configs = {}
    m._tool_fingerprints = {}
    m._quarantined_servers = set()
    m._fingerprint_store_path = tmp_path / "fingerprints.json"
    m._force_accepted_fingerprints = {}
    m._force_accept_store_path = tmp_path / "force_accepts.json"
    return m


async def _init_with(manager, server_configs, monkeypatch, tmp_path,
                     poison=None):
    """Drive the real _initialize_locked against a temp mcp_config.json.

    *poison*, when given, is a server name whose per-entry setup raises. All
    connections are stubbed to succeed without spawning; the subject is the
    setup path that runs before any spawn.

    Returns (ok, connected_names).
    """
    monkeypatch.setenv("ZIYA_ENABLE_MCP", "true")

    cfg_file = tmp_path / "mcp_config.json"
    cfg_file.write_text(json.dumps({"mcpServers": server_configs}))

    manager.builtin_server_definitions = {}
    manager.config_path = str(cfg_file)
    manager._server_enabled_overrides = {}
    # refresh_config_path() would re-discover the real ~/.ziya config.
    monkeypatch.setattr(manager, "refresh_config_path", lambda: None)

    real_overlay = manager._apply_escalation_overlay

    def _maybe_raise(server_env, server_name):
        if poison is not None and server_name == poison:
            raise RuntimeError(_BOOM)
        return real_overlay(server_env, server_name)

    monkeypatch.setattr(manager, "_apply_escalation_overlay", _maybe_raise)

    connected = []

    async def _fake_connect(server_name, client):
        connected.append(server_name)
        return True

    monkeypatch.setattr(manager, "_connect_server", _fake_connect)

    ok = await manager._initialize_locked()
    assert manager.server_configs, (
        "test harness failure: no server configs loaded, so the code under "
        "test never ran"
    )
    return ok, connected


def _healthy(name_count):
    return {
        f"good{i}": {"command": _REAL_COMMAND, "args": [], "enabled": True,
                     "builtin": False}
        for i in range(name_count)
    }


class TestHarnessIsHonest:
    """A containment test is worthless if the harness passes with no fix, so
    pin the un-poisoned path and the poison mechanism itself first."""

    @pytest.mark.asyncio
    async def test_all_servers_connect_when_nothing_fails(
        self, manager, monkeypatch, tmp_path
    ):
        cfg = _healthy(3)
        ok, connected = await _init_with(manager, cfg, monkeypatch, tmp_path)

        assert ok is True
        assert sorted(connected) == ["good0", "good1", "good2"]

    @pytest.mark.asyncio
    async def test_poison_actually_reaches_the_setup_body(
        self, manager, monkeypatch, tmp_path
    ):
        # If the overlay were never called for this entry the containment
        # assertions below would pass vacuously.
        called = []
        cfg = {"solo": {"command": _REAL_COMMAND, "args": [], "enabled": True,
                        "builtin": False}}

        monkeypatch.setenv("ZIYA_ENABLE_MCP", "true")
        cfg_file = tmp_path / "mcp_config.json"
        cfg_file.write_text(json.dumps({"mcpServers": cfg}))
        manager.builtin_server_definitions = {}
        manager.config_path = str(cfg_file)
        manager._server_enabled_overrides = {}
        monkeypatch.setattr(manager, "refresh_config_path", lambda: None)
        monkeypatch.setattr(
            manager, "_apply_escalation_overlay",
            lambda env, name: called.append(name),
        )

        async def _fake_connect(server_name, client):
            return True

        monkeypatch.setattr(manager, "_connect_server", _fake_connect)
        await manager._initialize_locked()

        assert called == ["solo"], (
            "the injection point is not on the per-entry setup path"
        )


class TestOneBadEntryDoesNotAbortTheRest:

    @pytest.mark.asyncio
    async def test_initialization_still_succeeds(
        self, manager, monkeypatch, tmp_path
    ):
        cfg = {**_healthy(2),
               "poison": {"command": _REAL_COMMAND, "args": [],
                          "enabled": True, "builtin": False}}
        ok, _ = await _init_with(manager, cfg, monkeypatch, tmp_path,
                                 poison="poison")

        assert ok is True, (
            "REGRESSION: a per-entry setup exception aborted the whole "
            "initialization -- every other MCP server was lost"
        )

    @pytest.mark.asyncio
    async def test_healthy_siblings_still_connect(
        self, manager, monkeypatch, tmp_path
    ):
        cfg = {**_healthy(2),
               "poison": {"command": _REAL_COMMAND, "args": [],
                          "enabled": True, "builtin": False}}
        _, connected = await _init_with(manager, cfg, monkeypatch, tmp_path,
                                        poison="poison")

        assert sorted(connected) == ["good0", "good1"], (
            f"expected both healthy servers to connect, got {connected}"
        )
        assert "poison" not in connected, (
            "the entry whose setup failed must not be connected"
        )

    @pytest.mark.asyncio
    async def test_siblings_ordered_after_the_failure_are_not_skipped(
        self, manager, monkeypatch, tmp_path
    ):
        # Dict order is insertion order, so a poisoned FIRST entry is the case
        # that previously lost everything. Ordering matters here, so this is
        # asserted separately from the general case above.
        cfg = {}
        cfg["poison"] = {"command": _REAL_COMMAND, "args": [],
                         "enabled": True, "builtin": False}
        cfg.update(_healthy(2))
        _, connected = await _init_with(manager, cfg, monkeypatch, tmp_path,
                                        poison="poison")

        assert sorted(connected) == ["good0", "good1"], (
            "entries after the failing one were abandoned"
        )


class TestFailedEntryIsDiagnosable:
    """A contained failure that leaves no trace is a silent failure. The GUI
    reads self.clients, so the entry must still be present with a reason."""

    @pytest.mark.asyncio
    async def test_failed_entry_gets_a_stub_client(
        self, manager, monkeypatch, tmp_path
    ):
        cfg = {"poison": {"command": _REAL_COMMAND, "args": [],
                          "enabled": True, "builtin": False}}
        await _init_with(manager, cfg, monkeypatch, tmp_path, poison="poison")

        assert "poison" in manager.clients, (
            "REGRESSION: the failed entry was dropped from clients -- "
            "/servers/poison/details will 404 into an empty Logs pane"
        )

    @pytest.mark.asyncio
    async def test_stub_carries_a_structured_diagnostic(
        self, manager, monkeypatch, tmp_path
    ):
        cfg = {"poison": {"command": _REAL_COMMAND, "args": [],
                          "enabled": True, "builtin": False}}
        await _init_with(manager, cfg, monkeypatch, tmp_path, poison="poison")

        failure = manager.clients["poison"].preflight_failure
        assert failure is not None, "no diagnostic recorded for the failure"
        assert failure["code"] == "setup_failed"
        # The underlying error text must survive: without it the card says
        # only "something went wrong".
        blob = f"{failure.get('summary')} {failure.get('detail')}"
        assert _BOOM in blob, (
            f"the actual error was lost; diagnostic said: {blob!r}"
        )

    @pytest.mark.asyncio
    async def test_stub_reason_is_visible_in_the_logs_pane(
        self, manager, monkeypatch, tmp_path
    ):
        cfg = {"poison": {"command": _REAL_COMMAND, "args": [],
                          "enabled": True, "builtin": False}}
        await _init_with(manager, cfg, monkeypatch, tmp_path, poison="poison")

        logs = " ".join(manager.clients["poison"].logs)
        assert _BOOM in logs, (
            "the GUI Logs pane reads client.logs; the reason is not there"
        )

    @pytest.mark.asyncio
    async def test_stub_is_not_connected_and_offers_no_tools(
        self, manager, monkeypatch, tmp_path
    ):
        cfg = {"poison": {"command": _REAL_COMMAND, "args": [],
                          "enabled": True, "builtin": False}}
        await _init_with(manager, cfg, monkeypatch, tmp_path, poison="poison")

        stub = manager.clients["poison"]
        assert stub.is_connected is False
        assert stub.tools == []

    @pytest.mark.asyncio
    async def test_stub_refuses_to_spawn_on_a_later_connect(
        self, manager, monkeypatch, tmp_path
    ):
        # preflight_failure makes connect() terminal, so a health check or
        # reconnect cannot resurrect an entry whose config cannot be prepared.
        cfg = {"poison": {"command": _REAL_COMMAND, "args": [],
                          "enabled": True, "builtin": False}}
        await _init_with(manager, cfg, monkeypatch, tmp_path, poison="poison")

        stub = manager.clients["poison"]
        assert await stub.connect() is False
        assert stub.process is None, "a stub client must never be spawned"
