"""Cross-conversation shell isolation: workspace-scoped instance keying.

Regression coverage for the cross-conversation blocking bug. The shell MCP
server's request loop is strictly serial (blocking ``sys.stdin.readline()``
plus blocking ``proc.communicate()``), so two conversations sharing ONE
subprocess serialize: a slow command in conversation A stalls B.

Isolation is supposed to come from ``_get_or_create_workspace_client``, which
keys instances ``f"{workspace_path}::{session_id}"``. But when ``session_id``
is None that expression COLLAPSES to the bare workspace path and every
conversation resolves to the same client. ``session_id`` is the conversation
id, which several dispatch paths never injected into ``arguments`` -- so it
arrived None and isolation silently did not happen.

These tests assert the two halves of the fix:
  1. the instance key de-collapses when a conversation id is present, and
     concurrent calls from different conversations do NOT serialize;
  2. ``MCPManager.call_tool`` resolves the conversation id from the
     request-scoped ContextVar when the caller omitted it from arguments
     (this is what covers the text-fence shell path in
     streaming_tool_executor and the anthropic/openai/bedrock direct
     provider wrappers, none of which inject it).

Two negative controls reproduce the original bug -- one by forcing
session_id=None at the keying site, one by neutralizing the ContextVar
fallback itself -- proving the assertions are load-bearing rather than
vacuously true.

Known remaining limitation, asserted here so a future change to it is
deliberate rather than accidental: calls within a SINGLE conversation still
share one subprocess and therefore still serialize (see
test_same_conversation_reuses_one_instance). Fixing that requires making the
shell server itself concurrent, which in turn requires converting
ShellWriteChecker._task_scope from an instance attribute to a ContextVar --
under concurrency one conversation's write grant could otherwise be live
while another conversation's command is validated.
"""
import asyncio
import time

import pytest

from app.context import set_conversation_id, get_conversation_id_or_none
from app.mcp.manager import MCPManager
import app.mcp.manager as mgr_mod


class _FakeTool:
    """Minimal stand-in for MCPTool -- only .name is consulted by routing."""

    def __init__(self, name):
        self.name = name
        self.description = ""
        self.inputSchema = {}


class SerialFakeClient:
    """Stands in for MCPClient over a serial shell subprocess.

    Holds a per-instance lock for the duration of every call, mirroring the
    real server's one-request-at-a-time loop. Two conversations sharing an
    instance therefore serialize; separate instances run concurrently. This
    is what lets the tests below measure real concurrency instead of merely
    inspecting the instance keys.
    """

    def __init__(self):
        self.is_connected = True
        self.tools = []
        self.server_config = {"name": "shell"}
        self._lock = asyncio.Lock()
        self.calls = []

    def _is_process_healthy(self):
        return True

    async def connect(self):
        return True

    async def disconnect(self):
        self.is_connected = False

    async def call_tool(self, tool_name, arguments):
        async with self._lock:  # serial, like the real request loop
            self.calls.append((tool_name, arguments))
            await asyncio.sleep(0.05)
            return {"content": [{"type": "text", "text": "ok"}]}


def _manager_with_fake_shell(monkeypatch, force_session_none=False):
    """MCPManager wired to spawn SerialFakeClient instances, no real I/O."""
    m = MCPManager.__new__(MCPManager)
    # A connected client advertising the tool is required for call_tool to
    # resolve target_server_name; without it routing never reaches the
    # workspace-scoped branch at all.
    registry = SerialFakeClient()
    registry.tools = [_FakeTool("run_shell_command")]
    m.clients = {"shell": registry}
    m.server_configs = {"shell": {"command": "x", "enabled": True,
                                  "workspace_scoped": True, "builtin": True}}
    m.workspace_scoped_clients = {}
    m._workspace_instance_last_used = {}
    m._workspace_instance_timeout = 300
    m._quarantined_servers = set()
    m._tools_cache = None
    m._tools_cache_timestamp = 0
    m._recent_tool_calls = {}
    m._max_recent_calls = 10
    m._loop_detection_window = 60
    m._turn_tool_counts = {}
    m.tool_timeout = 30.0
    m.spawned = []

    async def fake_get_or_create(server_name, workspace_path, session_id=None):
        if force_session_none:
            session_id = None  # negative control: reproduce the old bug
        key = f"{workspace_path}::{session_id}" if session_id else workspace_path
        bucket = m.workspace_scoped_clients.setdefault(server_name, {})
        if key not in bucket:
            bucket[key] = SerialFakeClient()
            m.spawned.append(key)
        return bucket[key]

    monkeypatch.setattr(m, "_get_or_create_workspace_client", fake_get_or_create)
    monkeypatch.setattr(m, "_turn_limit", lambda: 0)  # disable turn ceiling
    monkeypatch.setattr(m, "_is_repetitive_call", lambda *a, **k: False)
    monkeypatch.setattr(m, "_normalize_tool_parameters", lambda n, a: a)
    monkeypatch.setattr(m, "_coerce_argument_types", lambda n, a: a)
    return m


class TestInstanceKeyCollapse:
    """The keying expression itself, isolated from dispatch."""

    def test_key_collapses_when_session_id_is_none(self):
        ws = "/proj"
        session_a = session_b = None
        key_a = f"{ws}::{session_a}" if session_a else ws
        key_b = f"{ws}::{session_b}" if session_b else ws
        assert key_a == key_b == ws, "None session_id must collapse to bare path"

    def test_key_separates_per_conversation(self):
        ws = "/proj"
        key_a = f"{ws}::conv-A" if "conv-A" else ws
        key_b = f"{ws}::conv-B" if "conv-B" else ws
        assert key_a != key_b


