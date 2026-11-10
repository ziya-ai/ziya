"""
await_diff_apply (design/await-diff-apply.md): the model parks its turn
until the diffs it emitted have an apply result.

The wait is a consent-gate record settled by the apply route, so the seam
tests run the REAL gate generator with the real tool and settle it the way
``/api/apply-changes`` does, through ``apply_ledger.record_and_settle``.
"""

import asyncio

import pytest

from app.utils import apply_ledger as al
from app.utils import consent_gate as cg
from app.mcp.tools.await_diff_apply import AwaitDiffApplyTool

DIFF_X = (
    "```diff\n"
    "diff --git a/app/x.py b/app/x.py\n--- a/app/x.py\n+++ b/app/x.py\n"
    "@@ -1,2 +1,2 @@\n-a = 1\n+a = 2\n b = 1\n"
    "@@ -10,1 +10,1 @@\n-c = 1\n+c = 2\n"
    "```\n"
)
DIFF_Y = (
    "```diff\n"
    "diff --git a/tests/test_x.py b/tests/test_x.py\n--- a/tests/test_x.py\n+++ b/tests/test_x.py\n"
    "@@ -1 +1 @@\n-x\n+y\n"
    "```\n"
)
TEXT = "Here is the fix:\n\n" + DIFF_X + "\nand its test:\n\n" + DIFF_Y


def _ok(hunks=("1", "2")):
    return {"status": "success", "details": {"hunk_statuses": {h: {"status": "succeeded"} for h in hunks}}}


def _partial():
    return {"status": "partial", "hunk_statuses": {
        "1": {"status": "succeeded"},
        "2": {"status": "failed", "error_details": {"error": "context mismatch"}}}}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    cg.reset_for_tests(tmp_path / "consents")
    monkeypatch.setattr(cg, "_consent_config", lambda: {})
    al.reset_for_tests()
    yield
    cg.reset_for_tests()
    al.reset_for_tests()


# ── expected set ──────────────────────────────────────────────────────────

def test_expected_set_counts_files_hunks_and_repeats():
    got = al.expected_from_text(TEXT + DIFF_X)
    by = {e["path"]: e for e in got["expected"]}
    assert by["app/x.py"] == {"path": "app/x.py", "count": 2, "hunks": 4}
    assert by["tests/test_x.py"]["count"] == 1 and got["not_found"] == []


def test_expected_set_restricted_to_named_files_reports_the_absent_ones():
    got = al.expected_from_text(TEXT, files=["tests/test_x.py", "nope.py"])
    assert [e["path"] for e in got["expected"]] == ["tests/test_x.py"]
    assert got["not_found"] == ["nope.py"]


def test_combined_diff_in_one_fence_yields_one_entry_per_file():
    combined = "```diff\n" + DIFF_X.split("\n", 1)[1].rsplit("```", 1)[0] + \
               DIFF_Y.split("\n", 1)[1]
    got = al.expected_from_text(combined)
    assert sorted(e["path"] for e in got["expected"]) == ["app/x.py", "tests/test_x.py"]


def test_preflight_refuses_when_there_is_nothing_to_wait_for():
    t = AwaitDiffApplyTool()
    pf = t.preflight(_turn_text="no diffs here", _conversation_id="c1")
    assert pf["error"] is True and "no ```diff" in pf["message"]
    pf = t.preflight(_turn_text=TEXT, _conversation_id="c1", files=["zzz.py"])
    assert pf["error"] is True and "zzz.py" in pf["message"]


# ── results and matching ──────────────────────────────────────────────────

def test_results_before_the_wait_count_and_unapply_removes_them():
    al.record("c1", "app/x.py", _ok())
    exp = al.expected_from_text(TEXT)["expected"]
    assert al.remaining_paths(exp, al.results_since("c1", 0)) == ["tests/test_x.py"]
    al.record("c1", "app/x.py", {"status": "success"}, kind="unapply")
    assert sorted(al.remaining_paths(exp, al.results_since("c1", 0))) == ["app/x.py", "tests/test_x.py"]


def test_two_diffs_to_one_file_need_two_results():
    exp = al.expected_from_text(DIFF_X + DIFF_X)["expected"]
    al.record("c1", "app/x.py", _ok())
    assert not al.is_complete(exp, al.results_since("c1", 0))
    al.record("c1", "app/x.py", _ok())
    assert al.is_complete(exp, al.results_since("c1", 0))


def test_report_lists_status_hunks_and_failed_reasons():
    exp = al.expected_from_text(TEXT)["expected"]
    al.record("c1", "app/x.py", _partial())
    view = {"expected": exp, "outcomes": al.outcomes(exp, al.results_since("c1", 0))}
    text = al.format_report(view)
    assert "app/x.py" in text and "partial" in text and "1/2 hunks" in text
    assert "h2 'context mismatch'" in text
    assert "tests/test_x.py" in text and "not_applied" in text


# ── the seam: real gate, real tool, settled by the route hook ─────────────

async def _run_gate(conv, text, **args):
    events = []
    async for ev in cg.consent_gate(
            tool_name="await_diff_apply", tool_id="t1", args=args,
            conversation_id=conv, tool=AwaitDiffApplyTool(), turn_text=text):
        events.append(ev)
    return events


