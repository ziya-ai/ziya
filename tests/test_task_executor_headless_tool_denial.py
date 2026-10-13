"""Headless task runs withhold builtins that would wait for a human.

The sweep run cli-695c6e36c9f8 (2026-10-12) sat four hours in
``ConsentLedger.await_answer`` on a ``git commit`` issued through the
``git`` builtin: the tool declares ``consent_default = "ask"``, the CLI
runner renders no consent prompt, and the ledger TTL is 24 h.  The fix is
not to answer the prompt but never to offer the tool: a run with no human
on it (``ExecutionContext.headless`` from ``ziya task``, or a scheduler
fire persisted as ``launch_context="headless"``) drops every ``ask``-mode
builtin from the payload and tells the model so in ``decisions``.

Two seams, both asserted end-to-end:

* ``unattended_denied_tools`` picks exactly the ``ask`` tools, honouring a
  project's explicit ``autonomous`` mode.
* ``execute_task_block`` hands the names to ``stream_with_tools`` as
  ``tool_denylist`` — the executor is where the payload is built, so a
  caller-side filter alone would be the same dead code the allowlist once
  was — and does NOT when the run is interactive.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agents import task_executor
from app.models.task_card import Block, TaskScope
from app.utils.task_tool_floor import unattended_denied_tools


def _tool(name, consent_default=None, wrapped=True):
    inst = SimpleNamespace(name=name)
    if consent_default is not None:
        inst.consent_default = consent_default
    if wrapped:
        return SimpleNamespace(name=name, tool_instance=inst)
    return inst


class TestUnattendedDeniedTools:
    def test_only_ask_tools_are_denied(self):
        tools = [
            _tool("git", "ask"),
            _tool("memory_delete", "ask", wrapped=False),
            _tool("file_read"),
            _tool("run_shell_command", "off"),
        ]
        assert unattended_denied_tools(tools, None) == ["git", "memory_delete"]

    def test_project_autonomous_mode_keeps_the_tool(self):
        with patch("app.utils.project_tool_modes.mode_for",
                   side_effect=lambda n, r: "autonomous" if n == "git" else None):
            denied = unattended_denied_tools(
                [_tool("git", "ask"), _tool("memory_delete", "ask")], "/p")
        assert denied == ["memory_delete"]

    def test_unreadable_tool_is_not_denied(self):
        class Bad:
            name = "weird"

            @property
            def consent_default(self):
                raise RuntimeError("no")
        assert unattended_denied_tools([Bad()], None) == []


# ── execute_task_block seam ───────────────────────────────────────────

class _CapturingExecutor:
    """Records the kwargs stream_with_tools received."""
    calls = []

    def __init__(self, *a, **kw):
        pass

    async def stream_with_tools(self, messages, tools=None, **kwargs):
        type(self).calls.append(kwargs)
        yield {"type": "text", "content": "done"}
        yield {"type": "stream_end"}


@pytest.fixture
def patched():
    _CapturingExecutor.calls = []
    registry = [_tool("git", "ask"), _tool("file_read"), _tool("emit_artifact")]
    with patch("app.streaming_tool_executor.StreamingToolExecutor", _CapturingExecutor), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1", "aws_profile": "x",
                             "current_model": "m"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=registry):
        yield _CapturingExecutor


def _task(**scope_kwargs) -> Block:
    return Block(
        block_type="task", id="task-1", name="T", instructions="do it",
        scope=TaskScope(**scope_kwargs) if scope_kwargs else None,
    )


class TestHeadlessSeam:
    @pytest.mark.asyncio
    async def test_headless_run_sends_denylist_and_records_decision(self, patched):
        art = await task_executor.execute_task_block(_task(), run_id="r1", headless=True)
        assert patched.calls, "stream_with_tools was not called"
        assert patched.calls[0].get("tool_denylist") == ["git"]
        assert any("withheld" in d and "git" in d for d in art.decisions), art.decisions

    @pytest.mark.asyncio
    async def test_scope_grant_does_not_restore_a_denied_tool(self, patched):
        # Naming the tool in scope.tools names a tool, not an approver.
        await task_executor.execute_task_block(
            _task(tools=["git", "file_read"]), run_id="r2", headless=True)
        kw = patched.calls[0]
        assert kw.get("tool_denylist") == ["git"]
        assert "git" in (kw.get("tool_allowlist") or [])  # allowlist still passed...
        # ...but the executor applies the denylist first (see
        # StreamingToolExecutor._load_and_prepare_tools), so this pairing is
        # the contract the executor-side test below pins.

    @pytest.mark.asyncio
    async def test_interactive_run_sends_no_denylist(self, patched):
        art = await task_executor.execute_task_block(_task(), run_id="r3")
        assert patched.calls[0].get("tool_denylist") is None
        assert not any("withheld" in d for d in art.decisions)

    @pytest.mark.asyncio
    async def test_persisted_headless_launch_context_is_detected(self, patched):
        # A scheduler fire: nothing passes headless=True, but the run record
        # says so.  project_id makes execute_task_block open storage.
        run = SimpleNamespace(launch_context="headless")
        store = SimpleNamespace(get=lambda rid: run, record_activity=lambda *a, **k: None)
        with patch("app.storage.task_runs.TaskRunStorage", return_value=store), \
             patch("app.utils.paths.get_project_dir", return_value="/tmp/x"):
            await task_executor.execute_task_block(
                _task(), run_id="r4", project_id="p1")
        assert patched.calls[0].get("tool_denylist") == ["git"]


class TestProjectModeEnabledToolIsStillDenied:
    """The failure after the first fix: ``git`` is NOT in
    create_secure_mcp_tools(); this project's ``toolModes: {git: gated}``
    adds it in project_tool_modes.apply_to_tools, which runs AFTER the
    caller computed its denylist from the bare registry.  Run
    cli-3e094857dc39 parked on ``git commit`` with an empty denylist."""

    def _modes(self, name, root):
        # modes_for signature: (project_root) -> {tool: mode}
        return {"git": "gated"}

    @pytest.mark.asyncio
    async def test_task_executor_names_the_project_mode_tool(self):
        _CapturingExecutor.calls = []
        registry = [_tool("file_read"), _tool("emit_artifact")]  # no git
        with patch("app.streaming_tool_executor.StreamingToolExecutor", _CapturingExecutor), \
             patch("app.agents.models.ModelManager.get_state",
                   return_value={"aws_region": "us-east-1", "aws_profile": "x"}), \
             patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=registry), \
             patch("app.utils.project_tool_modes.modes_for", return_value={"git": "gated"}):
            art = await task_executor.execute_task_block(
                _task(), run_id="r5", project_root="/p", headless=True)
        kw = _CapturingExecutor.calls[0]
        assert kw.get("tool_denylist") == ["git"], kw.get("tool_denylist")
        assert kw.get("unattended") is True
        assert any("withheld" in d and "git" in d for d in art.decisions), art.decisions

    @pytest.mark.asyncio
    async def test_executor_denies_after_its_own_project_mode_step(self):
        """Even with NO names from the caller, ``unattended`` makes the
        executor drop the tool project modes just added."""
        from app.streaming_tool_executor import StreamingToolExecutor
        ex = StreamingToolExecutor.__new__(StreamingToolExecutor)
        registry = [_tool("file_read"), _tool("emit_artifact")]  # no git
        mgr = SimpleNamespace(is_initialized=True)
        with patch("app.mcp.manager.get_mcp_manager", return_value=mgr), \
             patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=registry), \
             patch("app.utils.project_tool_modes.modes_for", return_value={"git": "gated"}), \
             patch("app.utils.bench_prompt.withheld_mcp_for_root", return_value=set()), \
             patch("app.utils.tool_gating.filter_tools_by_session",
                   side_effect=lambda tools, sig, keep=None: (tools, [])), \
             patch.object(StreamingToolExecutor, "_convert_tools_for_provider",
                          lambda self, tools, *a, **k: ([], set()), create=True):
            with_flag = await ex._load_and_prepare_tools(
                tool_denylist=None, unattended=True, project_root="/p", conversation_id="c")
            without_flag = await ex._load_and_prepare_tools(
                tool_denylist=None, unattended=False, project_root="/p", conversation_id="c")
        names_with = {getattr(t, "name", "") for t in with_flag[0]}
        names_without = {getattr(t, "name", "") for t in without_flag[0]}
        # Positive: project modes really did add git (so the denial has work to do).
        assert "git" in names_without
        assert "git" not in names_with
        assert "file_read" in names_with


class TestExecutorAppliesDenylist:
    """The payload side: _load_and_prepare_tools drops the names even when
    the allowlist names them."""

    @pytest.mark.asyncio
    async def test_denylist_removes_tool_before_allowlist(self):
        from app.streaming_tool_executor import StreamingToolExecutor
        ex = StreamingToolExecutor.__new__(StreamingToolExecutor)
        registry = [_tool("git", "ask"), _tool("file_read"), _tool("emit_artifact")]
        mgr = SimpleNamespace(is_initialized=True)
        with patch("app.mcp.manager.get_mcp_manager", return_value=mgr), \
             patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=registry), \
             patch("app.utils.project_tool_modes.apply_to_tools",
                   side_effect=lambda tools, root, wrap=None: (tools, [], [])), \
             patch("app.utils.bench_prompt.withheld_mcp_for_root", return_value=set()), \
             patch("app.utils.tool_gating.filter_tools_by_session",
                   side_effect=lambda tools, sig, keep=None: (tools, [])), \
             patch.object(StreamingToolExecutor, "_convert_tools_for_provider",
                          lambda self, tools, *a, **k: ([], set()), create=True):
            try:
                result = await ex._load_and_prepare_tools(
                    tool_allowlist=["git", "file_read"], tool_denylist=["git"],
                    project_root="/p", conversation_id="c")
            except TypeError as e:
                pytest.fail(f"tool_denylist not accepted by _load_and_prepare_tools: {e}")
            except Exception:
                # Downstream schema conversion may need a richer fake; the
                # contract under test is the filtered name set, which the
                # method logs and threads first.  Fall back to asserting on
                # the first element if it got that far.
                raise
        names = {getattr(t, "name", "") for t in result[0]}
        assert "git" not in names
        assert "file_read" in names
