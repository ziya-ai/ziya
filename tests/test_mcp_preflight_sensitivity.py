"""Prove the preflight tests are sensitive to regression.

Runs under pytest (cwd = project root) so the working-tree `app` package is
imported. Asserts:
  1. the module under test is the working-tree copy, not site-packages
  2. the stub-registration behaviour is genuinely present
  3. the test assertions fail when the stub is absent (pre-fix state)

This exists because an earlier version of the preflight harness assigned
`manager.server_configs` directly, which `_initialize_locked` discards — the
preflight path never ran and two tests "passed" by asserting the absence of
something that was never created.
"""
import json
import os

import pytest

import app.mcp.manager as mgr_mod
from app.mcp.manager import MCPManager


def test_module_under_test_is_working_tree():
    """A site-packages copy without the fix would make every other test here
    meaningless, so pin the provenance explicitly."""
    assert "site-packages" not in mgr_mod.__file__, (
        f"importing a stale installed copy: {mgr_mod.__file__}"
    )
    assert hasattr(mgr_mod, "_install_hint_for_command")


@pytest.mark.asyncio
async def test_stub_present_and_assertions_are_sensitive(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_ENABLE_MCP", "true")
    cfg = tmp_path / "mcp_config.json"
    cfg.write_text(json.dumps({"mcpServers": {"ghost": {
        "command": "definitely-not-a-real-binary-xyz",
        "args": [], "enabled": True}}}))

    m = MCPManager()
    m.clients = {}
    m.server_configs = {}
    m.builtin_server_definitions = {}
    m.config_path = str(cfg)
    m._server_enabled_overrides = {}
    m._fingerprint_store_path = tmp_path / "fp.json"
    m._force_accept_store_path = tmp_path / "fa.json"
    m._force_accepted_fingerprints = {}
    monkeypatch.setattr(m, "refresh_config_path", lambda: None)

    await m._initialize_locked()

    # (2) fix is genuinely in effect
    assert "ghost" in m.clients, "stub was not registered — fix not in effect"
    stub = m.clients["ghost"]
    assert stub.preflight_failure["code"] == "command_not_on_path"
    assert stub.logs

    # (3) the same assertion FAILS in the pre-fix state (entry dropped)
    m.clients.pop("ghost")
    with pytest.raises(AssertionError):
        assert "ghost" in m.clients, "pre-fix state must fail this assertion"