def test_gate_opens_a_never_expiring_wait_and_the_apply_route_hook_settles_it():
    conv = "conv-seam"

    async def scenario():
        task = asyncio.ensure_future(_run_gate(conv, TEXT))
        # Let the gate open the record and start waiting.
        for _ in range(50):
            await asyncio.sleep(0.02)
            if cg.ledger().open_for(conv):
                break
        recs = cg.ledger().open_for(conv)
        assert len(recs) == 1
        rec = recs[0]
        assert rec.ttl_ms is None, "a diff wait must not expire on a timer"
        pf = rec.payload["preflight"]
        assert sorted(e["path"] for e in pf["expected"]) == ["app/x.py", "tests/test_x.py"]
        assert rec.payload["tool"] == "await_diff_apply"

        # First apply: the wait stays open and reports what remains.
        w = al.record_and_settle(conv, "app/x.py", _ok(), request_id="r1")
        assert w == {"request_id": rec.request_id,
                     "expected": pf["expected"], "remaining": ["tests/test_x.py"], "settled": False}
        assert not task.done()

        # Second apply completes the set: the gate resumes approved.
        w = al.record_and_settle(conv, "tests/test_x.py", _ok(("1",)), request_id="r2")
        assert w["settled"] is True and w["remaining"] == []
        events = await asyncio.wait_for(task, 5)
        return events

    events = asyncio.run(scenario())
    types = [e["type"] for e in events]
    assert "consent_opened" in types and "consent_answered" in types
    opened = next(e for e in events if e["type"] == "consent_opened")
    assert opened["scopes"] == ["once"] and opened["ttl_ms"] is None
    answered = next(e for e in events if e["type"] == "consent_answered")
    assert answered["decision"] == "approve" and answered["answered_by"] == "apply"
    decision = events[-1]
    assert decision["type"] == cg.DECISION_EVENT and decision["approved"] is True

    # What the model then gets from execute(): per-file outcomes.
    from app import context as appctx
    appctx.set_conversation_id(conv)
    text = asyncio.run(AwaitDiffApplyTool().execute())
    assert "app/x.py" in text and "applied" in text and "2/2 hunks" in text
    assert "tests/test_x.py" in text and "not_applied" not in text


def test_an_apply_that_landed_before_the_call_settles_on_open():
    conv = "conv-early"
    al.record(conv, "tests/test_x.py", _ok(("1",)))

    async def scenario():
        return await asyncio.wait_for(_run_gate(conv, DIFF_Y), 5)

    events = asyncio.run(scenario())
    assert events[-1]["approved"] is True
    assert next(e for e in events if e["type"] == "consent_answered")["answered_by"] == "apply"


def test_second_wait_in_a_conversation_only_counts_results_after_the_first():
    conv = "conv-cycle"

    async def scenario():
        t1 = asyncio.ensure_future(_run_gate(conv, DIFF_X))
        while not cg.ledger().open_for(conv):
            await asyncio.sleep(0.02)
        al.record_and_settle(conv, "app/x.py", _ok())
        assert (await asyncio.wait_for(t1, 5))[-1]["approved"] is True
        al.take_report(conv)
        # Same file again in the next cycle: the earlier result must not satisfy it.
        t2 = asyncio.ensure_future(_run_gate(conv, DIFF_X))
        while not cg.ledger().open_for(conv):
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.1)
        assert not t2.done()
        al.record_and_settle(conv, "app/x.py", _ok())
        assert (await asyncio.wait_for(t2, 5))[-1]["approved"] is True

    asyncio.run(scenario())


def test_declining_in_the_panel_is_the_ordinary_refusal():
    conv = "conv-decline"

    async def scenario():
        task = asyncio.ensure_future(_run_gate(conv, DIFF_X))
        while not cg.ledger().open_for(conv):
            await asyncio.sleep(0.02)
        rid = cg.ledger().open_for(conv)[0].request_id
        cg.ledger().record_answer(rid, "reject", answer="not applying this", answered_by="user")
        return await asyncio.wait_for(task, 5)

    events = asyncio.run(scenario())
    assert events[-1]["approved"] is False
    assert "not applying this" in cg.refusal_text("await_diff_apply", "*", events[-1]["answer"])


def test_no_conversation_refuses_immediately_without_a_record():
    async def scenario():
        return await asyncio.wait_for(_run_gate(None, DIFF_X), 2)
    events = asyncio.run(scenario())
    assert events[-1]["approved"] is False and events[-1]["asked"] is False


def test_tool_is_withheld_from_unattended_runs():
    from app.utils.task_tool_floor import unattended_denied_tools
    assert "await_diff_apply" in unattended_denied_tools([AwaitDiffApplyTool()])


def test_tool_is_registered_in_the_fileio_builtins():
    from app.mcp.builtin_tools import get_fileio_tools
    assert any(getattr(t, "name", None) == "await_diff_apply" for t in get_fileio_tools())


def test_executor_context_passes_turn_text_to_the_gate():
    import inspect
    from app import tool_execution
    assert "turn_text" in tool_execution.ToolExecContext.__dataclass_fields__
    src = inspect.getsource(tool_execution.execute_single_tool)
    assert "turn_text=ctx.turn_text" in src
    import app.streaming_tool_executor as ste
    assert "_pc.turn_text = assistant_text" in inspect.getsource(ste)


def test_apply_route_calls_record_and_settle():
    import inspect
    from app.routes import diff_routes
    assert "record_and_settle" in inspect.getsource(diff_routes.apply_changes)
    assert 'kind="unapply"' in inspect.getsource(diff_routes.unapply_changes)
