"""
Tests for MCP preflight diagnostics.

Before this change, a server whose command was missing from PATH was dropped
during ``_initialize_locked`` with a bare ``continue``: the name stayed in
``server_configs`` (so the GUI listed it as "disconnected") but never entered
``self.clients``, so ``GET /servers/{name}/details`` 404'd, so the frontend
swallowed the error into ``{logs: []}`` and rendered "no logs available". The
actual reason went to a ``logger.info`` in the terminal and was discarded.

The fix registers a non-connecting stub ``MCPClient`` carrying a structured
``preflight_failure`` diagnostic. These tests pin the contract the GUI failure
card depends on:

  - a preflight failure produces a client (not a dropped entry)
  - the client is never spawned, on any path, including reconnect/health-check
  - the stub cannot contribute tools or be selected for a tool call
  - the diagnostic distinguishes which check failed, and why
  - /details returns 200 with the diagnostic instead of 404
"""

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.mcp.client import MCPClient
from app.mcp.manager import MCPManager, _install_hint_for_command


# --------------------------------------------------------------------------
# _install_hint_for_command
# --------------------------------------------------------------------------

class TestInstallHint:
    """The hint is what makes the failure card actionable rather than merely
    honest, so it must name the right provider and never omit the
    version-manager caveat (the common false negative)."""

    def test_known_launcher_names_its_provider(self):
        hint = _install_hint_for_command("npx")
        assert "Node.js" in hint
        assert "brew install node" in hint

    def test_known_launcher_resolved_from_absolute_path(self):
        # A configured absolute path must still map to its provider — the
        # lookup keys on basename, not the full string.
        hint = _install_hint_for_command("/opt/homebrew/bin/uvx")
        assert "uv" in hint
        assert "brew install uv" in hint

    def test_unknown_command_gets_generic_guidance_not_a_guess(self):
        # A wrong-but-confident install instruction is worse than none.
        hint = _install_hint_for_command("totally-made-up-binary")
        assert "totally-made-up-binary" in hint
        assert "brew install" not in hint

    @pytest.mark.parametrize("cmd", ["npx", "node", "uvx", "some-unknown-thing"])
    def test_version_manager_caveat_always_present(self, cmd):
        # Users who DO have the binary installed (via nvm/asdf) must not be
        # told to install it again with no other explanation.
        hint = _install_hint_for_command(cmd)
        assert "version manager" in hint
        assert "login shell" in hint


# --------------------------------------------------------------------------
# MCPClient preflight guard
# --------------------------------------------------------------------------

class TestClientPreflightGuard:
    """A preflight failure is terminal for the client's life: the command does
    not exist, so spawning can only reproduce the error. The guard lives in
    connect() so no caller — reconnect, health check, restart — can resurrect
    a known-bad launch."""

    def test_fresh_client_has_no_failure_and_config_stage(self):
        client = MCPClient({"name": "srv", "command": ["echo"]})
        assert client.preflight_failure is None
        assert client.startup_stage == "config"

    @pytest.mark.asyncio
    async def test_connect_refuses_when_preflight_failed(self):
        client = MCPClient({"name": "srv", "command": ["definitely-not-real"]})
        client.preflight_failure = {"code": "command_not_on_path"}

        with patch(
            "asyncio.create_subprocess_exec", new=AsyncMock()
        ) as spawn:
            result = await client.connect()

        assert result is False
        assert client.is_connected is False
        spawn.assert_not_called(), "preflight-failed client must never spawn"

    @pytest.mark.asyncio
    async def test_repeated_connect_attempts_never_spawn(self):
        """The health-check path calls connect() on any client with
        is_connected False. A missing binary must not be re-invoked on every
        such attempt."""
        client = MCPClient({"name": "srv", "command": ["nope"]})
        client.preflight_failure = {"code": "command_not_on_path"}

        with patch(
            "asyncio.create_subprocess_exec", new=AsyncMock()
        ) as spawn:
            for _ in range(5):
                assert await client.connect() is False

        spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_guard_precedes_remote_transport_branch(self):
        """A URL-configured server that somehow carries a preflight failure
        must still short-circuit — the guard must sit above the remote
        branch, not below it."""
        client = MCPClient({"name": "srv", "url": "https://example.invalid/mcp"})
        client.preflight_failure = {"code": "command_not_on_path"}

        with patch.object(
            client, "_connect_remote", new=AsyncMock(return_value=True)
        ) as remote:
            assert await client.connect() is False

        remote.assert_not_called()

    @pytest.mark.asyncio
    async def test_disconnect_is_safe_on_never_spawned_client(self):
        """Stubs live in manager.clients, and _shutdown_locked calls
        disconnect() unguarded. That must be a harmless no-op when no process
        and no SDK stack exist."""
        client = MCPClient({"name": "srv", "command": ["nope"]})
        client.preflight_failure = {"code": "command_not_on_path"}
        await client.disconnect()  # must not raise
        assert client.is_connected is False


