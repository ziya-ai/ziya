"""``pre_tool`` (veto) and ``session_start`` lifecycle hooks
(app/utils/lifecycle_hooks.py), and the seams that consume them:
app/tool_execution.py before the consent gate, and
app/utils/session_context_prompt.py's Session Context block."""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.utils import lifecycle_hooks as lh
from app.utils import policy_exec


def _allow(command, *, project_root, shell_commands=None):
    return True, "", {"PATH": os.environ.get("PATH", "")}


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / ".ziya").mkdir()
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))
    lh.reset_session_notes_for_tests()
    return tmp_path


def _hooks(project, text):
    (project / ".ziya" / "hooks.yaml").write_text(text)


# ----------------------------------------------------------------- pre_tool


def test_pre_tool_is_always_awaited_and_parses_on_error(project):
    _hooks(project, "hooks:\n  pre_tool:\n    - run: 'true'\n      on_error: deny\n    - run: 'true'\n      on_error: sideways\n")
    hooks = lh.load_hooks(str(project))
    assert all(h.await_result for h in hooks)
    assert hooks[0].on_error == "deny" and hooks[1].on_error == "allow"


@pytest.mark.asyncio
async def test_exit_zero_allows_exit_two_vetoes_with_reason(project):
    _hooks(project, """
hooks:
  pre_tool:
    - run: "test $ZIYA_HOOK_TOOL != run_shell_command || { echo 'shell is off in this repo'; exit 2; }"
""")
    with patch.object(policy_exec, "adjudicate", _allow):
        assert await lh.before_tool("file_read", {"path": "a"}, project_root=str(project)) is None
        why = await lh.before_tool("run_shell_command", {"command": "ls"}, project_root=str(project))
    assert why and "shell is off in this repo" in why and "pre_tool" in why


@pytest.mark.asyncio
async def test_hook_error_allows_by_default_and_denies_when_configured(project):
    _hooks(project, "hooks:\n  pre_tool:\n    - run: 'exit 7'\n")
    with patch.object(policy_exec, "adjudicate", _allow):
        assert await lh.before_tool("file_read", {}, project_root=str(project)) is None
    _hooks(project, "hooks:\n  pre_tool:\n    - run: 'exit 7'\n      on_error: deny\n")
    os.utime(project / ".ziya" / "hooks.yaml", None)
    lh._cache.clear()
    with patch.object(policy_exec, "adjudicate", _allow):
        why = await lh.before_tool("file_read", {}, project_root=str(project))
    assert why and "on_error: deny" in why


@pytest.mark.asyncio
async def test_tools_filter_and_routing_keys_hidden(project):
    seen = project / "seen"
    _hooks(project, f"hooks:\n  pre_tool:\n    - run: 'cat > {seen}'\n      tools: [file_write]\n")
    with patch.object(policy_exec, "adjudicate", _allow):
        await lh.before_tool("file_read", {}, project_root=str(project))
        assert not seen.exists()
        await lh.before_tool("file_write", {"path": "x", "_workspace_path": "/p"}, project_root=str(project))
    body = seen.read_text()
    assert '"path": "x"' in body and "_workspace_path" not in body


# ------------------------------------------------- seam: tool_execution


def _ctx(root, tool, args):
    from app.mcp.enhanced_tools import DirectMCPTool
    from app.mcp.tools.fileio import FileWriteTool
    from app.tool_execution import ToolExecContext
    manager = MagicMock()
    manager._exceeds_turn_ceiling = lambda *_: False
    manager._is_repetitive_call = lambda *_: False
    executor = SimpleNamespace(
        _format_tool_result=lambda name, text, a: text,
        _infer_syntax_hint=lambda name, a: "text",
        _get_tool_header=lambda name, a: tool,
    )
    return ToolExecContext(
        tool_id="tc-pre", tool_name=tool, actual_tool_name=tool,
        args=dict(args), all_tools=[DirectMCPTool(FileWriteTool())],
        internal_tool_names=set(), mcp_manager=manager, project_root=str(root),
        conversation_id="conv-pre", conversation=[], recent_commands=[],
        inter_tool_delay={'current': 0.0, 'min': 0.0, 'max': 1, 'decay_factor': 0.6,
                          'growth_factor': 2.5, 'last_was_throttled': False},
        iteration_start_time=0, track_yield_fn=lambda x: x,
        drain_feedback_fn=lambda: [], executor=executor,
    )


@pytest.mark.asyncio
async def test_vetoed_file_write_does_not_touch_disk_and_the_model_is_told(project):
    from app.tool_execution import execute_single_tool
    _hooks(project, "hooks:\n  pre_tool:\n    - run: 'echo no writes under .ziya/vetoed; exit 2'\n      tools: [file_write]\n")
    events = []

    async def pull():
        async for ev in execute_single_tool(_ctx(project, "file_write",
                                                 {"path": ".ziya/vetoed/x.txt", "content": "nope"})):
            events.append(ev)
    with patch.object(policy_exec, "adjudicate", _allow):
        await asyncio.wait_for(pull(), 60)
    assert not (project / ".ziya" / "vetoed" / "x.txt").exists()
    results = [e for e in events if e.get("type") == "_tool_result"]
    assert results and "no writes under .ziya/vetoed" in str(results[-1]["result"])
    # The veto happened before any consent prompt could open.
    assert not any(e.get("type") == "consent_opened" for e in events)


# -------------------------------------------------------- session_start


def test_session_notes_run_once_per_session_key_and_land_in_the_prompt(project):
    counter = project / "count"
    _hooks(project, f"hooks:\n  session_start:\n    - run: 'echo x >> {counter}; echo branch: main; echo 2 dirty files'\n")
    from app.utils.session_context_prompt import build_session_context_section
    with patch.object(policy_exec, "adjudicate", _allow):
        a = build_session_context_section(project_root=str(project), conv_start_iso="2026-11-05T10:00:00")
        b = build_session_context_section(project_root=str(project), conv_start_iso="2026-11-05T10:00:00")
        c = build_session_context_section(project_root=str(project), conv_start_iso="2026-11-05T11:00:00")
    assert "### Session notes" in a and "branch: main" in a and "2 dirty files" in a
    assert a.count("branch: main") == 1 and "branch: main" in b and "branch: main" in c
    assert counter.read_text().count("x") == 2        # once per conversation, not per turn


def test_failing_session_hook_adds_nothing(project):
    _hooks(project, "hooks:\n  session_start:\n    - run: 'echo partial; exit 1'\n")
    with patch.object(policy_exec, "adjudicate", _allow):
        assert lh.session_notes(str(project), "s") == ""


def test_no_session_hooks_means_no_section(project):
    from app.utils.session_context_prompt import build_session_context_section
    assert "Session notes" not in build_session_context_section(project_root=str(project))
