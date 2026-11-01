"""The CLI stdout sink must strip ``<self_assessment .../>`` and
``<thinking-data>`` from the live delta stream for the WHOLE task, not only
for the text before the first tool call.

Regression: ``_StdoutSink._break_text()`` (called on ``task_tool_call``)
discarded the per-task ``_DeltaFilter``, so every delta after the first
tool call printed raw, and the final self_assessment tag -- which always
follows tool work -- appeared verbatim on the terminal.
"""
import io
import sys

import pytest

from app.cli_card_runner import _StdoutSink


async def _run(events):
    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        sink = _StdoutSink()
        for e in events:
            await sink.send_json(e)
    finally:
        sys.stdout = saved
    return buf.getvalue()


TAG = '<self_assessment objective_met="true" rationale="All 4 hunks attributed" />'


@pytest.mark.asyncio
async def test_tag_after_tool_call_is_stripped():
    out = await _run([
        {"type": "task_started", "block_name": "t"},
        {"type": "task_text_delta", "content": "Checking."},
        {"type": "task_tool_call", "tool_name": "file_read"},
        {"type": "task_text_delta", "content": "Done.\n" + TAG},
        {"type": "task_finished", "ok": True},
    ])
    assert "Done." in out          # the path ran and text was shown
    assert "Checking." in out
    assert "self_assessment" not in out


@pytest.mark.asyncio
async def test_tag_split_across_deltas_after_tool_call_is_stripped():
    out = await _run([
        {"type": "task_started", "block_name": "t"},
        {"type": "task_tool_call", "tool_name": "file_read"},
        {"type": "task_text_delta", "content": "Finished. <self_ass"},
        {"type": "task_text_delta", "content": 'essment objective_met="true" '},
        {"type": "task_text_delta", "content": 'rationale="ok" />'},
        {"type": "task_finished", "ok": True},
    ])
    assert "Finished." in out
    assert "self_ass" not in out
    assert "objective_met" not in out


@pytest.mark.asyncio
async def test_thinking_after_tool_call_is_stripped():
    out = await _run([
        {"type": "task_started", "block_name": "t"},
        {"type": "task_tool_call", "tool_name": "file_read"},
        {"type": "task_text_delta",
         "content": "<thinking-data>secret reasoning</thinking-data>Answer."},
        {"type": "task_finished", "ok": True},
    ])
    assert "Answer." in out
    assert "secret reasoning" not in out


@pytest.mark.asyncio
async def test_incomplete_tag_at_task_end_is_not_printed():
    out = await _run([
        {"type": "task_started", "block_name": "t"},
        {"type": "task_text_delta", "content": 'Ok. <self_assessment objective_met="tr'},
        {"type": "task_finished", "ok": True},
    ])
    assert "Ok." in out
    assert "self_assessment" not in out


@pytest.mark.asyncio
async def test_real_angle_bracket_survives():
    out = await _run([
        {"type": "task_started", "block_name": "t"},
        {"type": "task_tool_call", "tool_name": "x"},
        {"type": "task_text_delta", "content": "a < b and c<d"},
        {"type": "task_finished", "ok": True},
    ])
    assert "a < b and c<d" in out


@pytest.mark.asyncio
async def test_progress_tag_is_stripped_without_gluing_sentences():
    # The model is told to emit <progress note=.../> mid-stream; the GUI
    # strips it (strip_progress_tags) and fills the gap with one space.
    out = await _run([
        {"type": "task_started", "block_name": "t"},
        {"type": "task_text_delta", "content": 'Test gate.<progress note="3/'},
        {"type": "task_text_delta", "content": '10 done"/>Next phase.'},
        {"type": "task_finished", "ok": True},
    ])
    assert "Test gate. Next phase." in out
    assert "progress" not in out


@pytest.mark.asyncio
async def test_progress_tag_between_spaces_adds_no_extra_space():
    out = await _run([
        {"type": "task_started", "block_name": "t"},
        {"type": "task_text_delta", "content": 'One.\n<progress note="x"/>\nTwo.'},
        {"type": "task_finished", "ok": True},
    ])
    assert "One.\n\nTwo." in out


@pytest.mark.asyncio
async def test_iteration_marker_starts_on_its_own_line():
    out = await _run([
        {"type": "task_started", "block_name": "t"},
        {"type": "task_text_delta", "content": "partial line"},
        {"type": "iteration_started", "index": 2},
    ])
    assert "↻ iteration 2" in out
    assert "partial line\n" in out
