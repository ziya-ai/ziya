"""
Tests for GET /servers/{name}/details reporting preflight diagnostics.

Before the change this endpoint 404'd whenever a server had no client, and the
frontend turned that 404 into `{logs: []}` — the "no logs available" dead end.
It must now return 200 with the stub's diagnostic so the GUI can render a
failure card.

Also pins the status-code contract: HTTPExceptions raised inside the handler's
try block must not be swallowed by its `except Exception` and re-raised as 500.
"""
import pytest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.mcp.client import MCPClient
from app.routes.mcp_routes import get_mcp_server_details


def _stub_client(name="ghost"):
    c = MCPClient({"name": name, "command": ["definitely-not-real"]})
    c.preflight_failure = {
        "code": "command_not_on_path",
        "summary": "Command not found on PATH: definitely-not-real",
        "detail": "... There are no logs because nothing ever ran.",
        "searched": ["/usr/bin", "/bin"],
        "hint": "Install it.",
    }
    c.startup_stage = "preflight"
    c.logs = ["ERROR: Command not found on PATH: definitely-not-real"]
    return c


def _manager(clients=None, configs=None, initialized=True):
    m = MagicMock()
    m.is_initialized = initialized
    m.clients = clients if clients is not None else {}
    m.server_configs = configs if configs is not None else {}
    return m


class TestDetailsReturnsDiagnostic:

    @pytest.mark.asyncio
    async def test_preflight_stub_returns_200_with_diagnostic(self):
        mgr = _manager(clients={"ghost": _stub_client()},
                       configs={"ghost": {}})
        with patch("app.routes.mcp_routes.get_mcp_manager", return_value=mgr):
            result = await get_mcp_server_details("ghost")

        assert result["preflight_failure"]["code"] == "command_not_on_path"
        assert result["startup_stage"] == "preflight"
        assert result["logs"], "REGRESSION: empty logs for never-started server"
        assert result["tools"] == []

    @pytest.mark.asyncio
    async def test_healthy_server_reports_no_failure(self):
        healthy = MCPClient({"name": "ok", "command": ["echo"]})
        healthy.startup_stage = "ready"
        mgr = _manager(clients={"ok": healthy}, configs={"ok": {}})
        with patch("app.routes.mcp_routes.get_mcp_manager", return_value=mgr):
            result = await get_mcp_server_details("ok")

        assert result["preflight_failure"] is None
        assert result["startup_stage"] == "ready"

    @pytest.mark.asyncio
    async def test_configured_but_clientless_server_is_404(self):
        """A name in server_configs with no client has no panel to populate.

        Config-stage rejections are removed from server_configs entirely and
        are explained by the config-findings panel, so there is no reachable
        state where a server has a details panel but no client. A 404 here is
        the contract, not a dead end.
        """
        mgr = _manager(clients={}, configs={"ghost": {}})
        with patch("app.routes.mcp_routes.get_mcp_manager", return_value=mgr):
            with pytest.raises(HTTPException) as ei:
                await get_mcp_server_details("ghost")
        assert ei.value.status_code == 404


class TestDetailsStatusCodes:
    """A 404 that arrives as a 500 is a debugging dead end of its own: the
    frontend cannot distinguish 'unknown server' from 'server error'."""

    @pytest.mark.asyncio
    async def test_unknown_server_is_404_not_500(self):
        mgr = _manager(clients={}, configs={})
        with patch("app.routes.mcp_routes.get_mcp_manager", return_value=mgr):
            with pytest.raises(HTTPException) as ei:
                await get_mcp_server_details("never-heard-of-it")

        assert ei.value.status_code == 404, (
            f"expected 404 for unknown server, got {ei.value.status_code} — "
            f"the handler's `except Exception` is swallowing HTTPException "
            f"and re-raising it as 500"
        )

    @pytest.mark.asyncio
    async def test_uninitialised_mcp_is_404_not_500(self):
        mgr = _manager(initialized=False)
        with patch("app.routes.mcp_routes.get_mcp_manager", return_value=mgr):
            with pytest.raises(HTTPException) as ei:
                await get_mcp_server_details("anything")

        assert ei.value.status_code == 404, (
            f"expected 404 when MCP is not initialized, got "
            f"{ei.value.status_code}"
        )
