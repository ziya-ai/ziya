"""
Regression tests: loop guards must fire on the WORKSPACE-SCOPED call_tool path.

Root cause of the CHANGELOG find-loop (session cli_20260716_000737_41665):
call_tool() has an early branch for workspace-scoped servers that executed the
tool and `return`ed BEFORE reaching the per-turn circuit breaker
(_exceeds_turn_ceiling) and the repetitive-call guard (_is_repetitive_call)
further down the method. Shell is workspace-scoped, so every shell command took
that early return and bypassed both guards entirely — a model stuck in a
hallucinated-tool-name loop re-issued the identical `find ... CHANGELOG*`
command 13+ times with nothing stopping it.

The prior test suite only unit-tested `_exceeds_turn_ceiling` in isolation
(tests/test_session_tool_ceiling.py). That passes whether or not call_tool()
actually consults the helper on a given branch, which is precisely why the
bypass shipped undetected. These tests drive call_tool() end-to-end through the
workspace-scoped branch and assert the guards are reached.

Each test is written so it would FAIL against the pre-fix code (guards
bypassed → every call returns the success sentinel, never a block).
"""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.mcp.manager import MCPManager, DEFAULT_TOOL_TIMEOUT


_SUCCESS = {"content": [{"type": "text", "text": "ok"}]}
_WS_SERVER = "workspace-shell"  # not literally "shell" → skips _task_scope re-attach
_WS_PATH = "/tmp/ws-under-test"
_CMD = 'find /Users/dcohn/workplace/ziya -maxdepth 2 -iname "CHANGELOG*"'


def _make_manager(**overrides) -> MCPManager:
    """Minimal MCPManager built via __new__ (no async initialize), wired for
    the workspace-scoped call_tool path."""
    mgr = MCPManager.__new__(MCPManager)
    mgr.config_path = None
    mgr.clients = {}
    mgr.workspace_scoped_clients = {}
    mgr.server_configs = {}
    mgr.is_initialized = True
    mgr._quarantined_servers = set()
    # Repetitive-call guard state
    mgr._recent_tool_calls = {}
    mgr._max_recent_calls = 10
    mgr._loop_detection_window = 60
    # Per-turn circuit breaker state
    mgr._turn_tool_counts = {}
    mgr.tool_timeout = float(overrides.get("tool_timeout", DEFAULT_TOOL_TIMEOUT))
    return mgr


def _wire_workspace_path(mgr):
    """Patch out everything on the workspace-scoped branch except the two
    guards under test, so a call reaches (and is decided by) the guards."""
    ws_client = MagicMock()
    ws_client.is_connected = True

    call_timeout = AsyncMock(return_value=_SUCCESS)
    get_or_create = AsyncMock(return_value=ws_client)

    patches = [
        patch("app.mcp.manager.get_dynamic_loader"),
        patch("app.mcp.permissions.get_permissions_manager"),
        patch.object(mgr, "_is_workspace_scoped", return_value=True),
        patch.object(mgr, "_get_or_create_workspace_client", get_or_create),
        patch.object(mgr, "_call_tool_with_timeout", call_timeout),
        # Isolate the guards from schema coercion — pass args through unchanged.
        patch.object(mgr, "_normalize_tool_parameters", side_effect=lambda name, args: args),
        patch.object(mgr, "_coerce_argument_types", side_effect=lambda name, args: args),
    ]
    started = [p.start() for p in patches]
    # get_dynamic_loader().get_tool() must return None (not a dynamic tool)
    started[0].return_value.get_tool.return_value = None
    # permissions default to enabled
    started[1].return_value.get_permissions.return_value = {
        "defaults": {"tool": "enabled"}, "servers": {},
    }
    return patches, call_timeout


async def _call(mgr, *, conversation_id, command=_CMD):
    return await mgr.call_tool(
        "mcp_run_shell_command",
        {
            "command": command,
            "_workspace_path": _WS_PATH,
            "conversation_id": conversation_id,
        },
        server_name=_WS_SERVER,
    )


def _is_block(result) -> bool:
    return isinstance(result, dict) and result.get("error") is True and result.get("code") == -32001


