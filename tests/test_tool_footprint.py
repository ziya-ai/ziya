"""Batches overlap independent writes, not just reads.

app/utils/tool_footprint.py gives each call a (reads, writes) footprint;
app/tool_batch.plan_groups puts a call in the current group unless it
conflicts with a member.  Before this, every non-read was its own barrier,
so the post-nudge audit showed batches like "3 reads + 2 ledger_record"
saving only 29% of serial time.

Pinned here: which writers may overlap, which stay barriers (anything that
could open an approval panel), that conflicting calls keep issue order,
and the seam into plan_groups (the module is used, not just defined).

Run:
    pytest tests/test_tool_footprint.py -v
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.utils import tool_footprint as fp
from app import tool_batch


ROOT = "/proj"


@pytest.fixture(autouse=True)
def _no_ask_policy_allows(monkeypatch):
    """Default: nothing asks, every path is inside the write policy.
    Tests of the barrier cases override these."""
    monkeypatch.setattr(fp, "_may_ask", lambda name, args, tool, root: False)
    monkeypatch.setattr(fp, "_write_allowed", lambda path, root, exists: True)


def _ctx(tool_id, name, args=None):
    return SimpleNamespace(
        tool_id=tool_id, tool_name=name, actual_tool_name=name, args=args or {},
        project_root=ROOT, all_tools=(),
        deferred_feedback=[], feedback_received=False, should_stop_stream=False,
    )


def _groups(ctxs):
    return [[c.tool_id for c in g] for g in tool_batch.plan_groups(ctxs)]


def _fw(tid, path):
    return _ctx(tid, "file_write", {"path": path, "content": "x"})


def _claim(tid, key, inputs=None):
    der = {"obs_ids": ["o1"]}
    if inputs:
        der["inputs"] = inputs
    return _ctx(tid, "ledger_record", {"claim": {"key": key, "value": 1, "derivation": der}})


# --- grouping through plan_groups (the seam) ------------------------------

def test_reads_and_append_only_writes_share_one_group():
    ctxs = [_ctx("1", "file_read", {"path": "a.py"}),
            _ctx("2", "run_shell_command", {"command": "grep -n x app/b.py"}),
            _claim("3", "s1.total"), _claim("4", "s1.mean"),
            _ctx("5", "emit_artifact", {"name": "n", "text": "t"}),
            _ctx("6", "store_output", {"schema": "ziya:finding", "data": {}}),
            _ctx("7", "memory_propose", {"content": "c"})]
    # store_output resolves {"claim": id} numbers against the ledger, so it
    # waits for the two claims written before it; memory_propose rides along.
    assert _groups(ctxs) == [["1", "2", "3", "4", "5"], ["6", "7"]]


def test_store_output_with_no_earlier_ledger_write_joins_the_reads():
    ctxs = [_ctx("1", "file_read", {"path": "a.py"}),
            _ctx("2", "store_output", {"schema": "ziya:finding", "data": {}}),
            _ctx("3", "emit_artifact", {"name": "n", "text": "t"})]
    assert _groups(ctxs) == [["1", "2", "3"]]


def test_file_writes_to_distinct_paths_overlap_each_other_and_unrelated_reads():
    ctxs = [_fw("1", "tests/a.py"), _fw("2", "tests/b.py"),
            _ctx("3", "file_read", {"path": "app/c.py"}),
            _ctx("4", "render_diagram", {"type": "mermaid", "definition": "x"})]
    assert _groups(ctxs) == [["1", "2", "3", "4"]]


def test_same_path_write_then_read_keeps_issue_order():
    ctxs = [_fw("1", "tests/a.py"), _ctx("2", "file_read", {"path": "./tests/a.py"}),
            _ctx("3", "file_read", {"path": "tests/other.py"})]
    # 2 conflicts with 1 (same file, different spelling) -> new group, after it.
    assert _groups(ctxs) == [["1"], ["2", "3"]]


def test_two_writes_to_one_path_are_ordered():
    assert _groups([_fw("1", "x.md"), _fw("2", "x.md")]) == [["1"], ["2"]]


def test_shell_read_does_not_overlap_a_file_write():
    ctxs = [_fw("1", "tests/a.py"), _ctx("2", "run_shell_command", {"command": "cat tests/a.py"})]
    assert _groups(ctxs) == [["1"], ["2"]]


def test_same_claim_spelled_two_ways_is_ordered():
    assert _groups([_claim("1", "s1.total"), _claim("2", "total")]) == [["1"], ["2"]]


def test_claim_with_inputs_waits_for_earlier_ledger_writes():
    ctxs = [_claim("1", "s1.total"), _claim("2", "s1.share", inputs=["s1.total"])]
    assert _groups(ctxs) == [["1"], ["2"]]


def test_ledger_query_after_a_record_is_ordered():
    ctxs = [_claim("1", "s1.total"), _ctx("2", "ledger_query", {"ref": "s1.total"})]
    assert _groups(ctxs) == [["1"], ["2"]]


def test_emits_proposals_and_outputs_keep_order_among_themselves():
    e = lambda t: _ctx(t, "emit_artifact", {"name": t, "text": "x"})  # noqa: E731
    m = lambda t: _ctx(t, "memory_propose", {"content": t})  # noqa: E731
    assert _groups([e("1"), e("2")]) == [["1"], ["2"]]
    assert _groups([m("1"), _ctx("2", "memory_search", {"query": "q"})]) == [["1"], ["2"]]


def test_unknown_and_external_reads_never_overlap_a_write():
    ctxs = [_claim("1", "s1.total"), _ctx("2", "mcp_InternalSearch", {"query": "q"})]
    assert tool_batch.is_read_only_call("mcp_InternalSearch", {"query": "q"})
    assert _groups(ctxs) == [["1"], ["2"]]


def test_reads_alone_group_exactly_as_before():
    ctxs = [_ctx(str(i), "file_read", {"path": f"f{i}"}) for i in range(4)]
    assert _groups(ctxs) == [["0", "1", "2", "3"]]


# --- barriers --------------------------------------------------------------

def test_write_outside_policy_is_a_barrier(monkeypatch):
    monkeypatch.setattr(fp, "_write_allowed", lambda path, root, exists: False)
    ctxs = [_ctx("1", "file_read", {"path": "a"}), _fw("2", "/etc/hosts"), _ctx("3", "file_read", {"path": "b"})]
    assert _groups(ctxs) == [["1"], ["2"], ["3"]]


def test_writer_that_may_ask_is_a_barrier(monkeypatch):
    monkeypatch.setattr(fp, "_may_ask", lambda name, args, tool, root: name == "ledger_record")
    ctxs = [_ctx("1", "file_read", {"path": "a"}), _claim("2", "s1.x"), _ctx("3", "file_read", {"path": "b"})]
    assert _groups(ctxs) == [["1"], ["2"], ["3"]]


@pytest.mark.parametrize("ctx", [
    _ctx("2", "file_write", {"content": "x"}),                 # no path
    _ctx("2", "ledger_record", {}),                            # nothing to record
    _ctx("2", "ledger_record", {"claim": {"value": 1}}),       # claim without key
    _ctx("2", "job_start", {"command": "sleep 1"}),            # other mutating tool
    _ctx("2", "git", {"op": "commit", "message": "m"}),
])
def test_other_writes_stay_barriers(ctx):
    ctxs = [_ctx("1", "file_read", {"path": "a"}), ctx, _ctx("3", "file_read", {"path": "b"})]
    assert _groups(ctxs) == [["1"], ["2"], ["3"]]


# --- the real policy hooks (no monkeypatch) --------------------------------

def test_may_ask_follows_the_tools_declared_consent_mode(monkeypatch):
    monkeypatch.undo()  # drop the autouse stubs
    from app.utils import consent_gate as cg
    monkeypatch.setattr(cg, "_consent_config", lambda: {})
    ask_tool = SimpleNamespace(name="file_write", consent_default="ask")
    off_tool = SimpleNamespace(name="file_write", consent_default="off")
    assert fp._may_ask("file_write", {}, ask_tool, ROOT) is True
    assert fp._may_ask("file_write", {}, off_tool, ROOT) is False
    monkeypatch.setattr(cg, "_consent_config", lambda: {"tools": {"file_write": "ask"}})
    assert fp._may_ask("file_write", {}, off_tool, ROOT) is True


def test_write_allowed_uses_the_file_write_preflight_test(monkeypatch):
    monkeypatch.undo()
    from app.utils import write_grants
    seen = []

    def classify(path, root, exists):
        seen.append((path, root, exists))
        return ("outside policy", True) if path.startswith("/etc") else ("", False)
    monkeypatch.setattr(write_grants, "classify", classify)
    assert fp._write_allowed("tests/a.py", ROOT, False) is True
    assert fp._write_allowed("/etc/hosts", ROOT, True) is False
    assert seen == [("tests/a.py", ROOT, False), ("/etc/hosts", ROOT, True)]


# --- end to end through run_tool_batch ------------------------------------

def test_reads_and_writes_in_one_group_actually_overlap(monkeypatch):
    log = []

    async def fake(ctx):
        log.append(("start", ctx.tool_id))
        yield {"type": "tool_start", "tool_id": ctx.tool_id, "tool_name": ctx.tool_name}
        await asyncio.sleep(0.03)
        log.append(("end", ctx.tool_id))
        yield {"type": "_tool_result", "tool_id": ctx.tool_id, "tool_name": ctx.tool_name, "result": "ok"}
    monkeypatch.setattr(tool_batch, "execute_single_tool", fake)

    ctxs = [_ctx("1", "file_read", {"path": "a"}), _claim("2", "s1.x"), _fw("3", "tests/z.py")]
    delay = {"current": 0.0, "min": 0.0, "max": 3.0, "decay_factor": 0.6,
             "growth_factor": 2.5, "last_was_throttled": False}

    async def run():
        out = []
        async for e in tool_batch.run_tool_batch(ctxs, inter_tool_delay=delay,
                                                 outcome=tool_batch.BatchOutcome()):
            out.append(e)
        return out
    events = asyncio.run(run())
    starts = [i for i, (k, _) in enumerate(log) if k == "start"]
    first_end = next(i for i, (k, _) in enumerate(log) if k == "end")
    assert len(starts) == 3 and max(starts) < first_end, log  # all started before any ended
    group = [e for e in events if e["type"] == "tool_start"]
    assert {e["batch_group"]["size"] for e in group} == {3}
    assert [e["tool_id"] for e in events if e["type"] == "_tool_result"] == ["1", "2", "3"]
