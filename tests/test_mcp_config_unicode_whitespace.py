"""
Seam test: MCPManager's config loader goes through loads_tolerant, so an
mcp_config.json whose indentation contains U+00A0 still loads its servers and
reports a non-fatal warning finding instead of a fatal config_error.  A file
that is malformed even after normalization keeps config_error, with a hint
naming the character.

Uses the same _initialize_locked harness as test_mcp_server_sources.py.
"""

import json

import pytest

from app.mcp.manager import MCPManager

NBSP = "\u00a0"


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


async def _init_with_text(manager, text, monkeypatch, tmp_path):
    monkeypatch.setenv("ZIYA_ENABLE_MCP", "true")
    cfg_file = tmp_path / "mcp_config.json"
    cfg_file.write_bytes(text.encode("utf-8"))
    manager.builtin_server_definitions = {}
    manager.config_path = str(cfg_file)
    manager._server_enabled_overrides = {}
    monkeypatch.setattr(manager, "refresh_config_path", lambda: None)

    async def _fake_connect(server_name, client):
        return True

    monkeypatch.setattr(manager, "_connect_server", _fake_connect)
    await manager._initialize_locked()


def _nbsp(text):
    return "\n".join(
        ln.replace("  ", NBSP + " ") for ln in text.splitlines()
    )


@pytest.mark.asyncio
async def test_nbsp_indented_config_loads_with_warning(manager, monkeypatch, tmp_path):
    text = _nbsp(json.dumps(
        {"mcpServers": {"pasted": {"command": "/bin/echo", "args": []}}}, indent=2))
    assert NBSP in text
    await _init_with_text(manager, text, monkeypatch, tmp_path)

    assert manager.config_error is None
    assert "pasted" in manager.server_configs
    ws = [f for f in manager.config_findings if f["code"] == "nonascii_whitespace"]
    assert len(ws) == 1
    assert ws[0]["severity"] == "warning"
    assert "U+00A0 NO-BREAK SPACE" in ws[0]["detail"]
    # Not rewritten silently.
    assert NBSP in (tmp_path / "mcp_config.json").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_bom_config_loads(manager, monkeypatch, tmp_path):
    text = "\ufeff" + json.dumps({"mcpServers": {"bom": {"command": "/bin/echo", "args": []}}})
    await _init_with_text(manager, text, monkeypatch, tmp_path)
    assert manager.config_error is None
    assert "bom" in manager.server_configs
    assert not [f for f in manager.config_findings if f["code"] == "nonascii_whitespace"]


@pytest.mark.asyncio
async def test_malformed_config_keeps_error_and_names_the_character(manager, monkeypatch, tmp_path):
    text = '{\n' + NBSP + ' "mcpServers": {"x": }\n}'
    await _init_with_text(manager, text, monkeypatch, tmp_path)
    assert manager.config_error is not None
    assert "line 2, column 1" in manager.config_error
    assert "U+00A0 NO-BREAK SPACE, not a space" in manager.config_error