# --------------------------------------------------------------------------
# Manager preflight -> stub client
# --------------------------------------------------------------------------

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

    ``_initialize_locked`` rebuilds ``server_configs`` from builtins plus the
    user config file, so assigning ``manager.server_configs`` directly is
    discarded. Instead the servers under test are written to a real config
    file and builtins are emptied, so the genuine load -> preflight path runs.
    """
    monkeypatch.setenv("ZIYA_ENABLE_MCP", "true")

    cfg_file = tmp_path / "mcp_config.json"
    cfg_file.write_text(json.dumps({"mcpServers": server_configs}))

    manager.builtin_server_definitions = {}
    manager.config_path = str(cfg_file)
    manager._server_enabled_overrides = {}
    # refresh_config_path() would re-discover the user's real ~/.ziya config
    # and clobber the temp one.
    monkeypatch.setattr(manager, "refresh_config_path", lambda: None)

    # Any server surviving preflight would spawn a real subprocess; fail loudly
    # instead so a broken preflight can't masquerade as a pass.
    async def _no_connect(server_name, client):
        raise AssertionError(
            f"_connect_server called for '{server_name}' — expected preflight "
            f"to have stopped it before any spawn"
        )

    monkeypatch.setattr(manager, "_connect_server", _no_connect)

    ok = await manager._initialize_locked()
    assert manager.server_configs, (
        "test harness failure: no server configs were loaded, so the preflight "
        "path never ran"
    )
    return ok


class TestManagerPreflightRegistersStub:

    @pytest.mark.asyncio
    async def test_command_not_on_path_creates_stub_client(
        self, manager, monkeypatch, tmp_path
    ):
        cfg = {
            "ghost": {
                "command": "definitely-not-a-real-binary-xyz",
                "args": [],
                "enabled": True,
                "builtin": False,
            }
        }
        await _init_with(manager, cfg, monkeypatch, tmp_path)

        assert "ghost" in manager.clients, (
            "REGRESSION: preflight-failed server was dropped instead of "
            "registered — /details will 404 into an empty Logs pane"
        )
        stub = manager.clients["ghost"]
        assert stub.preflight_failure is not None
        assert stub.preflight_failure["code"] == "command_not_on_path"
        assert stub.startup_stage == "preflight"
        assert stub.is_connected is False

    @pytest.mark.asyncio
    async def test_diagnostic_explains_absent_logs(
        self, manager, monkeypatch, tmp_path
    ):
        """The load-bearing message: users assume log capture is broken when
        the truth is no process was ever created."""
        cfg = {
            "ghost": {
                "command": "definitely-not-a-real-binary-xyz",
                "args": [],
                "enabled": True,
                "builtin": False,
            }
        }
        await _init_with(manager, cfg, monkeypatch, tmp_path)

        failure = manager.clients["ghost"].preflight_failure
        assert "nothing ever ran" in failure["detail"]
        assert failure["hint"]
        assert failure["searched"], "PATH entries searched must be reported"

    @pytest.mark.asyncio
    async def test_stub_logs_are_non_empty(
        self, manager, monkeypatch, tmp_path
    ):
        """The Logs tab reads client.logs; it must not be empty for a
        never-started server."""
        cfg = {
            "ghost": {
                "command": "definitely-not-a-real-binary-xyz",
                "args": [],
                "enabled": True,
                "builtin": False,
            }
        }
        await _init_with(manager, cfg, monkeypatch, tmp_path)

        logs = manager.clients["ghost"].logs
        assert logs, "REGRESSION: never-started server has empty logs"
        assert any("ERROR" in line for line in logs)

    @pytest.mark.asyncio
    async def test_absolute_command_missing_is_distinguished(
        self, manager, monkeypatch, tmp_path
    ):
        missing = str(tmp_path / "no-such-binary")
        cfg = {
            "abs": {
                "command": missing,
                "args": [],
                "enabled": True,
                "builtin": False,
            }
        }
        await _init_with(manager, cfg, monkeypatch, tmp_path)

        failure = manager.clients["abs"].preflight_failure
        assert failure["code"] == "command_missing_at_path"
        assert missing in failure["searched"]

    @pytest.mark.asyncio
    async def test_missing_script_is_distinguished_from_missing_command(
        self, manager, monkeypatch, tmp_path
    ):
        """Command present, script absent — a different user action is needed,
        so it must not be reported as 'command not found'."""
        missing_script = str(tmp_path / "server.py")
        cfg = {
            "scripted": {
                "command": sys.executable,   # real, on disk
                "args": [missing_script],    # absolute, absent
                "enabled": True,
                "builtin": False,
            }
        }
        await _init_with(manager, cfg, monkeypatch, tmp_path)

        failure = manager.clients["scripted"].preflight_failure
        assert failure["code"] == "script_not_found"
        assert missing_script in failure["searched"]

    @pytest.mark.asyncio
    async def test_stub_contributes_no_tools(
        self, manager, monkeypatch, tmp_path
    ):
        """Stubs share manager.clients with live clients. Every collection
        site guards on is_connected; verify the stub cannot leak tools."""
        cfg = {
            "ghost": {
                "command": "definitely-not-a-real-binary-xyz",
                "args": [],
                "enabled": True,
                "builtin": False,
            }
        }
        await _init_with(manager, cfg, monkeypatch, tmp_path)

        assert manager.get_all_tools() == []
        assert manager.get_all_resources() == []
        assert manager.get_all_prompts() == []

    @pytest.mark.asyncio
    async def test_disabled_server_is_not_given_a_stub(
        self, manager, monkeypatch, tmp_path
    ):
        """A deliberately disabled server is not a failure and must not be
        reported as one."""
        cfg = {
            "off": {
                "command": "definitely-not-a-real-binary-xyz",
                "args": [],
                "enabled": False,
                "builtin": False,
            }
        }
        await _init_with(manager, cfg, monkeypatch, tmp_path)

        assert "off" not in manager.clients

    @pytest.mark.asyncio
    async def test_status_reports_stub_as_disconnected(
        self, manager, monkeypatch, tmp_path
    ):
        cfg = {
            "ghost": {
                "command": "definitely-not-a-real-binary-xyz",
                "args": [],
                "enabled": True,
                "builtin": False,
            }
        }
        await _init_with(manager, cfg, monkeypatch, tmp_path)

        status = manager.get_server_status()
        assert status["ghost"]["connected"] is False
        assert status["ghost"]["tools"] == 0
