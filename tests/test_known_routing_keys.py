"""
_task_scope (and other routing keys) must pass through argument validation
without a misleading "Unknown parameter" warning, while still being preserved
(ASR F-001 follow-on — cosmetic, but prevents future readers mistaking the
warning for a dropped envelope).

The ZIYA logger has propagate=False, so caplog (root-attached) cannot observe
its records; we patch the module logger's ``warning`` to assert the contract.
"""

from unittest import mock
import pytest

from app.mcp.client import MCPClient


_SHELL_SCHEMA = {
    "type": "object",
    "properties": {"command": {"type": "string"},
                   "timeout": {"type": "number"}},
    "required": ["command"],
}


def _validator():
    # Bare instance is enough to exercise _validate_and_convert_arguments, but
    # the schema-constraint pass scans self.tools to label the tool, so give it
    # an empty list (no tool will match `inputSchema is schema`, leaving the
    # label "unknown" — fine for this test).
    c = MCPClient.__new__(MCPClient)
    c.tools = []
    return c


def test_task_scope_preserved_without_warning():
    v = _validator()
    args = {"command": "gh --version",
            "_task_scope": {"shell_commands": ["gh"], "writable": [],
                            "readable": [], "project_root": "/x"}}
    with mock.patch("app.mcp.client.logger.warning") as warn:
        out = v._validate_and_convert_arguments(args, _SHELL_SCHEMA)
    # Preserved intact …
    assert out["_task_scope"]["shell_commands"] == ["gh"]
    assert out["command"] == "gh --version"
    # … and no misleading warning about it.
    warned = " ".join(str(c.args) for c in warn.call_args_list)
    assert "_task_scope" not in warned


def test_genuinely_unknown_param_still_warns():
    v = _validator()
    args = {"command": "ls", "bogus_key": 1}
    with mock.patch("app.mcp.client.logger.warning") as warn:
        out = v._validate_and_convert_arguments(args, _SHELL_SCHEMA)
    assert out["bogus_key"] == 1                     # still preserved
    warned = " ".join(str(c.args) for c in warn.call_args_list)
    assert "bogus_key" in warned                     # but warns


def test_routing_keys_constant_contains_task_scope():
    assert "_task_scope" in MCPClient._KNOWN_ROUTING_KEYS
    assert "_workspace_path" in MCPClient._KNOWN_ROUTING_KEYS


def test_ledger_scope_preserved_without_warning():
    # MCPManager.call_tool attaches _ledger_scope = {url, token} to every
    # shell call (evidence ledger); the shell server pops it.  It must reach
    # the server intact and must not log "Unknown parameter: _ledger_scope".
    v = _validator()
    envelope = {"url": "http://127.0.0.1:6969/api/ledger", "token": "t0k"}
    args = {"command": "env", "_ledger_scope": envelope}
    with mock.patch("app.mcp.client.logger.warning") as warn:
        out = v._validate_and_convert_arguments(args, _SHELL_SCHEMA)
    assert out["_ledger_scope"] == envelope
    assert out["command"] == "env"
    warned = " ".join(str(c.args) for c in warn.call_args_list)
    assert "_ledger_scope" not in warned
    assert "_ledger_scope" in MCPClient._KNOWN_ROUTING_KEYS
