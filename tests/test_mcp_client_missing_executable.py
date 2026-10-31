"""
A stdio MCP server whose executable is absent reports WHICH command is
missing and the PATH it was looked up on.

create_subprocess_exec raises FileNotFoundError without a filename, so the
log used to read "MCP server executable not found: [Errno 2] No such file or
directory" -- observed on a user's machine for csds-ddf-mcp, where the
command lived in ~/.toolbox/bin and that directory was not on Ziya's PATH.
"""

import asyncio

from app.mcp import client as client_mod
from app.mcp.client import MCPClient

MISSING = "zziya-definitely-not-installed-mcp"


def test_missing_executable_is_named_in_logs(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent-ziya-test-bin")
    # Ziya's logger does not propagate, so caplog sees nothing; record directly.
    errors = []
    monkeypatch.setattr(client_mod.logger, "error",
                        lambda msg, *a, **k: errors.append(str(msg)))
    client = MCPClient({"name": "absent", "command": MISSING, "args": []})

    ok = asyncio.run(client.connect())

    assert ok is False
    assert client.is_connected is False
    # Outermost surface: the per-server log the GUI shows.
    assert any(MISSING in line and "PATH" in line for line in client.logs), client.logs
    # The server log carries the PATH that was searched.
    assert any(MISSING in m and "/nonexistent-ziya-test-bin" in m for m in errors), errors


def test_present_executable_does_not_hit_the_missing_branch(monkeypatch):
    """Negative control: a real executable gets past spawn, so the
    'not on Ziya's PATH' message must not appear."""
    client = MCPClient({"name": "present", "command": "true", "args": []})
    asyncio.run(client.connect())
    assert not any("not on Ziya's PATH" in line for line in client.logs), client.logs
