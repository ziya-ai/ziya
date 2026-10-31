"""A parallel group announces itself to the frontend; a lone call does not.

The batch runner (app/tool_batch.py `_run_group`) overlaps every read-only
call issued in one assistant message, but the chat rendered each member as
if it were alone, so overlap was invisible and its absence had no tell.
Pinned here: for a group of >= 2 calls every ``tool_start`` carries
``batch_group = {id, size, index}`` with index = issue order, and exactly one
``tool_group_end`` follows the (order-preserved) results with the group's
wall-clock vs. serial time.  A single-call group is byte-identical to before:
no tag, no summary.

Run:
    pytest tests/test_tool_batch_group_chrome.py -v
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app import tool_batch


def _ctx(tool_id: str, sleep_s: float):
    return SimpleNamespace(tool_id=tool_id, tool_name="run_shell_command", sleep_s=sleep_s,
                           should_stop_stream=False, deferred_feedback=None)


def _fake_execute(ctx):
    async def gen():
        yield {"type": "tool_start", "tool_id": ctx.tool_id, "tool_name": ctx.tool_name}
        await asyncio.sleep(ctx.sleep_s)
        yield {"type": "tool_display", "tool_id": ctx.tool_id, "content": "ok"}
        yield {"type": "_tool_result", "tool_id": ctx.tool_id, "tool_name": ctx.tool_name, "result": "ok"}
    return gen()


async def _collect(group):
    out = []
    async for evt in tool_batch._run_group(group, tool_batch.BatchOutcome()):
        out.append(evt)
    return out


@pytest.fixture(autouse=True)
def _patch_execute(monkeypatch):
    monkeypatch.setattr(tool_batch, "execute_single_tool", _fake_execute)
    monkeypatch.setattr(tool_batch, "_absorb", lambda ctx, outcome: None)


def test_group_members_are_tagged_in_issue_order_and_summary_follows_results():
    # Issue order a, b, c; completion order c, a, b.
    group = [_ctx("a", 0.06), _ctx("b", 0.09), _ctx("c", 0.02)]
    events = asyncio.run(_collect(group))

    starts = [e for e in events if e["type"] == "tool_start"]
    assert len(starts) == 3
    ids = {e["batch_group"]["id"] for e in starts}
    assert len(ids) == 1
    by_tool = {e["tool_id"]: e["batch_group"] for e in starts}
    assert by_tool["a"]["index"] == 0 and by_tool["b"]["index"] == 1 and by_tool["c"]["index"] == 2
    assert all(t["size"] == 3 for t in by_tool.values())

    results = [e for e in events if e["type"] == "_tool_result"]
    assert [e["tool_id"] for e in results] == ["a", "b", "c"]  # issue order kept

    ends = [e for e in events if e["type"] == "tool_group_end"]
    assert len(ends) == 1
    end = ends[0]
    assert events.index(end) > events.index(results[-1]), "summary lands after the results"
    assert end["group_id"] == ids.pop()
    assert end["size"] == 3 and end["tool_ids"] == ["a", "b", "c"]
    # Overlap is real: wall ~ max(sleeps) = 90 ms, serial ~ sum = 170 ms.
    assert end["serial_ms"] > end["wall_ms"]
    assert end["wall_ms"] < 150
    assert end["serial_ms"] >= 150


def test_lone_call_is_untagged_and_emits_no_summary():
    events = asyncio.run(_collect([_ctx("solo", 0.01)]))
    starts = [e for e in events if e["type"] == "tool_start"]
    assert len(starts) == 1 and "batch_group" not in starts[0]
    assert not any(e["type"] == "tool_group_end" for e in events)


def test_stopped_group_emits_no_summary():
    group = [_ctx("a", 0.01), _ctx("b", 0.5)]
    group[0].should_stop_stream = True
    events = asyncio.run(_collect(group))
    assert not any(e["type"] == "tool_group_end" for e in events)
    assert not any(e["type"] == "_tool_result" for e in events)


def test_executor_forwards_non_result_events_unchanged():
    """Seam: the executor passes every non-_tool_result event from
    run_tool_batch straight to the stream, so tool_group_end reaches SSE."""
    import inspect
    from app import streaming_tool_executor as ste
    src = inspect.getsource(ste.StreamingToolExecutor._stream_with_tools_impl)
    i = src.index("async for _evt in run_tool_batch(")
    window = src[i:i + 1200]
    assert "if _evt.get('type') == '_tool_result'" in window
    assert "yield _evt" in window
