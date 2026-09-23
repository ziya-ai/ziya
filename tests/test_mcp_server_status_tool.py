"""
Tests for the mcp_server_status self-inspection builtin tool.

Why this file exists.  The startup logs, startup_stage, and
preflight/startup failure diagnostics that the MCP Logs tab shows live only
in the running manager's in-memory MCPClient objects and were reachable
solely through the browser status route (app/routes/mcp_routes.py).  The
model had no accessor, so it could not answer "why did my MCP server fail
to start?" without asking the user to paste the panel.  mcp_server_status
closes that gap by reading the same manager the route reads.

These assert the seams that class of gap hides in:

  * REGISTRATION seam — the tool is discoverable through the builtin
    category registry and enabled by default (a tool that executes
    correctly but is never registered is invisible to the model, which is
    exactly the state before this change).
  * FIELD seam — the report carries the SAME diagnostic fields the status
    route returns (startup_stage / preflight_failure / startup_failure /
    logs), because those are the fields a user debugging a failed server
    needs; a report missing them is a report that cannot answer the
    question.
  * DEFAULT-ALL seam — calling with no arguments reports every configured
    server, including one that never produced a client, which is precisely
    the failed-to-start case being diagnosed.
"""

import json

import pytest

from app.mcp.tools.mcp_diagnostics import McpServerStatusTool
from app.mcp import builtin_tools


# ---------------------------------------------------------------------------
# Fakes: a manager shaped like MCPManager for the fields this tool reads.
# ---------------------------------------------------------------------------

class _FakeClient:
    def __init__(self, *, connected, stage, logs=None,
                 preflight_failure=None, startup_failure=None,
                 tools=0, resources=0, prompts=0):
        self.is_connected = connected
        self.startup_stage = stage
        self.logs = list(logs or [])
        self.preflight_failure = preflight_failure
        self.startup_failure = startup_failure
        self.tools = list(range(tools))
        self.resources = list(range(resources))
        self.prompts = list(range(prompts))


class _FakeManager:
    def __init__(self, *, initialized=True, server_configs=None,
                 clients=None, quarantined=None):
        self.is_initialized = initialized
        self.server_configs = server_configs or {}
        self.clients = clients or {}
        self._quarantined_servers = quarantined or set()


def _payload(result):
    """Pull the JSON dict out of a _text() tool result, or None if the
    result is a plain-text message rather than a JSON report."""
    text = result["content"][0]["text"]
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _run(tool, **kwargs):
    import asyncio
    return asyncio.run(tool.execute(**kwargs))


def _healthy_and_failed_manager():
    """A manager with one ready server, one that died at spawn, and one
    that was configured but never produced a client."""
    good = _FakeClient(
        connected=True, stage="ready",
        logs=["INFO: Generated command", "INFO: ready"], tools=4,
    )
    broken = _FakeClient(
        connected=False, stage="spawn",
        logs=["STDERR: ImportError: no module named foo",
              "ERROR: Server process exited during initialization"],
        startup_failure={"summary": "runtime missing",
                         "detail": "python3.13 not found",
                         "hint": "install python 3.13"},
    )
    return _FakeManager(
        server_configs={
            "good": {"builtin": False},
            "broken": {"builtin": False},
            "never_started": {"builtin": False},
        },
        clients={"good": good, "broken": broken},
        quarantined=set(),
    )


# ---------------------------------------------------------------------------
# Registration seam
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_category_present_and_enabled_by_default(self):
        assert "mcp_diagnostics" in builtin_tools.BUILTIN_TOOL_CATEGORIES
        cat = builtin_tools.BUILTIN_TOOL_CATEGORIES["mcp_diagnostics"]
        assert cat["enabled_by_default"] is True

    def test_getter_returns_the_tool_class(self):
        classes = builtin_tools.get_builtin_tools_for_category("mcp_diagnostics")
        assert McpServerStatusTool in classes

    def test_tool_is_in_the_enabled_builtin_set(self):
        # The end-to-end surface: get_enabled_builtin_tools is what the agent
        # actually loads.  Without a registry wiring, the tool could import
        # and execute perfectly yet never reach the model.
        builtin_tools.invalidate_category_cache()
        names = {t.name for t in builtin_tools.get_enabled_builtin_tools()}
        assert "mcp_server_status" in names

    def test_direct_and_read_only_in_description(self):
        tool = McpServerStatusTool()
        assert tool.description.startswith("[DIRECT]")
        assert "read-only" in tool.description.lower()


# ---------------------------------------------------------------------------
# Field seam — the report mirrors the status route's diagnostic fields.
# ---------------------------------------------------------------------------

