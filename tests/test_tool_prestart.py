"""Read-only calls start while the model is still writing the message.

app/utils/tool_prestart.py starts the leading run of read-only calls as
each tool_use block closes; app/tool_batch.run_tool_batch(prestarted=...)
replays them instead of executing them again.  Pinned here: what may start
early (leading reads only, never with feedback pending, never when turned
off), that a pre-started call runs exactly once and its events still come
out of the batch in issue order with group chrome, that unused or stopped
work is cancelled, and the seams into the executor and the batch runner.

Run:
    pytest tests/test_tool_prestart.py -v
"""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from app import tool_batch
from app.utils import tool_prestart as tp


def _ctx(tool_id, name, args=None, pending=None):
    return SimpleNamespace(
        tool_id=tool_id, tool_name=name, actual_tool_name=name, args=args or {},
        project_root="/proj", all_tools=(),
        deferred_feedback=[], feedback_received=False, should_stop_stream=False,
        drain_feedback_fn=lambda: ["SHOULD NOT BE DRAINED"],
        peek_feedback_fn=lambda: list(pending or []),
    )


def _read(tid):
    return _ctx(tid, "file_read", {"path": f"f{tid}"})


def _write(tid):
    return _ctx(tid, "git", {"op": "commit", "message": "m"})  # always a barrier


def _fake(log, dur=0.03, stop_ids=()):
    async def fake(ctx):
        log.append(("start", ctx.tool_id))
        yield {"type": "tool_start", "tool_id": ctx.tool_id, "tool_name": ctx.tool_name}
        try:
            await asyncio.sleep(dur)
        except asyncio.CancelledError:
            log.append(("cancelled", ctx.tool_id))
            raise
        if ctx.tool_id in stop_ids:
            ctx.should_stop_stream = True
            return
        log.append(("end", ctx.tool_id))
        yield {"type": "_tool_result", "tool_id": ctx.tool_id, "tool_name": ctx.tool_name,
               "result": f"r-{ctx.tool_id}"}
    return fake


def _delay():
    return {"current": 0.0, "min": 0.0, "decay_factor": 1.0}


async def _batch(ctxs, pre, outcome=None):
    out = []
    async for e in tool_batch.run_tool_batch(ctxs, inter_tool_delay=_delay(),
                                             outcome=outcome or tool_batch.BatchOutcome(),
                                             prestarted=pre):
        out.append(e)
    return out


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    from app.utils import tool_footprint as fp
    monkeypatch.setattr(fp, "_may_ask", lambda *a: False)
    monkeypatch.setattr(fp, "_write_allowed", lambda *a: True)
    monkeypatch.delenv("ZIYA_TOOL_PRESTART", raising=False)


# --- what may start early ---------------------------------------------------

def test_only_the_leading_reads_start_early():
    async def run():
        log = []
        pre = tp.ToolPrestarter(execute=_fake(log))
        flags = [pre.offer(c) for c in (_read("1"), _read("2"), _write("3"), _read("4"))]
        await asyncio.sleep(0)
        ids = pre.started_ids()
        await pre.discard()
        return flags, ids, log
    flags, ids, log = asyncio.run(run())
    assert flags == [True, True, False, False]
    assert ids == ["1", "2"]
    assert ("start", "1") in log and ("start", "4") not in log


def test_nothing_starts_while_feedback_is_pending():
    async def run():
        pre = tp.ToolPrestarter(execute=_fake([]))
        ok = pre.offer(_ctx("1", "file_read", {"path": "a"}, pending=[{"type": "feedback"}]))
        later = pre.offer(_read("2"))  # closed by the first refusal
        await pre.discard()
        return ok, later
    assert asyncio.run(run()) == (False, False)


def test_env_switch_turns_it_off(monkeypatch):
    monkeypatch.setenv("ZIYA_TOOL_PRESTART", "0")

    async def run():
        pre = tp.ToolPrestarter(execute=_fake([]))
        return pre.offer(_read("1"))
    assert asyncio.run(run()) is False