class TestCrossConversationConcurrency:
    @pytest.mark.asyncio
    async def test_distinct_conversations_do_not_serialize(self, monkeypatch):
        m = _manager_with_fake_shell(monkeypatch)

        async def call(conv):
            return await m.call_tool(
                "run_shell_command",
                {"command": "sleep", "_workspace_path": "/proj",
                 "conversation_id": conv},
            )

        start = time.monotonic()
        await asyncio.gather(call("conv-A"), call("conv-B"))
        elapsed = time.monotonic() - start

        assert len(m.spawned) == 2, f"expected 2 subprocesses, got {m.spawned}"
        # Two 0.05s calls in parallel finish well under the 0.10s serial floor.
        assert elapsed < 0.09, f"calls serialized ({elapsed:.3f}s)"

    @pytest.mark.asyncio
    async def test_negative_control_shared_instance_serializes(self, monkeypatch):
        """With session_id forced to None the old bug reappears -- proving the
        assertion above is load-bearing and not trivially satisfied."""
        m = _manager_with_fake_shell(monkeypatch, force_session_none=True)

        async def call(conv):
            return await m.call_tool(
                "run_shell_command",
                {"command": "sleep", "_workspace_path": "/proj",
                 "conversation_id": conv},
            )

        start = time.monotonic()
        await asyncio.gather(call("conv-A"), call("conv-B"))
        elapsed = time.monotonic() - start

        assert len(m.spawned) == 1, "collapsed key must yield ONE shared client"
        assert elapsed >= 0.09, f"expected serialization, got {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_same_conversation_reuses_one_instance(self, monkeypatch):
        """Intra-conversation calls still share a subprocess (still serial).
        Documents the remaining known limitation for swarm delegates."""
        m = _manager_with_fake_shell(monkeypatch)
        for _ in range(2):
            await m.call_tool(
                "run_shell_command",
                {"command": "x", "_workspace_path": "/proj",
                 "conversation_id": "conv-A"},
            )
        assert len(m.spawned) == 1


class TestContextVarFallback:
    """Callers that never inject conversation_id (text-fence shell dispatch,
    anthropic/openai/bedrock direct wrappers) must still get isolation."""

    @pytest.mark.asyncio
    async def test_contextvar_supplies_missing_conversation_id(self, monkeypatch):
        m = _manager_with_fake_shell(monkeypatch)

        async def call_without_id(conv):
            set_conversation_id(conv)
            assert get_conversation_id_or_none() == conv
            # Note: no conversation_id key -- exactly what
            # _execute_fake_tool and the direct wrappers pass.
            return await m.call_tool(
                "run_shell_command",
                {"command": "sleep", "_workspace_path": "/proj"},
            )

        # Separate tasks so each gets its own ContextVar copy.
        await asyncio.gather(
            asyncio.create_task(call_without_id("conv-A")),
            asyncio.create_task(call_without_id("conv-B")),
        )
        assert len(m.spawned) == 2, (
            f"ContextVar fallback did not isolate; spawned={m.spawned}"
        )

    @pytest.mark.asyncio
    async def test_explicit_argument_wins_over_contextvar(self, monkeypatch):
        m = _manager_with_fake_shell(monkeypatch)
        set_conversation_id("ctx-conv")
        await m.call_tool(
            "run_shell_command",
            {"command": "x", "_workspace_path": "/proj",
             "conversation_id": "explicit-conv"},
        )
        assert m.spawned == ["/proj::explicit-conv"]

    @pytest.mark.asyncio
    async def test_without_fallback_isolation_collapses(self, monkeypatch):
        """Mutation check: neutralize the fallback -> both conversations share
        ONE subprocess. This is the original bug. If this does NOT collapse,
        the fallback is not what provides isolation for id-less callers."""
        monkeypatch.setattr(mgr_mod, "get_conversation_id_or_none", lambda: None)
        m = _manager_with_fake_shell(monkeypatch)

        async def call_without_id(conv):
            set_conversation_id(conv)
            return await m.call_tool(
                "run_shell_command",
                {"command": "sleep", "_workspace_path": "/proj"},
            )

        await asyncio.gather(
            asyncio.create_task(call_without_id("conv-A")),
            asyncio.create_task(call_without_id("conv-B")),
        )
        assert len(m.spawned) == 1, (
            f"expected the pre-fix collapse to ONE shared client, got {m.spawned}"
        )
        assert m.spawned == ["/proj"], f"expected bare-path key, got {m.spawned}"


class TestRoutingMetadataStripped:
    """The routing keys must never reach the MCP server."""

    @pytest.mark.asyncio
    async def test_metadata_not_forwarded_to_server(self, monkeypatch):
        m = _manager_with_fake_shell(monkeypatch)
        await m.call_tool(
            "run_shell_command",
            {"command": "x", "_workspace_path": "/proj",
             "conversation_id": "conv-A"},
        )
        client = m.workspace_scoped_clients["shell"]["/proj::conv-A"]
        _, forwarded = client.calls[0]
        for key in ("conversation_id", "_workspace_path"):
            assert key not in forwarded, f"{key} leaked to the MCP server"
        assert forwarded["command"] == "x"
