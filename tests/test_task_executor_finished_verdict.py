"""``task_finished`` carries the structured verdict, not just ``ok``.

The run record is encrypted at rest; events.jsonl is what an operator can
read after a bad night.  Until this event carried ``self_assessment`` and
``failure_reason``, the only way to learn WHY a task was recorded failed
was to reassemble the model's closing tag from text deltas.

Exercises execute_task_block end-to-end with a faked
StreamingToolExecutor, capturing every event pushed through the relay
(pattern from test_task_executor_progress_events.py).
"""

from unittest.mock import patch

import pytest

from app.agents import task_executor
from app.models.task_card import Block


def _task() -> Block:
    return Block(block_type="task", id="task-v", name="T", instructions="do it")


def _fake_executor(final_text: str):
    class _Fake:
        def __init__(self, *a, **k):
            pass

        async def stream_with_tools(self, messages, tools=None, **kwargs):
            yield {"type": "text", "content": final_text}
            yield {"type": "stream_end"}
    return _Fake


def _capture(final_text: str):
    events = []

    async def _fake_safe_push(run_id, event):
        events.append(event)

    ctx = [
        patch("app.streaming_tool_executor.StreamingToolExecutor",
              _fake_executor(final_text)),
        patch("app.agents.models.ModelManager.get_state",
              return_value={"aws_region": "us-east-1", "aws_profile": "x",
                            "current_model": "fake"}),
        patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]),
        patch("app.agents.task_run_stream_relay.safe_push", _fake_safe_push),
    ]
    return ctx, events


def _finished(events):
    fin = [e for e in events if e.get("type") == "task_finished"]
    assert len(fin) == 1, fin
    return fin[0]


@pytest.mark.asyncio
async def test_partial_verdict_is_structured_on_task_finished():
    text = ('Did most of it.\n<self_assessment objective_met="partial" '
            'rationale="Lane 1B skipped: credentials expired" />')
    patches, events = _capture(text)
    for p in patches:
        p.start()
    try:
        artifact = await task_executor.execute_task_block(_task(), run_id="run-v1")
    finally:
        for p in patches:
            p.stop()

    fin = _finished(events)
    assert fin["ok"] is False
    assert artifact.failed is True
    # The verdict the executor acted on, verbatim, not reconstructed.
    assert fin["self_assessment"] == {
        "objective_met": "partial",
        "rationale": "Lane 1B skipped: credentials expired",
    }
    assert fin["failure_signature"] == "self_assessment_partial"
    assert fin["failure_reason"] == (
        "self_assessment objective_met=partial: Lane 1B skipped: credentials expired"
    )


@pytest.mark.asyncio
async def test_true_verdict_has_no_failure_fields():
    text = 'All done.\n<self_assessment objective_met="true" rationale="ok" />'
    patches, events = _capture(text)
    for p in patches:
        p.start()
    try:
        await task_executor.execute_task_block(_task(), run_id="run-v2")
    finally:
        for p in patches:
            p.stop()

    fin = _finished(events)
    assert fin["ok"] is True
    assert fin["self_assessment"]["objective_met"] == "true"
    assert fin["failure_signature"] is None
    assert fin["failure_reason"] is None


@pytest.mark.asyncio
async def test_empty_summary_failure_reason_names_the_rule():
    """A failure the executor imposed (not a model verdict) still gets a
    readable reason — the decisions entry that explains the rule."""
    patches, events = _capture("")
    for p in patches:
        p.start()
    try:
        await task_executor.execute_task_block(_task(), run_id="run-v3")
    finally:
        for p in patches:
            p.stop()

    fin = _finished(events)
    assert fin["ok"] is False
    assert fin["self_assessment"] is None
    assert fin["failure_signature"] == "empty_summary"
    assert fin["failure_reason"].startswith("empty_summary:")


def test_failure_reason_helper_falls_back_to_signature():
    assert task_executor._failure_reason(False, None, None, []) is None
    assert task_executor._failure_reason(True, None, "output_contract:finding",
                                         []) == "output_contract:finding"
    assert task_executor._failure_reason(
        True, None, "output_contract:finding",
        ["output_contract: required output(s) not produced after one correction turn: finding"],
    ).startswith("output_contract: required")