class TestReportFields:
    def _report_for(self, monkeypatch, name):
        mgr = _healthy_and_failed_manager()
        monkeypatch.setattr(
            "app.mcp.manager.get_mcp_manager", lambda: mgr, raising=True,
        )
        payload = _payload(_run(McpServerStatusTool(), server_name=name))
        assert payload is not None
        assert payload["count"] == 1
        return payload["servers"][0]

    def test_failed_server_surfaces_stage_and_failure(self, monkeypatch):
        r = self._report_for(monkeypatch, "broken")
        assert r["connected"] is False
        assert r["startup_stage"] == "spawn"
        assert r["startup_failure"]["summary"] == "runtime missing"
        # The log tail that actually explains the failure must come through.
        assert any("ImportError" in line for line in r["logs"])

    def test_healthy_server_reports_connected_and_counts(self, monkeypatch):
        r = self._report_for(monkeypatch, "good")
        assert r["connected"] is True
        assert r["startup_stage"] == "ready"
        assert r["tools"] == 4

    def test_no_client_server_is_flagged_not_omitted(self, monkeypatch):
        # A server configured but never spawned is the failed-to-start case;
        # it must appear with an explanation, not silently vanish.
        r = self._report_for(monkeypatch, "never_started")
        assert r["status"] == "no_client"
        assert r["connected"] is False


# ---------------------------------------------------------------------------
# Default-all seam
# ---------------------------------------------------------------------------

class TestDefaultAll:
    def test_no_args_reports_every_configured_server(self, monkeypatch):
        mgr = _healthy_and_failed_manager()
        monkeypatch.setattr(
            "app.mcp.manager.get_mcp_manager", lambda: mgr, raising=True,
        )
        payload = _payload(_run(McpServerStatusTool()))
        assert payload is not None
        names = {s["name"] for s in payload["servers"]}
        assert names == {"good", "broken", "never_started"}
        assert payload["count"] == 3

    def test_log_lines_tail_is_bounded_but_reports_total(self, monkeypatch):
        client = _FakeClient(
            connected=True, stage="ready",
            logs=[f"line {i}" for i in range(200)],
        )
        mgr = _FakeManager(
            server_configs={"chatty": {}}, clients={"chatty": client},
        )
        monkeypatch.setattr(
            "app.mcp.manager.get_mcp_manager", lambda: mgr, raising=True,
        )
        payload = _payload(_run(McpServerStatusTool(), log_lines=10))
        r = payload["servers"][0]
        assert r["log_lines_total"] == 200
        assert r["log_lines_returned"] == 10
        assert r["logs"][-1] == "line 199"

    def test_log_lines_zero_returns_whole_buffer(self, monkeypatch):
        client = _FakeClient(
            connected=True, stage="ready",
            logs=[f"line {i}" for i in range(200)],
        )
        mgr = _FakeManager(
            server_configs={"chatty": {}}, clients={"chatty": client},
        )
        monkeypatch.setattr(
            "app.mcp.manager.get_mcp_manager", lambda: mgr, raising=True,
        )
        payload = _payload(_run(McpServerStatusTool(), log_lines=0))
        r = payload["servers"][0]
        assert r["log_lines_returned"] == 200


# ---------------------------------------------------------------------------
# Degenerate states
# ---------------------------------------------------------------------------

class TestDegenerate:
    def test_uninitialized_manager_reports_gracefully(self, monkeypatch):
        mgr = _FakeManager(initialized=False)
        monkeypatch.setattr(
            "app.mcp.manager.get_mcp_manager", lambda: mgr, raising=True,
        )
        result = _run(McpServerStatusTool())
        assert _payload(result) is None  # plain-text message, not a report
        assert "not initialized" in result["content"][0]["text"].lower()

    def test_unknown_server_name_lists_known(self, monkeypatch):
        mgr = _healthy_and_failed_manager()
        monkeypatch.setattr(
            "app.mcp.manager.get_mcp_manager", lambda: mgr, raising=True,
        )
        result = _run(McpServerStatusTool(), server_name="nope")
        text = result["content"][0]["text"]
        assert "no mcp server named 'nope'" in text.lower()
        assert "good" in text and "broken" in text

    def test_negative_log_lines_rejected(self, monkeypatch):
        mgr = _healthy_and_failed_manager()
        monkeypatch.setattr(
            "app.mcp.manager.get_mcp_manager", lambda: mgr, raising=True,
        )
        result = _run(McpServerStatusTool(), log_lines=-1)
        assert "must be 0 or greater" in result["content"][0]["text"]