class TestWorkspaceScopedRepetitiveGuard:
    """The repetitive-call guard must apply on the workspace-scoped path."""

    @pytest.mark.asyncio
    async def test_identical_command_eventually_blocked(self):
        # Disable the turn ceiling so this test isolates the repetitive guard.
        with patch.dict(os.environ, {"ZIYA_MAX_TOOLS_PER_TURN": "0"}):
            mgr = _make_manager()
            patches, call_timeout = _wire_workspace_path(mgr)
            try:
                results = [await _call(mgr, conversation_id="conv-loop") for _ in range(6)]
            finally:
                for p in patches:
                    p.stop()

        # Calls 1–5 execute; the 6th identical call is blocked (>= 5).
        assert all(not _is_block(r) for r in results[:5]), "first 5 identical calls should run"
        assert _is_block(results[5]), (
            "6th identical command must be blocked by the repetitive guard — "
            "pre-fix, the workspace-scoped branch bypassed this guard entirely"
        )
        # The blocked call must NOT have reached execution.
        assert call_timeout.await_count == 5, (
            "the blocked call must short-circuit before _call_tool_with_timeout"
        )

    @pytest.mark.asyncio
    async def test_distinct_commands_not_blocked_by_repetition(self):
        """Varying the command avoids the repetition guard (no false positives),
        confirming the guard keys on the command signature, not mere volume."""
        with patch.dict(os.environ, {"ZIYA_MAX_TOOLS_PER_TURN": "0"}):
            mgr = _make_manager()
            patches, call_timeout = _wire_workspace_path(mgr)
            try:
                results = [
                    await _call(mgr, conversation_id="conv-distinct", command=f"ls dir{i}")
                    for i in range(8)
                ]
            finally:
                for p in patches:
                    p.stop()
        assert all(not _is_block(r) for r in results)
        assert call_timeout.await_count == 8


class TestWorkspaceScopedTurnCeiling:
    """The per-turn circuit breaker must apply on the workspace-scoped path."""

    @pytest.mark.asyncio
    async def test_turn_ceiling_enforced(self):
        # Ceiling of 3: the 4th call in the turn is refused. Distinct commands
        # so ONLY the ceiling (not the repetitive guard) can be the blocker.
        with patch.dict(os.environ, {"ZIYA_MAX_TOOLS_PER_TURN": "3"}):
            mgr = _make_manager()
            patches, call_timeout = _wire_workspace_path(mgr)
            try:
                results = [
                    await _call(mgr, conversation_id="conv-ceiling", command=f"echo {i}")
                    for i in range(4)
                ]
            finally:
                for p in patches:
                    p.stop()

        assert all(not _is_block(r) for r in results[:3]), "first 3 calls under the ceiling should run"
        assert _is_block(results[3]), (
            "4th call must be refused by the per-turn ceiling — pre-fix, the "
            "workspace-scoped branch returned before this check"
        )
        assert "ceiling" in results[3]["message"].lower()
        assert call_timeout.await_count == 3

    @pytest.mark.asyncio
    async def test_reset_restores_budget_next_turn(self):
        """reset_turn_tool_count must let a fresh turn use the workspace path
        again — the ceiling bounds a burst, never a permanent lockout."""
        with patch.dict(os.environ, {"ZIYA_MAX_TOOLS_PER_TURN": "2"}):
            mgr = _make_manager()
            patches, call_timeout = _wire_workspace_path(mgr)
            try:
                r1 = await _call(mgr, conversation_id="conv-reset", command="echo a")
                r2 = await _call(mgr, conversation_id="conv-reset", command="echo b")
                r3 = await _call(mgr, conversation_id="conv-reset", command="echo c")  # over ceiling
                mgr.reset_turn_tool_count("conv-reset")
                r4 = await _call(mgr, conversation_id="conv-reset", command="echo d")  # fresh turn
            finally:
                for p in patches:
                    p.stop()
        assert not _is_block(r1) and not _is_block(r2)
        assert _is_block(r3), "3rd call in the turn exceeds ceiling of 2"
        assert not _is_block(r4), "after reset the next turn regains its budget"


class TestWorkspaceScopedGuardReachability:
    """Directly assert the branch reaches the guards (the specific defect)."""

    @pytest.mark.asyncio
    async def test_repetitive_guard_is_consulted_on_workspace_path(self):
        """Spy on _is_repetitive_call to prove call_tool() actually invokes it
        on the workspace-scoped branch. Pre-fix this spy would see 0 calls."""
        with patch.dict(os.environ, {"ZIYA_MAX_TOOLS_PER_TURN": "0"}):
            mgr = _make_manager()
            patches, _ = _wire_workspace_path(mgr)
            spy = MagicMock(wraps=mgr._is_repetitive_call)
            with patch.object(mgr, "_is_repetitive_call", spy):
                try:
                    await _call(mgr, conversation_id="conv-spy")
                finally:
                    for p in patches:
                        p.stop()
        assert spy.call_count == 1, (
            "_is_repetitive_call must be reached on the workspace-scoped path"
        )
