"""A model turn that follows another WITHOUT a tool call in between (the
output-contract correction turn, the time-budget closing turn) must be
separated on the LIVE stream, not only in ``collected_text``.

Regression: the executor appended "\\n\\n" to ``collected_text`` at those
boundaries but emitted no ``task_text_delta``, so the CLI sink printed the
second turn glued onto the last line of the first.
"""
import asyncio
import io
import sys
from unittest.mock import patch

import pytest

from app.agents.block_executor import ExecutionContext, execute_block
from app.cli_card_runner import _StdoutSink
from app.models.task_card import Block

FIND = [{"name": "finding", "schema": "ziya:finding/v1", "required": True}]
FINDING = {"id": "T-X", "title": "t", "verdict": "candidate"}


async def _emit_artifact(**kw):
    from app.mcp.tools.emit_artifact import EmitArtifactTool
    await EmitArtifactTool().execute(**kw)


class _Scripted:
    calls = 0

    def __init__(self, *a, **k):
        pass

    async def stream_with_tools(self, messages, tools=None, project_root=None, **_):
        n = type(self).calls
        type(self).calls += 1
        if n == 0:
            # First turn: prose, but the required output is missing.
            yield {"type": "text", "content": "First turn ends here."}
        else:
            await _emit_artifact(name="finding", part_type="data", data=FINDING)
            yield {"type": "text", "content":
                   'Second turn. <self_assessment objective_met="true" rationale="ok"/>'}
        yield {"type": "stream_end"}


def _run(tmp_path):
    _Scripted.calls = 0
    events = []

    async def _push(run_id, event):
        events.append(event)

    blk = Block(block_type="task", id="t1", name="Lane", instructions="x",
                output_contract=FIND)
    with patch("app.streaming_tool_executor.StreamingToolExecutor", _Scripted), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1", "aws_profile": "x",
                             "current_model": "f"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]), \
         patch("app.agents.task_run_stream_relay.safe_push", _push):
        ctx = ExecutionContext(run_id="run-s", project_root=str(tmp_path), storage=None)
        asyncio.run(execute_block(blk, ctx))
    return events


def test_correction_turn_seam_is_on_the_live_stream(tmp_path):
    events = _run(tmp_path)
    assert _Scripted.calls == 2, "correction turn did not run"
    live = "".join(e["content"] for e in events if e.get("type") == "task_text_delta")
    assert "First turn ends here." in live and "Second turn." in live
    assert "First turn ends here.\n\nSecond turn." in live


@pytest.mark.asyncio
async def test_cli_sink_prints_turns_on_separate_lines(tmp_path):
    events = await asyncio.to_thread(_run, tmp_path)
    buf, saved = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        sink = _StdoutSink()
        for e in events:
            await sink.send_json(e)
    finally:
        sys.stdout = saved
    out = buf.getvalue()
    assert "Second turn." in out
    assert "here.Second" not in out
    assert "First turn ends here.\n" in out
