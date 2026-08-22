"""
Tests for MCPClient.startup_stage progression.

The stage is the answer to "whose problem is this?" — a server stuck at
'preflight' is the user's machine, one stuck at 'spawn' is the server's own
startup, one stuck at 'handshake' answered initialize and then stalled listing
its capabilities. Those need different fixes, so the stage must actually
advance rather than being set once and left.

An earlier version assigned only 'config', 'spawn' and 'preflight', so a fully
connected server reported 'spawn' and a stall during capability loading was
indistinguishable from a process that died at launch.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.mcp.client import MCPClient


ORDER = ["config", "preflight", "spawn", "handshake", "ready"]


class TestStageProgression:

    def test_initial_stage_is_config(self):
        c = MCPClient({"name": "srv", "command": ["echo"]})
        assert c.startup_stage == "config"

    def test_all_documented_stages_are_reachable(self):
        """Every stage named in the docs must be assigned somewhere in the
        source. A documented stage that is never set makes the table a lie."""
        import inspect
        import app.mcp.client as client_mod
        import app.mcp.manager as manager_mod

        src = inspect.getsource(client_mod) + inspect.getsource(manager_mod)
        for stage in ORDER:
            assert f'"{stage}"' in src, (
                f"stage '{stage}' is documented but never assigned — either "
                f"assign it or remove it from the docs table"
            )

    @pytest.mark.asyncio
    async def test_preflight_stub_stays_at_preflight(self):
        """A stub must not advance: connect() short-circuits before spawn."""
        c = MCPClient({"name": "srv", "command": ["nope"]})
        c.preflight_failure = {"code": "command_not_on_path"}
        c.startup_stage = "preflight"

        with patch("asyncio.create_subprocess_exec", new=AsyncMock()) as spawn:
            await c.connect()

        spawn.assert_not_called()
        assert c.startup_stage == "preflight", (
            "a preflight-failed client advanced past preflight, which would "
            "report a server that never ran as having been launched"
        )

    @pytest.mark.asyncio
    async def test_stage_advances_to_spawn_on_launch_attempt(self):
        """Reaching spawn means a process was actually created, which is what
        makes an empty Logs tab meaningful vs. expected."""
        c = MCPClient({"name": "srv", "command": ["definitely-not-real-xyz"]})

        # Let the real launch path run and fail; the stage must have moved off
        # 'config' regardless of the failure.
        await c.connect()

        assert c.startup_stage != "config", (
            "stage never left 'config' despite a launch attempt"
        )

    def test_handshake_precedes_ready_in_source(self):
        """'ready' must be set after capability loading, not alongside
        'handshake' — otherwise a stall in tools/list reports as ready."""
        import inspect
        import app.mcp.client as client_mod

        src = inspect.getsource(client_mod)
        hs = src.index('self.startup_stage = "handshake"')
        rd = src.index('self.startup_stage = "ready"')
        cap = src.index("_load_server_capabilities()")

        assert hs < cap < rd, (
            "expected handshake -> _load_server_capabilities() -> ready; a "
            "'ready' set before capability loading would hide a stall there"
        )


class TestStageIsReportedToTheGui:

    @pytest.mark.asyncio
    async def test_details_route_reports_current_stage(self):
        from app.routes.mcp_routes import get_mcp_server_details

        c = MCPClient({"name": "srv", "command": ["echo"]})
        c.startup_stage = "handshake"

        mgr = MagicMock()
        mgr.is_initialized = True
        mgr.clients = {"srv": c}
        mgr.server_configs = {"srv": {}}

        with patch("app.routes.mcp_routes.get_mcp_manager", return_value=mgr):
            result = await get_mcp_server_details("srv")

        assert result["startup_stage"] == "handshake"
