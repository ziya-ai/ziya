"""Lifecycle hooks through the real tool-execution path.

execute_single_tool -> builtin file_write -> app/utils/lifecycle_hooks.after_tool
-> ``post_tool`` and ``post_apply`` hooks from ``.ziya/hooks.yaml``.  Asserts
on the ``tool_result_for_model`` event, i.e. what the model receives, so the
wiring in app/tool_execution.py is what is under test, not ``after_tool``.
"""
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.mcp.enhanced_tools import DirectMCPTool
from app.tool_execution import ToolExecContext, execute_single_tool
from app.utils import policy_exec


def _allow(command, *, project_root, shell_commands=None):
    return True, "", {"PATH": os.environ.get("PATH", "")}


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    (root / ".ziya").mkdir(parents=True)
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(root))
    return root


def _ctx(root, tool, args):
    from app.mcp.tools.fileio import FileWriteTool
    manager = MagicMock()
    manager._exceeds_turn_ceiling = lambda *_: False
    manager._is_repetitive_call = lambda *_: False
    executor = SimpleNamespace(
        _format_tool_result=lambda name, text, a: text,
        _infer_syntax_hint=lambda name, a: "text",
        _get_tool_header=lambda name, a: tool,
    )
    return ToolExecContext(
        tool_id="tc-hook", tool_name=tool, actual_tool_name=tool,
        args=dict(args), all_tools=[DirectMCPTool(FileWriteTool())],
        internal_tool_names=set(), mcp_manager=manager, project_root=str(root),
        conversation_id="conv-hooks", conversation=[], recent_commands=[],
        inter_tool_delay={'current': 0.0, 'min': 0.0, 'max': 1, 'decay_factor': 0.6,
                          'growth_factor': 2.5, 'last_was_throttled': False},
        iteration_start_time=0, track_yield_fn=lambda x: x,
        drain_feedback_fn=lambda: [], executor=executor,
    )


async def _model_text(root, tool, args) -> str:
    events = []

    async def pull():
        async for ev in execute_single_tool(_ctx(root, tool, args)):
            events.append(ev)
    await asyncio.wait_for(pull(), 60)
    model = [e for e in events if e.get("type") == "tool_result_for_model"]
    assert model, f"no tool_result_for_model event: {[e.get('type') for e in events]}"
    content = model[-1]["content"]
    if isinstance(content, list):
        content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return str(content)


@pytest.mark.asyncio
async def test_awaited_post_apply_hook_output_reaches_the_model(proj):
    (proj / ".ziya" / "hooks.yaml").write_text(
        'hooks:\n'
        '  post_apply:\n'
        '    - run: "echo LINT {file_rel}; echo E501 too long >&2; exit 1"\n'
        '      match: "*.py"\n'
        '      await: true\n'
        '  post_tool:\n'
        '    - run: "echo TOOL {tool}"\n'
        '      await: true\n'
    )
    with patch.object(policy_exec, "adjudicate", _allow):
        text = await _model_text(proj, "file_write", {"path": ".ziya/new.py", "content": "x = 1\n"})
    assert (proj / ".ziya" / "new.py").read_text() == "x = 1\n"
    assert "TOOL file_write" in text
    assert "LINT .ziya/new.py" in text and "E501" in text and "exited 1" in text


@pytest.mark.asyncio
async def test_no_hooks_file_leaves_the_result_untouched(proj):
    text = await _model_text(proj, "file_write", {"path": ".ziya/plain.py", "content": "y = 2\n"})
    assert "[hook" not in text
    assert (proj / ".ziya" / "plain.py").exists()