def test_prestarted_call_does_not_drain_feedback():
    async def run():
        c = _read("1")
        pre = tp.ToolPrestarter(execute=_fake([]))
        pre.offer(c)
        drained = c.drain_feedback_fn()
        await pre.discard()
        return drained
    assert asyncio.run(run()) == []


# --- through run_tool_batch (the seam) ---------------------------------------

def test_prestarted_reads_run_once_before_dispatch_and_replay_in_order(monkeypatch):
    log = []
    monkeypatch.setattr(tool_batch, "execute_single_tool", _fake(log, dur=0.05))

    async def run():
        pre = tp.ToolPrestarter()  # default executor: resolved via tool_batch
        ctxs = [_read("1"), _read("2"), _write("3")]
        for c in ctxs:
            pre.offer(c)
        await asyncio.sleep(0.01)          # the model is still "streaming"
        log.append(("dispatch", None))
        events = await _batch(ctxs, pre)
        await pre.discard()
        return events
    events = asyncio.run(run())
    starts = [e[1] for e in log if e[0] == "start"]
    assert starts == ["1", "2", "3"], log            # each call ran exactly once
    assert log.index(("start", "2")) < log.index(("dispatch", None)) < log.index(("start", "3"))
    assert [e["tool_id"] for e in events if e["type"] == "_tool_result"] == ["1", "2", "3"]
    tagged = {e["tool_id"]: e.get("batch_group") for e in events if e["type"] == "tool_start"}
    assert tagged["1"]["size"] == 2 and tagged["2"]["index"] == 1 and tagged["3"] is None
    end = next(e for e in events if e["type"] == "tool_group_end")
    assert end["tool_ids"] == ["1", "2"]


def test_batch_without_prestarter_is_unchanged(monkeypatch):
    log = []
    monkeypatch.setattr(tool_batch, "execute_single_tool", _fake(log))
    events = asyncio.run(_batch([_read("1"), _read("2")], None))
    assert [e["tool_id"] for e in events if e["type"] == "_tool_result"] == ["1", "2"]


def test_stop_in_a_group_cancels_a_prestarted_sibling(monkeypatch):
    log = []
    monkeypatch.setattr(tool_batch, "execute_single_tool", _fake(log, dur=0.02, stop_ids=("2",)))

    async def run():
        pre = tp.ToolPrestarter(execute=_fake(log, dur=1.0))
        a = _read("1")
        pre.offer(a)
        b = _read("2")                     # not offered: runs at dispatch, stops
        outcome = tool_batch.BatchOutcome()
        events = await _batch([a, b], pre, outcome)
        return events, outcome
    events, outcome = asyncio.run(run())
    assert outcome.stop_requested
    assert ("cancelled", "1") in log
    assert not any(e["type"] == "_tool_result" for e in events)


def test_discard_cancels_untaken_calls():
    async def run():
        log = []
        pre = tp.ToolPrestarter(execute=_fake(log, dur=1.0))
        pre.offer(_read("1"))
        await asyncio.sleep(0)
        await pre.discard()
        return log, pre.started_ids()
    log, ids = asyncio.run(run())
    assert ("cancelled", "1") in log and ids == []


def test_executor_offers_each_queued_call_and_hands_the_prestarter_to_the_batch():
    from app import streaming_tool_executor as ste
    src = inspect.getsource(ste.StreamingToolExecutor._stream_with_tools_impl)
    q = src.index("pending_tool_ctxs.append(_exec_ctx)")
    assert "_prestart.offer(_exec_ctx)" in src[q:q + 300]
    d = src.index("async for _evt in run_tool_batch(")
    # Scoped to the call's argument list (up to its closing "):"), and the
    # keyword must appear exactly once in the function: a misapplied hunk once
    # left a second copy in the loop body, where Python parses it as a
    # harmless-looking assignment `prestarted = (_prestart,)`.
    call_args = src[d:src.index("):", d)]
    assert "prestarted=_prestart" in call_args, call_args
    assert src.count("prestarted=_prestart") == 1
    assert src.count("await _prestart.discard()") >= 3  # retry, mid-stream stop, after batch
