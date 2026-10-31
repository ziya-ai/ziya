"""
Provenance of each configured MCP server, as shown in the MCP status modal.

The modal's expanded panel says where a server came from: built-in, a plugin,
a hand-written (or tool-written) config entry, or a registry install, plus a
note when the stanza uses keys another harness writes. That information is
recorded by the config loader in ``MCPManager.server_sources`` and served by
``get_server_sources()``; it is kept OUT of ``server_configs`` so it is never
written back to mcp_config.json or shown as part of the config stanza.

The loader tests drive the real ``_initialize_locked`` against a temp
mcp_config.json (connections stubbed), the same harness as
test_mcp_config_none_normalization.py.
"""

import json

import pytest

from app.mcp.manager import MCPManager

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


async def _init_with(manager, servers, monkeypatch, tmp_path, builtins=None):
    monkeypatch.setenv("ZIYA_ENABLE_MCP", "true")
    cfg_file = tmp_path / "mcp_config.json"
    cfg_file.write_text(json.dumps({"mcpServers": servers}))
    manager.builtin_server_definitions = builtins or {}
    manager.config_path = str(cfg_file)
    manager._server_enabled_overrides = {}
    monkeypatch.setattr(manager, "refresh_config_path", lambda: None)

    async def _fake_connect(server_name, client):
        return True

    monkeypatch.setattr(manager, "_connect_server", _fake_connect)
    await manager._initialize_locked()
    assert manager.server_configs, "harness failure: no configs loaded"
    return str(cfg_file)


class TestLoaderRecordsSource:
    @pytest.mark.asyncio
    async def test_manual_entry_is_a_config_file_source_with_its_path(
        self, manager, monkeypatch, tmp_path
    ):
        path = await _init_with(
            manager, {"handmade": {"command": _REAL_COMMAND, "args": []}},
            monkeypatch, tmp_path,
        )
        src = manager.get_server_sources()["handmade"]
        assert src["kind"] == "config_file"
        assert src["file"] == path
        assert "foreign_keys" not in src

    @pytest.mark.asyncio
    async def test_registry_entry_is_a_registry_source(
        self, manager, monkeypatch, tmp_path
    ):
        path = await _init_with(
            manager,
            {"reg": {"command": _REAL_COMMAND, "args": [],
                     "registry_provider": "official_mcp", "service_id": "svc-1"}},
            monkeypatch, tmp_path,
        )
        src = manager.get_server_sources()["reg"]
        assert src["kind"] == "registry"
        assert src["provider"] == "official_mcp"
        assert src["service_id"] == "svc-1"
        assert src["file"] == path

    @pytest.mark.asyncio
    async def test_other_harness_keys_are_reported(
        self, manager, monkeypatch, tmp_path
    ):
        # "disabled" is popped by the compatibility shim, so it must be
        # captured before normalization or it can never be reported.
        await _init_with(
            manager,
            {"kiro": {"url": "https://example.invalid/mcp",
                      "transportType": "sse", "disabled": False}},
            monkeypatch, tmp_path,
        )
        src = manager.get_server_sources()["kiro"]
        assert src["kind"] == "config_file"
        assert set(src["foreign_keys"]) == {"transportType", "disabled"}
        # The shim still ran: the stanza itself carries no "disabled".
        assert "disabled" not in manager.server_configs["kiro"]

    @pytest.mark.asyncio
    async def test_builtin_override_names_the_overriding_file(
        self, manager, monkeypatch, tmp_path
    ):
        path = await _init_with(
            manager, {"shellish": {"enabled": True}}, monkeypatch, tmp_path,
            builtins={"shellish": {"command": _REAL_COMMAND, "args": [],
                                   "builtin": True}},
        )
        src = manager.get_server_sources()["shellish"]
        assert src == {"kind": "builtin", "overridden_in": path}

    @pytest.mark.asyncio
    async def test_provenance_is_not_part_of_the_stanza(
        self, manager, monkeypatch, tmp_path
    ):
        await _init_with(
            manager, {"handmade": {"command": _REAL_COMMAND, "args": []}},
            monkeypatch, tmp_path,
        )
        cfg = manager.server_configs["handmade"]
        assert "kind" not in cfg and "file" not in cfg and "foreign_keys" not in cfg


class TestFallbackWithoutLoaderRecord:
    def test_registry_install_via_restart_is_still_a_registry_source(self):
        # restart_server(name, new_config) registers a config the loader
        # never saw; the registry metadata on the config identifies it.
        m = MCPManager.__new__(MCPManager)
        m.server_configs = {
            "late": {"command": "x", "registry_provider": "amazon_registry",
                     "builtin": False},
            "core": {"command": "y", "builtin": True},
            "mystery": {"command": "z", "builtin": False},
        }
        sources = m.get_server_sources()
        assert sources["late"]["kind"] == "registry"
        assert sources["late"]["provider"] == "amazon_registry"
        assert sources["core"]["kind"] == "builtin"
        assert sources["mystery"]["kind"] == "runtime"
