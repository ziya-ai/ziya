"""
A builtin-server override stanza that carries only ``env`` (no ``command``)
must still be merged over the builtin definition.

``shell_config._ensure_shell_env`` (CLI ``/shell`` allowlist edits, ``reset``,
``ziya-approve`` signing) creates the ``mcpServers.shell`` stanza with just
``enabled`` / ``description`` / ``env``; the builtin definition is what
supplies ``command`` and ``args``. The loader's "no way to start this server"
guard treated that stanza as unlaunchable and skipped it, so on every server
start the shell subprocess was spawned from the bare builtin definition:
floor allowlist, no ZIYA_SCOPE_SIG, and -- because no escalation was ever
seen -- no clamp warning. Meanwhile GET /shell-config merges the on-disk env
over the live one for display, so the Shell Configuration modal kept showing
the user's signed additions (``ada``, ``aws``) as active.

Verified against the unpatched loader (2026-09-22): the stanza is skipped,
``server_configs["shell"]`` has no ``env`` at all, and the running subprocess
reports only the builtin floor.
"""

import json
from pathlib import Path

import pytest

import app.mcp.manager as _manager_mod
from app.mcp.manager import MCPManager


# A real script path so the builtin preflight passes and the loader reaches
# the user-config merge under test.
_REAL_SHELL_SCRIPT = str(
    Path(_manager_mod.__file__).resolve().parent.parent / "mcp_servers" / "shell_server.py"
)

_BUILTIN_SHELL = {
    "command": "/usr/bin/python3",
    "args": ["-u", _REAL_SHELL_SCRIPT],
    "workspace_scoped": True,
    "enabled": True,
    "description": "Provides shell command execution",
    "builtin": True,
}

# The shape shell_config._ensure_shell_env writes: no command, no args.
_DISK_SHELL_STANZA = {
    "enabled": True,
    "description": "Shell command execution server",
    "env": {
        "ALLOW_COMMANDS": "ls,cat,ada,aws",
        "ZIYA_SCOPE_SIG": "c2lnbmF0dXJl",
    },
}


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


async def _init_with(manager, user_servers, builtins, monkeypatch, tmp_path):
    monkeypatch.setenv("ZIYA_ENABLE_MCP", "true")

    cfg_file = tmp_path / "mcp_config.json"
    cfg_file.write_text(json.dumps({"mcpServers": user_servers}))

    manager.builtin_server_definitions = builtins
    manager.config_path = str(cfg_file)
    manager._server_enabled_overrides = {}
    monkeypatch.setattr(manager, "refresh_config_path", lambda: None)

    async def _fake_connect(server_name, client):
        return True

    monkeypatch.setattr(manager, "_connect_server", _fake_connect)

    ok = await manager._initialize_locked()
    assert ok, "initialize returned False; the loader never reached the merge"
    return manager.server_configs


@pytest.mark.asyncio
async def test_env_only_shell_stanza_merges_over_builtin(manager, monkeypatch, tmp_path):
    configs = await _init_with(
        manager, {"shell": _DISK_SHELL_STANZA}, {"shell": dict(_BUILTIN_SHELL)},
        monkeypatch, tmp_path,
    )

    shell = configs["shell"]
    # The user's privilege-bearing env must reach the config the spawn paths
    # clone -- this is the value the subprocess re-verifies at init.
    assert shell.get("env", {}).get("ALLOW_COMMANDS") == "ls,cat,ada,aws"
    assert shell["env"].get("ZIYA_SCOPE_SIG") == "c2lnbmF0dXJl"
    # ...while the launch details still come from the builtin definition.
    assert shell["command"] == _BUILTIN_SHELL["command"]
    assert shell["args"] == _BUILTIN_SHELL["args"]
    assert shell["builtin"] is True


@pytest.mark.asyncio
async def test_non_builtin_stanza_without_command_is_still_skipped(manager, monkeypatch, tmp_path):
    """The guard must keep rejecting genuinely unlaunchable user servers."""
    configs = await _init_with(
        manager,
        {
            "shell": _DISK_SHELL_STANZA,
            "orphan": {"enabled": True, "env": {"X": "1"}},
        },
        {"shell": dict(_BUILTIN_SHELL)},
        monkeypatch, tmp_path,
    )
    assert "orphan" not in configs
    assert configs["shell"]["env"]["ALLOW_COMMANDS"] == "ls,cat,ada,aws"
