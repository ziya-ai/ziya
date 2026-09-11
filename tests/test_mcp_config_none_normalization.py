"""
An explicit ``null`` for ``args`` / ``env`` must not corrupt argv or abort
initialization for every other MCP server.

Two distinct defects, both reachable from a hand-written config and from any
registry provider that emits an explicit null (``validate_config`` only *warns*
about either -- it never rejects the entry, so the loader still reaches them):

  1. ``"env": null`` -> ``server_env.update(None)`` raises TypeError. Every
     server is built inside ONE try/except that returns False on any exception,
     so this is not contained to the offending entry: initialization aborts and
     NO server connects. Verified against the unpatched loader:
         env=null: initialize->False  connected=[]

  2. ``"args": null`` -> the resilience normalizer coerced any non-list with
     ``[str(value)]``, turning null into the literal one-element argv
     ``['None']``. That string is passed to the server process as a real
     argument. Verified against the unpatched loader:
         args=null: client_args=['None']

The string case (``"args": "-v"`` -> ``["-v"]``) already worked and is pinned
here so the None special-case cannot regress it.
"""

import json

import pytest

from app.mcp.manager import MCPManager


# An absolute path present on every POSIX box, so command preflight passes and
# execution reaches the args/env handling under test.
_REAL_COMMAND = "/bin/echo"


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


async def _init_with(manager, server_configs, monkeypatch, tmp_path):
    """Drive the real _initialize_locked against a temp mcp_config.json.

    Connections are stubbed to succeed without spawning: the subject is the
    config handling that runs BEFORE any spawn. Returns (ok, connected_names).
    """
    monkeypatch.setenv("ZIYA_ENABLE_MCP", "true")

    cfg_file = tmp_path / "mcp_config.json"
    cfg_file.write_text(json.dumps({"mcpServers": server_configs}))

    manager.builtin_server_definitions = {}
    manager.config_path = str(cfg_file)
    manager._server_enabled_overrides = {}
    # refresh_config_path() would re-discover the real ~/.ziya config.
    monkeypatch.setattr(manager, "refresh_config_path", lambda: None)

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


class TestNullEnvIsContained:
    """`env: null` must not take the whole MCP subsystem down with it."""

    @pytest.mark.asyncio
    async def test_null_env_does_not_abort_initialization(
        self, manager, monkeypatch, tmp_path
    ):
        cfg = {
            "broken": {"command": _REAL_COMMAND, "args": [], "env": None,
                       "enabled": True, "builtin": False},
        }
        ok, connected = await _init_with(manager, cfg, monkeypatch, tmp_path)

        assert ok is True, (
            "REGRESSION: 'env': null raised in server_env.update(None) inside "
            "the single try/except wrapping the whole server loop"
        )
        assert "broken" in connected

    @pytest.mark.asyncio
    async def test_null_env_does_not_disconnect_healthy_siblings(
        self, manager, monkeypatch, tmp_path
    ):
        # The blast radius is the real severity: one malformed entry silently
        # cost the user every other server, with a single terminal line as the
        # only evidence.
        cfg = {
            "good_one": {"command": _REAL_COMMAND, "args": ["a"],
                         "enabled": True, "builtin": False},
            "broken": {"command": _REAL_COMMAND, "env": None,
                       "enabled": True, "builtin": False},
            "good_two": {"command": _REAL_COMMAND, "args": ["b"],
                         "enabled": True, "builtin": False},
        }
        ok, connected = await _init_with(manager, cfg, monkeypatch, tmp_path)

        assert ok is True
        assert "good_one" in connected and "good_two" in connected, (
            f"REGRESSION: a malformed sibling took healthy servers down "
            f"(connected={connected})"
        )

    @pytest.mark.asyncio
    async def test_valid_env_is_still_applied(
        self, manager, monkeypatch, tmp_path
    ):
        # Paired positive: the None guard must not skip real env overrides.
        cfg = {
            "envy": {"command": _REAL_COMMAND, "args": [],
                     "env": {"ZIYA_TEST_MARKER": "applied"},
                     "enabled": True, "builtin": False},
        }
        ok, _ = await _init_with(manager, cfg, monkeypatch, tmp_path)

        assert ok is True
        client = manager.clients["envy"]
        assert client.server_config.get("env", {}).get("ZIYA_TEST_MARKER") == "applied"


class TestNullArgsDoesNotBecomeALiteralNone:
    """`args: null` must mean "no arguments", not the argument "None"."""

    @pytest.mark.asyncio
    async def test_null_args_yields_no_arguments(
        self, manager, monkeypatch, tmp_path
    ):
        # Asserted on the client the manager actually hands to the spawn path,
        # not on the normalizer in isolation.
        cfg = {
            "broken": {"command": _REAL_COMMAND, "args": None,
                       "enabled": True, "builtin": False},
        }
        ok, _ = await _init_with(manager, cfg, monkeypatch, tmp_path)

        assert ok is True
        got = manager.clients["broken"].server_config.get("args")
        assert got == [], (
            f"REGRESSION: 'args': null was stringified into argv (got {got!r}) "
            f"— the server is launched with a literal 'None' argument"
        )

    @pytest.mark.asyncio
    async def test_string_args_still_becomes_one_element(
        self, manager, monkeypatch, tmp_path
    ):
        # Paired positive: the existing string coercion must survive, and must
        # not degrade into character-wise iteration (['-', 'v']).
        cfg = {
            "stringy": {"command": _REAL_COMMAND, "args": "-v",
                        "enabled": True, "builtin": False},
        }
        ok, _ = await _init_with(manager, cfg, monkeypatch, tmp_path)

        assert ok is True
        assert manager.clients["stringy"].server_config.get("args") == ["-v"]

    @pytest.mark.asyncio
    async def test_valid_list_args_pass_through_unchanged(
        self, manager, monkeypatch, tmp_path
    ):
        cfg = {
            "fine": {"command": _REAL_COMMAND, "args": ["-y", "pkg"],
                     "enabled": True, "builtin": False},
        }
        ok, _ = await _init_with(manager, cfg, monkeypatch, tmp_path)

        assert ok is True
        assert manager.clients["fine"].server_config.get("args") == ["-y", "pkg"]
