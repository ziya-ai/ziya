"""A registry install that cannot start must report why, and must not persist.

Regression coverage for the failure a new user hit: installing an MCP service
whose launcher needs a runtime that is not present. The server was written to
mcp_config.json, the process exited 127 ("exec: node: not found"), and the
install still answered success -- so the UI listed an installed server with a
registry description and zero tools, with no stated reason.

Two seams are asserted here, because each half was independently silent:
  * client.py must turn the launcher's own stderr into a structured record
    (the configured command exists in this case -- an internal-toolbox shim
    symlinked to a compiled dispatcher -- so no static check of that path can
    see the interpreter it execs; the shell's error text is the only evidence).
  * registry_manager.install_service must consume the connect result, roll the
    config entry back, and return the diagnosis with an install hint.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.mcp.client import _detect_missing_runtime, _missing_runtime_failure
from app.mcp.registry_manager import RegistryIntegrationManager
from app.mcp.registry.interface import InstallationResult


# The verbatim stderr captured from the real failure.
OBSERVED_STDERR = (
    "STDERR: /Users/x/.toolbox/tools/quip-mcp/1.0.2/quip-mcp/quip-mcp-server: "
    "line 6: exec: node: not found\n"
    "ERROR: Server process exited during initialization (exit code 127)"
)


class TestMissingRuntimeDetection:
    def test_identifies_node_from_observed_stderr(self):
        assert _detect_missing_runtime(OBSERVED_STDERR) == "node"

    @pytest.mark.parametrize("text,expected", [
        ("sh: 1: node: not found", "node"),
        ("/usr/bin/env: 'python3': No such file or directory", "python3"),
        ("env: uvx: No such file or directory", "uvx"),
    ])
    def test_identifies_other_launcher_shapes(self, text, expected):
        assert _detect_missing_runtime(text) == expected

    def test_python3_not_reported_as_python(self):
        """Longest-first matching: a missing python3 must not name python."""
        assert _detect_missing_runtime("sh: python3: not found") == "python3"

    @pytest.mark.parametrize("text", [
        "",
        "INFO: server ready",
        "ValueError: config key 'node_id' not found in payload",
    ])
    def test_no_false_positives(self, text):
        """A wrong 'missing runtime' verdict would blame the user's machine for
        a server-side error, so the negative case is load-bearing."""
        assert _detect_missing_runtime(text) is None

    def test_failure_record_carries_actionable_hint(self):
        record = _missing_runtime_failure("node", 127)
        assert record["code"] == "runtime_not_found"
        assert "node" in record["summary"]
        assert "127" in record["detail"]
        # The whole point is telling the user what to install.
        assert record["hint"]
        assert "node" in record["hint"].lower()


def _manager(tmp_path: Path, *, connected: bool, client=None):
    """Build a RegistryIntegrationManager without its heavyweight __init__.

    __init__ initializes every registry provider and the global MCP manager;
    neither is under test here.
    """
    mgr = object.__new__(RegistryIntegrationManager)
    config_path = tmp_path / "mcp_config.json"
    config_path.write_text(json.dumps({"mcpServers": {}}))
    mgr.config_path = str(config_path)

    async def restart_server(name, cfg):
        return connected

    mgr.mcp_manager = SimpleNamespace(
        is_initialized=True,
        restart_server=restart_server,
        clients={"quip-mcp": client} if client else {},
        server_configs={},
        invalidate_tools_cache=lambda: None,
    )
    return mgr, config_path


class _StubProvider:
    identifier = "official-mcp"

    def __init__(self, result):
        self._result = result

    async def install_service(self, service_id, config_path):
        return self._result


def _install_result():
    return InstallationResult(
        success=True,
        service_id="io.example/quip-mcp",
        server_name="quip-mcp",
        installation_path="/tmp/quip-mcp",
        config_entries={
            "enabled": True,
            "command": "/home/x/.toolbox/bin/quip-mcp-server",
            "registry_provider": "official-mcp",
        },
    )


def _bind_provider(mgr, provider):
    mgr.provider_registry = SimpleNamespace(get_provider=lambda pid: provider)


@pytest.mark.asyncio
class TestInstallReportsConnectFailure:
    async def test_failed_connect_is_not_reported_as_success(self, tmp_path):
        failed_client = SimpleNamespace(
            startup_failure=_missing_runtime_failure("node", 127),
            preflight_failure=None,
            logs=["STDERR: exec: node: not found"],
        )
        mgr, config_path = _manager(tmp_path, connected=False, client=failed_client)
        _bind_provider(mgr, _StubProvider(_install_result()))

        result = await mgr.install_service("io.example/quip-mcp", "official-mcp")

        assert result["status"] == "error", "a server that never started is not installed"
        assert "node" in result["error"]
        assert result["hint"], "the user must be told what to install"
        assert result["failure_code"] == "runtime_not_found"
        assert result["config_updated"] is False

    async def test_failed_connect_rolls_back_the_config_entry(self, tmp_path):
        failed_client = SimpleNamespace(
            startup_failure=_missing_runtime_failure("node", 127),
            preflight_failure=None,
            logs=[],
        )
        mgr, config_path = _manager(tmp_path, connected=False, client=failed_client)
        _bind_provider(mgr, _StubProvider(_install_result()))

        await mgr.install_service("io.example/quip-mcp", "official-mcp")

        persisted = json.loads(config_path.read_text())
        assert "quip-mcp" not in persisted.get("mcpServers", {}), (
            "a launcher that cannot run must not be left in the user's config"
        )
        assert "quip-mcp" not in mgr.mcp_manager.clients, (
            "a rolled-back server must not linger in the status poll"
        )

    async def test_diagnosis_falls_back_when_client_has_no_record(self, tmp_path):
        """No structured record (e.g. handshake timeout) must still explain
        itself rather than reverting to a bare 'restart failed'."""
        mgr, config_path = _manager(tmp_path, connected=False, client=SimpleNamespace(
            startup_failure=None, preflight_failure=None, logs=["INFO: spawned"],
        ))
        _bind_provider(mgr, _StubProvider(_install_result()))

        result = await mgr.install_service("io.example/quip-mcp", "official-mcp")

        assert result["status"] == "error"
        assert result["failure_code"] == "connect_failed"
        assert result["detail"]

    async def test_successful_connect_still_installs(self, tmp_path):
        """Positive control: the failure path must not have broken the one that
        works, and the entry must survive."""
        mgr, config_path = _manager(tmp_path, connected=True)
        _bind_provider(mgr, _StubProvider(_install_result()))

        result = await mgr.install_service("io.example/quip-mcp", "official-mcp")

        assert result["status"] == "success"
        assert result["connected"] is True
        persisted = json.loads(config_path.read_text())
        assert "quip-mcp" in persisted["mcpServers"]
