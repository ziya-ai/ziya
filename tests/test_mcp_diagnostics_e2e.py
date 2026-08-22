"""End-to-end check: a config with several distinct problems must produce
config findings AND preflight stubs AND a renderable /details payload.

This test earned its place by finding a gap the unit tests missed: a server
rejected at the config stage is removed from server_configs entirely, so it has
no /details panel and its explanation must come from the findings panel. The
unit tests each verified one layer and none of them crossed that boundary.

Run under pytest from the project root so the working-tree `app` is imported.
"""
import json
import sys

import pytest

import app.mcp.manager as mgr_mod
from app.mcp.manager import MCPManager
from app.routes.mcp_routes import get_mcp_server_details
from unittest.mock import patch


BAD_CONFIG = {
    "mcpServers": {
        # config-stage: typo'd key -> no launch mechanism
        "filesystem": {
            "commands": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        },
        # config-stage: non-string env value
        "github": {
            "command": "npx",
            "env": {"GITHUB_TOKEN": None},
        },
        # preflight-stage: command genuinely absent
        "context7": {
            "command": "definitely-not-a-real-binary-xyz",
            "args": ["-y", "ctx"],
        },
    }
}


@pytest.mark.asyncio
async def test_end_to_end_diagnostics(tmp_path, monkeypatch, capsys):
    assert "site-packages" not in mgr_mod.__file__, mgr_mod.__file__

    monkeypatch.setenv("ZIYA_ENABLE_MCP", "true")
    cfg = tmp_path / "mcp_config.json"
    cfg.write_text(json.dumps(BAD_CONFIG, indent=2))

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

    async def _no_connect(name, client):
        raise AssertionError(f"unexpected spawn attempt for {name}")

    monkeypatch.setattr(m, "_connect_server", _no_connect)

    await m._initialize_locked()

    report = []

    # --- config findings -------------------------------------------------
    findings = m.config_findings
    report.append(f"config findings: {len(findings)}")
    for f in findings:
        report.append(
            f"  [{f['severity']:7}] L{f['line']} {f['server']}: {f['summary']}"
        )

    codes = {f["code"] for f in findings}
    assert "unknown_key_typo" in codes, codes
    assert "missing_launch_key" in codes, codes
    assert "env_value_not_string" in codes, codes

    # the typo must be reported with a line number and a suggestion
    typo = next(f for f in findings if f["code"] == "unknown_key_typo")
    assert typo["suggestion"] == "command"
    assert typo["line"] is not None

    # --- preflight stub --------------------------------------------------
    report.append(f"clients registered: {sorted(m.clients)}")
    assert "context7" in m.clients, "preflight stub missing"
    stub = m.clients["context7"]
    assert stub.startup_stage == "preflight"
    assert stub.preflight_failure["code"] == "command_not_on_path"
    report.append(f"  context7 stage={stub.startup_stage} "
                  f"code={stub.preflight_failure['code']}")
    report.append(f"  hint={stub.preflight_failure['hint'][:70]}...")

    # servers dropped at config stage must NOT get stubs (nothing to launch)
    assert "filesystem" not in m.clients

    # --- /details payload ------------------------------------------------
    with patch("app.routes.mcp_routes.get_mcp_manager", return_value=m):
        details = await get_mcp_server_details("context7")
    assert details["preflight_failure"]["code"] == "command_not_on_path"
    assert details["logs"], "logs must not be empty"
    report.append(f"  /details logs: {len(details['logs'])} line(s)")

    # A server rejected at the CONFIG stage is removed from server_configs
    # entirely, so /details 404s for it. That is the documented behaviour: its
    # explanation lives in the config-findings panel (asserted above), not in
    # the per-server Logs tab, because there is no server to have a tab.
    from fastapi import HTTPException as _HTTPExc
    assert "filesystem" not in m.server_configs
    with patch("app.routes.mcp_routes.get_mcp_manager", return_value=m):
        with pytest.raises(_HTTPExc) as ei:
            await get_mcp_server_details("filesystem")
    assert ei.value.status_code == 404
    report.append("  filesystem -> 404 (covered by findings panel)")

    # --- no tool leakage -------------------------------------------------
    assert m.get_all_tools() == []

    print("\n".join(report), file=sys.stderr)
