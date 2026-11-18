"""
Diff-apply hold (design/await-diff-apply.md): an interactive web-chat
response that ends with closed ```diff fences does not end the turn.  The
executor holds the tool chain open until every diffed file has an apply
result, then hands the model the per-file outcome and continues.

  * every diff applied cleanly -> report, continue
  * any partial / failed apply -> report at once, continue
  * user sends a message       -> wait ends; feedback carries the report
  * CLI / Task Card / delegate -> never held (no Apply button there)

The seam tests drive the real ``stream_with_tools`` loop with a scripted
provider and settle the wait the way ``/api/apply-changes`` does, through
``apply_ledger.record``.  They assert on what the model was SENT and what the
stream YIELDED.
"""

import asyncio
import copy
import inspect
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.providers.base import StreamEnd, TextDelta
from app.utils import apply_ledger as al

CONV = "conv-diff-hold"

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
# A second, unrelated edit to app/x.py: far from DIFF_X's hunks, another body.
DIFF_X_ELSEWHERE = (
    "```diff\n"
    "diff --git a/app/x.py b/app/x.py\n--- a/app/x.py\n+++ b/app/x.py\n"
    "@@ -40,1 +40,1 @@\n-zeta = 1\n+zeta = 2\n"
    "```\n"
)
TEXT = "Here is the fix:\n\n" + DIFF_X + "\nand its test:\n\n" + DIFF_Y
# A response that ends on a closing fence is continued by the loop before
# the hold is reached; real responses end in prose, so the scripted one does.
RESPONSE = TEXT + "\nOnce both are applied I will run the tests.\n"


def _ok(hunks=("1", "2")):
    return {"status": "success", "details": {"hunk_statuses": {h: {"status": "succeeded"} for h in hunks}}}


def _partial():
    return {"status": "partial", "hunk_statuses": {
        "1": {"status": "succeeded"},
        "2": {"status": "failed", "error_details": {"error": "context mismatch"}}}}


@pytest.fixture(autouse=True)
def isolated():
    al.reset_for_tests()
    yield
    al.reset_for_tests()


# ── ledger ────────────────────────────────────────────────────────────────

def test_expected_set_counts_files_hunks_and_repeats():
    got = al.expected_from_text(TEXT + DIFF_X_ELSEWHERE)
    by = {e["path"]: e for e in got["expected"]}
    x = by["app/x.py"]
    assert (x["path"], x["count"], x["hunks"]) == ("app/x.py", 2, 3)
    assert len(x["keys"]) == 2 and x["keys"][0] != x["keys"][1]   # two independent fences
    assert by["tests/test_x.py"]["count"] == 1 and got["not_found"] == []
    assert got["superseded"] == set()


def test_unclosed_fence_is_not_expected():
    assert al.expected_from_text("text\n" + DIFF_X.rstrip("`\n"))["expected"] == []


def test_turn_text_includes_diffs_emitted_before_a_tool_call():
    msgs = [{"role": "assistant", "content": [{"type": "text", "text": DIFF_X},
                                              {"type": "tool_use", "id": "t", "name": "n", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": DIFF_Y}]}]
    paths = {e["path"] for e in al.expected_from_text(al.turn_text(msgs, "done"))["expected"]}
    # The tool result's text is not the model's: only app/x.py is awaited.
    assert paths == {"app/x.py"}


def test_turn_text_does_not_count_the_current_response_twice():
    # The executor's hold runs after the response is already in the
    # conversation; counting it again doubled every fence.
    msgs = [{"role": "assistant", "content": [{"type": "text", "text": TEXT}]}]
    exp = al.expected_from_text(al.turn_text(msgs, TEXT))["expected"]
    assert {e["path"]: e["count"] for e in exp} == {"app/x.py": 1, "tests/test_x.py": 1}
    # Positive control: a current response not yet appended is still counted.
    exp2 = al.expected_from_text(al.turn_text([], TEXT))["expected"]
    assert {e["path"]: e["count"] for e in exp2} == {"app/x.py": 1, "tests/test_x.py": 1}


def test_wait_state_complete_partial_and_seq_scoping():
    exp = al.expected_from_text(TEXT)["expected"]
    al.record(CONV, "app/x.py", _ok())          # before the turn
    since = al.current_seq()
    st = al.wait_state(CONV, exp, since)
    assert sorted(st["remaining"]) == ["app/x.py", "tests/test_x.py"]
    assert not st["complete"] and not st["failed"]
    al.record(CONV, "./app/x.py", _ok())
    al.record(CONV, "tests/test_x.py", {"status": "already_applied"})
    st = al.wait_state(CONV, exp, since)
    assert st["complete"] and st["remaining"] == []
    al.record(CONV, "app/x.py", _partial())     # later turn
    st2 = al.wait_state(CONV, exp, al.current_seq() - 1)
    assert st2["failed"] == ["app/x.py"] and not st2["complete"]


def test_report_names_failed_hunks_and_unapplied_files():
    exp = al.expected_from_text(TEXT)["expected"]
    al.record(CONV, "app/x.py", _partial())
    text = al.format_report(al.wait_state(CONV, exp, 0), "failed")
    assert "app/x.py" in text and "partial" in text and "1/2 hunks" in text
    assert "h2 'context mismatch'" in text
    assert "tests/test_x.py" in text and "not_applied" in text


def test_chip_view_counts_fences():
    exp = al.expected_from_text(TEXT + DIFF_X_ELSEWHERE)["expected"]
    al.record(CONV, "app/x.py", _ok())
    assert al.chip_view(al.wait_state(CONV, exp, 0)) == {
        "total": 3, "remaining": 2, "remaining_paths": ["app/x.py", "tests/test_x.py"]}


# ── seam: the real loop ───────────────────────────────────────────────────

class _Provider:
    provider_name = "scripted"

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def stream_response(self, conversation, system_content, tools, config):
        self.calls.append(copy.deepcopy(conversation))
        i = len(self.calls) - 1
        for ev in (self.script[i] if i < len(self.script) else []):
            yield ev

    def build_assistant_message(self, text, tool_uses, thinking_blocks=None, **_kw):
        content = [{"type": "text", "text": text}] if text else []
        for tu in tool_uses:
            content.append({"type": "tool_use", "id": tu["id"], "name": tu["name"], "input": tu["input"]})
        return {"role": "assistant", "content": content}

    def build_tool_result_message(self, tool_results):
        return {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r["tool_use_id"], "content": r["content"]}
            for r in tool_results]}

    def supports_feature(self, name):
        return False

    def prepare_cache_control(self, messages, *a, **k):
        return messages


def _make_executor(provider):
    with patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock", "ZIYA_MODEL": "sonnet3.7"}):
        with patch("app.streaming_tool_executor.StreamingToolExecutor.__init__", return_value=None):
            from app.streaming_tool_executor import StreamingToolExecutor
            ex = StreamingToolExecutor.__new__(StreamingToolExecutor)
    ex.model_id = "test-model"
    ex.model_config = {"family": "claude", "max_output_tokens": 8192, "supports_assistant_prefill": True}
    ex.bedrock = None
    ex.provider = provider
    ex._block_opening_buffer = ""
    ex._normalize_fence_spacing = lambda text, tracker, **kw: text
    ex._update_code_block_tracker = lambda text, tracker: None
    optimizer = MagicMock()
    optimizer.add_content.side_effect = lambda t: [t] if t else []
    optimizer.flush_remaining.return_value = ""
    ex._content_optimizer = optimizer
    ex._build_conversation_from_messages = MagicMock(return_value=(
        [{"role": "user", "content": [{"type": "text", "text": "Go"}]}], "system prompt"))
    ex._format_tools_for_api = MagicMock(return_value=[])
    ex._build_provider_config = MagicMock(return_value=MagicMock(max_output_tokens=8192))
    ex._handle_usage_event = MagicMock()
    ex._build_tool_reminder_message = MagicMock(return_value={"role": "user", "content": "reminder"})
    ex._load_and_prepare_tools = AsyncMock(return_value=([], [], set(), set(), set()))
    return ex


def _diffs_then_end():
    return [TextDelta(content=RESPONSE), StreamEnd(stop_reason="end_turn")]


def _final():
    return [TextDelta(content="Tests pass."), StreamEnd(stop_reason="end_turn")]


async def _run(script, during=None, history=None, **kw):
    """Run the loop; ``during(provider)`` runs concurrently and plays the user."""
    provider = _Provider(script)
    ex = _make_executor(provider)
    if history is not None:
        ex._build_conversation_from_messages = MagicMock(return_value=(history, "system prompt"))
    mgr = MagicMock()
    mgr.is_initialized = True
    events = []

    async def drive():
        async for ev in ex.stream_with_tools(
                messages=[{"role": "user", "content": "Go"}], tools=[],
                conversation_id=CONV, **kw):
            events.append(ev)
            if len(events) > 400:
                break

    with patch("app.mcp.manager.get_mcp_manager", return_value=mgr), \
         patch("app.streaming_tool_executor.StreamingToolExecutor._FEEDBACK_GRACE_SECONDS", 0, create=True):
        task = asyncio.ensure_future(drive())
        if during:
            await during(provider)
        await asyncio.wait_for(task, 20)
    return provider, events


async def _until_waiting(provider, events_ref=None):
    for _ in range(200):
        if provider.calls:
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.2)


def _user_texts(conv):
    out = []
    for m in conv:
        if m.get("role") != "user":
            continue
        c = m.get("content")
        out.append(c if isinstance(c, str) else " ".join(
            b.get("text", "") for b in c if isinstance(b, dict)))
    return out


def _of(events, kind):
    return [e for e in events if e.get("type") == kind]


@pytest.mark.asyncio
async def test_both_applied_resumes_the_same_chain_with_the_report():
    async def user(provider):
        await _until_waiting(provider)
        assert len(provider.calls) == 1, "turn did not hold"
        al.record(CONV, "app/x.py", _ok())
        await asyncio.sleep(0.8)
        assert len(provider.calls) == 1, "resumed with a file still unapplied"
        al.record(CONV, "tests/test_x.py", _ok(("1",)))

    provider, events = await _run([_diffs_then_end(), _final()], user, hold_for_diff_apply=True)
    assert len(provider.calls) == 2
    last = _user_texts(provider.calls[1])[-1]
    assert "[Diff apply results]" in last and "were applied" in last
    assert "app/x.py" in last and "2/2 hunks" in last and "not_applied" not in last
    waits = _of(events, "diff_wait")
    assert waits[0]["state"] == "waiting" and waits[0]["remaining"] == 2
    assert any(w["state"] == "waiting" and w["remaining"] == 1 for w in waits)
    assert waits[-1]["state"] == "done" and waits[-1]["outcome"] == "complete"
    assert "Tests pass." in "".join(e.get("content", "") for e in _of(events, "text"))
    # The resumed call carries the response once, not twice.
    roles = [m["role"] for m in provider.calls[1]]
    assert not any(a == b == "assistant" for a, b in zip(roles, roles[1:])), roles
    assert sum("Here is the fix" in str(m.get("content")) for m in provider.calls[1]) == 1
    # What the hold reported is not reported again when the next turn starts.
    assert al.begin_turn(CONV, _conv("Tests pass.")) is None


@pytest.mark.asyncio
async def test_response_ending_on_a_fence_still_holds_for_its_diffs():
    """The loop continues a response that ends on a closing fence before the
    hold point is reached.  The diffs from that first call must still be
    awaited when the continuation ends."""
    script = [[TextDelta(content=TEXT), StreamEnd(stop_reason="end_turn")],
              [TextDelta(content="Apply both and I will run the tests."), StreamEnd(stop_reason="end_turn")],
              _final(), _final()]

    async def user(provider):
        for _ in range(300):
            if any(w.get("state") == "waiting" for w in seen):
                break
            await asyncio.sleep(0.02)
        al.record(CONV, "app/x.py", _ok())
        al.record(CONV, "tests/test_x.py", _ok(("1",)))

    seen = []
    import app.utils.apply_ledger as _mod
    real = _mod.chip_view

    def spy(state):
        v = real(state)
        seen.append({"state": "waiting", **v})
        return v

    with patch.object(_mod, "chip_view", spy):
        provider, events = await _run(script, user, hold_for_diff_apply=True)
    reports = [t for conv in provider.calls for t in _user_texts(conv) if "[Diff apply results]" in t]
    assert reports, f"never held; {len(provider.calls)} calls"
    assert "app/x.py" in reports[-1] and "tests/test_x.py" in reports[-1]


@pytest.mark.asyncio
async def test_partial_apply_reports_at_once_without_waiting_for_the_rest():
    async def user(provider):
        await _until_waiting(provider)
        al.record(CONV, "app/x.py", _partial())

    provider, events = await _run([_diffs_then_end(), _final()], user, hold_for_diff_apply=True)
    assert len(provider.calls) == 2
    last = _user_texts(provider.calls[1])[-1]
    assert "partial" in last and "context mismatch" in last
    assert "tests/test_x.py" in last and "not_applied" in last
    assert _of(events, "diff_wait")[-1]["outcome"] == "failed"


@pytest.mark.asyncio
async def test_a_sent_message_ends_the_wait_and_carries_the_report():
    async def user(provider):
        from app.server import pending_feedback_lists
        await _until_waiting(provider)
        al.record(CONV, "app/x.py", _ok())
        await asyncio.sleep(0.3)
        pending_feedback_lists.setdefault(CONV, []).append(
            {"type": "feedback", "message": "skip the test file"})

        # The reply does not release the turn: the test diff is still awaited.
        await _until_calls(provider, 2)
        await asyncio.sleep(1.0)
        assert len(provider.calls) == 2, "resumed with the test diff still unapplied"
        al.record(CONV, "tests/test_x.py", _ok(("1",)))

    provider, events = await _run([_diffs_then_end(), _final(), _final()], user,
                                  hold_for_diff_apply=True)
    assert len(provider.calls) == 3
    last = _user_texts(provider.calls[1])[-1]
    assert "skip the test file" in last
    assert "replied before applying everything" in last and "not_applied" in last
    outcomes = [e["outcome"] for e in _of(events, "diff_wait") if e["state"] == "done"]
    assert outcomes == ["feedback", "complete"]
    # The count shown while waiting does not grow across the reply: no diff
    # was added, so both holds are for the same two.
    waits = [e for e in _of(events, "diff_wait") if e["state"] == "waiting"]
    assert len(waits) >= 2 and {e["total"] for e in waits} == {2}, [e["total"] for e in waits]
    # The response is sent to the model once, not appended a second time
    # before the feedback.
    replies = [m for m in provider.calls[1] if m.get("role") == "assistant"
               and "Once both are applied" in str(m.get("content"))]
    assert len(replies) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kw", [{}, {"hold_for_diff_apply": True, "unattended": True},
                                {"hold_for_diff_apply": True, "is_delegate": True}])
async def test_not_held_without_opt_in_or_in_headless_runs(kw):
    provider, events = await _run([_diffs_then_end(), _final()], None, **kw)
    assert len(provider.calls) == 1
    assert not _of(events, "diff_wait")


@pytest.mark.asyncio
async def test_response_without_diffs_is_not_held():
    provider, events = await _run([_final(), _final()], None, hold_for_diff_apply=True)
    assert len(provider.calls) == 1 and not _of(events, "diff_wait")


# ── wiring ────────────────────────────────────────────────────────────────

def test_apply_route_records_results():
    from app.routes import diff_routes
    assert "apply_ledger" in inspect.getsource(diff_routes.apply_changes)


def test_web_chat_opts_in_and_forwards_the_event():
    import app.server as server
    src = inspect.getsource(server)
    assert "hold_for_diff_apply=True" in src
    assert "'diff_wait'" in src


# ── between turns ─────────────────────────────────────────────────────────
# Reported 2026-11-11: diffs applied after a turn had ended were invisible
# to the model, which told the user they were "still waiting to be applied".

def _conv(last_assistant):
    return [{"role": "user", "content": "Go"},
            {"role": "assistant", "content": last_assistant},
            {"role": "user", "content": [{"type": "text", "text": "next"}]}]


def test_next_turn_is_told_what_was_applied_after_the_turn_ended():
    assert al.begin_turn(CONV, _conv(RESPONSE)) is None          # nothing recorded yet
    al.record(CONV, "app/x.py", _ok())
    conv = _conv(RESPONSE)
    note = al.begin_turn(CONV, conv)
    assert note.startswith("[Diff apply status")
    assert "app/x.py" in note and "applied" in note and "2/2 hunks" in note
    assert "tests/test_x.py" in note and note.count("no apply recorded\n") == 1
    assert conv[-1]["content"][0]["text"] == "next" and conv[-1]["content"][-1]["text"] == note
    assert al.begin_turn(CONV, _conv("No diffs.")) is None        # told once


def test_results_the_hold_reported_are_not_repeated_between_turns():
    al.begin_turn(CONV, _conv(""))
    al.record(CONV, "app/x.py", _ok())
    al.record(CONV, "tests/test_x.py", _ok(("1",)))
    expected = al.expected_from_text(RESPONSE)["expected"]
    al.mark_state_reported(CONV, al.wait_state(CONV, expected, 0))
    assert al.begin_turn(CONV, _conv(RESPONSE + "\nAll applied; tests pass.")) is None


def test_an_apply_from_before_the_last_turn_does_not_count_for_its_diffs():
    al.record(CONV, "app/x.py", _ok())
    assert "app/x.py" in al.begin_turn(CONV, _conv(""))
    note = al.begin_turn(CONV, _conv(RESPONSE))
    assert note.count("no apply recorded\n") == 2


def test_note_goes_on_a_string_message_and_is_kept_when_it_cannot_be_delivered():
    al.record(CONV, "app/x.py", _ok())
    conv = [{"role": "user", "content": "next"}]
    note = al.begin_turn(CONV, conv)
    assert conv[-1]["content"] == "next\n\n" + note
    al.record(CONV, "app/x.py", _partial())
    assert al.begin_turn(CONV, [{"role": "assistant", "content": "x"}]) is None
    # Not delivered, so not marked as shown: the next turn still gets it.
    later = al.begin_turn(CONV, [{"role": "user", "content": "next"}])
    assert "partial" in later and "context mismatch" in later


@pytest.mark.asyncio
async def test_the_real_loop_sends_the_note_only_on_the_web_chat():
    al.record(CONV, "app/x.py", _ok())
    provider, events = await _run([_final()], None, hold_for_diff_apply=True)
    sent = _user_texts(provider.calls[0])[-1]
    assert "Go" in sent and "[Diff apply status" in sent and "app/x.py" in sent
    assert not _of(events, "diff_wait")
    # Surfaces without Apply buttons get no note.
    al.record(CONV, "app/x.py", _ok())
    provider, _ = await _run([_final()], None)
    assert "[Diff apply status" not in _user_texts(provider.calls[0])[-1]


# ── which diff an apply belongs to ────────────────────────────────────────
# Matching by path alone let an old diff for a file stand in for a newer one.

# Hunk headers below are built from _AT: a literal one on a changed line of
# this file is read as the start of a new hunk by some diff tooling.
_AT = "@" * 2
DIFF_X_OLD = DIFF_X.replace("+a = 2", "+a = 3")


def _body(fence):
    """The diff text inside a fence."""
    return fence.split("\n", 1)[1][:-4]


def _as_sent(fence):
    """The fence's diff as the Apply button sends it: trimmed, hunk header rewritten."""
    return _body(fence).replace(f"{_AT} -1,2 +1,2 {_AT}", f"{_AT} -3,2 +3,2 {_AT}").strip()


def test_diff_key_survives_what_the_apply_button_changes_and_tells_diffs_apart():
    key = al.diff_key(_body(DIFF_X))
    assert key and key == al.diff_key(_as_sent(DIFF_X))
    assert key == al.expected_from_text(DIFF_X)["expected"][0]["keys"][0]
    assert key != al.diff_key(_body(DIFF_X_OLD))
    unchanged = "\n".join(["-" * 3 + " a/x", "+" * 3 + " b/x", f"{_AT} -1 +1 {_AT}", " unchanged", ""])
    assert al.diff_key(unchanged) is None
    assert al.diff_key(None) is None


def test_an_apply_whose_text_matches_no_fence_still_counts_by_path():
    exp = al.expected_from_text(TEXT)["expected"]
    al.record(CONV, "app/x.py", _ok(), diff=f"{_AT} -1 +1 {_AT}\n-merged\n+by the client\n")
    assert "app/x.py" not in al.wait_state(CONV, exp, 0, set())["remaining"]


@pytest.mark.asyncio
async def test_hold_ignores_an_apply_of_an_older_diff_for_the_same_file():
    history = [{"role": "user", "content": [{"type": "text", "text": "Go"}]},
               {"role": "assistant", "content": "First try:\n\n" + DIFF_X_OLD + "\nApply it.\n"},
               {"role": "user", "content": [{"type": "text", "text": "Try again"}]}]
    script = [[TextDelta(content="Second try:\n\n" + DIFF_X + "\nApply it.\n"),
               StreamEnd(stop_reason="end_turn")], _final()]

    async def user(provider):
        await _until_waiting(provider)
        al.record(CONV, "app/x.py", _ok(), diff=_as_sent(DIFF_X_OLD))
        await asyncio.sleep(1.2)
        assert len(provider.calls) == 1, "an older diff for the file ended the hold"
        al.record(CONV, "app/x.py", _ok(), diff=_as_sent(DIFF_X))      # positive control

    provider, events = await _run(script, user, history=history, hold_for_diff_apply=True)
    assert len(provider.calls) == 2
    report = _user_texts(provider.calls[1])[-1]
    assert "[Diff apply results]" in report and "were applied" in report
    assert _of(events, "diff_wait")[-1]["outcome"] == "complete"


def test_a_late_apply_of_an_older_diff_is_labelled_and_does_not_cover_the_new_one():
    conv = [{"role": "user", "content": "Go"},
            {"role": "assistant", "content": "First try:\n\n" + DIFF_X_OLD},
            {"role": "user", "content": "Try again"},
            {"role": "assistant", "content": RESPONSE},
            {"role": "user", "content": "next"}]
    assert al.begin_turn(CONV, copy.deepcopy(conv)) is None
    al.record(CONV, "app/x.py", _ok(), diff=_as_sent(DIFF_X_OLD))
    note = al.begin_turn(CONV, copy.deepcopy(conv))
    assert "applied" in note and "[a diff from 2 responses back]" in note
    assert note.count("no apply recorded\n") == 2          # x.py's new diff and test_x.py


def test_a_late_apply_of_the_last_responses_diff_is_not_labelled():
    conv = _conv(RESPONSE)
    al.begin_turn(CONV, copy.deepcopy(conv))
    al.record(CONV, "app/x.py", _ok(), diff=_as_sent(DIFF_X))
    note = al.begin_turn(CONV, copy.deepcopy(conv))
    assert "responses back" not in note
    assert note.count("no apply recorded\n") == 1          # only test_x.py


def test_apply_route_passes_the_applied_text():
    from app.routes import diff_routes
    assert "diff=target_diff" in inspect.getsource(diff_routes.apply_changes)


# ── continuation fences the client merges ─────────────────────────────────
# A headed diff followed by a headerless fence gets ONE Apply button; the
# client sends both as one diff (chainHeaderlessContinuationDiffs).

_TICKS = "`" * 3
CONT = f"{_AT} -20,1 +20,2 {_AT}\n d = 1\n+e = 1\n"


def _fence(body):
    return f"{_TICKS}diff\n{body}{_TICKS}\n"


def _merged(headed_fence, cont=CONT):
    """What the Apply button sends for a headed fence plus its continuation."""
    return _body(headed_fence).rstrip() + "\n" + cont


def test_a_headerless_continuation_fence_is_part_of_the_diff_before_it():
    text = "Fix:\n\n" + DIFF_X + "\nand further down:\n\n" + _fence(CONT) + "\nDone.\n"
    [e] = al.expected_from_text(text)["expected"]
    assert (e["path"], e["count"], e["hunks"]) == ("app/x.py", 1, 3)
    assert e["keys"] == [al.diff_key(_merged(DIFF_X))]
    assert e["keys"] != [al.diff_key(_body(DIFF_X))]


@pytest.mark.parametrize("cont, chained", [
    ("+f = 1\n+g = 1\n", True),                       # bare body, adds only
    (f"{_AT} def tail\n+f = 1\n", True),              # hint header, adds only
    (f"{_AT} def tail\n d = 1\n+e = 1\n", False),     # hint header with context
    ("-d = 1\n+d = 2\n", False),                      # bare body with a removal
])
def test_continuations_chain_under_the_clients_rule(cont, chained):
    [e] = al.expected_from_text(DIFF_X + "\n" + _fence(cont))["expected"]
    assert (e["keys"] == [al.diff_key(_merged(DIFF_X, cont))]) is chained
    assert (e["keys"] == [al.diff_key(_body(DIFF_X))]) is not chained


def test_an_older_merged_apply_does_not_stand_in_for_the_new_diff():
    history = [{"role": "assistant", "content": "First:\n\n" + DIFF_X_OLD + "\n" + _fence(CONT)}]
    foreign = al.keys_in(history)
    exp = al.expected_from_text(DIFF_X + "\n" + _fence(CONT))["expected"]
    al.record(CONV, "app/x.py", _ok(), diff=_merged(DIFF_X_OLD))
    assert al.wait_state(CONV, exp, 0, foreign)["remaining"] == ["app/x.py"]
    al.record(CONV, "app/x.py", _ok(), diff=_merged(DIFF_X))          # positive control
    assert al.wait_state(CONV, exp, 0, foreign)["complete"]


def test_a_continuation_does_not_chain_across_messages():
    history = [{"role": "assistant", "content": DIFF_X_OLD},
               {"role": "assistant", "content": _fence(CONT) + DIFF_Y}]
    assert al.diff_key(_body(DIFF_X_OLD)) in al.keys_in(history)


# ── a reply mid-wait keeps the unapplied diffs expected ───────────────────
# Reported 2026-11-11: eight diffs, the user replied before applying, the
# model answered with one corrected diff, and applying that one alone
# resumed the turn with seven still unapplied.

DIFF_X_NEW = DIFF_X.replace("+a = 2", "+a = 5")


def test_expected_for_turn_keeps_earlier_diffs_and_replaces_a_revised_file():
    got = al.expected_for_turn([TEXT, "Corrected:\n\n" + DIFF_X_NEW])
    by = {e["path"]: e for e in got["expected"]}
    assert set(by) == {"app/x.py", "tests/test_x.py"}
    assert by["app/x.py"]["count"] == 1
    assert by["app/x.py"]["keys"] == [al.diff_key(_body(DIFF_X_NEW))]
    assert got["superseded"] == {al.diff_key(_body(DIFF_X))}
    # One segment behaves as expected_from_text does.
    one = al.expected_for_turn([TEXT])
    assert one["expected"] == al.expected_from_text(TEXT)["expected"] and one["superseded"] == set()
    # The same fence repeated after the reply is not its own replacement.
    assert al.expected_for_turn([TEXT, DIFF_X])["superseded"] == set()


def test_an_apply_of_the_replaced_diff_does_not_stand_in_for_the_revision():
    got = al.expected_for_turn([TEXT, DIFF_X_NEW])
    al.record(CONV, "app/x.py", _ok(), diff=_as_sent(DIFF_X))
    st = al.wait_state(CONV, got["expected"], 0, got["superseded"])
    assert "app/x.py" in st["remaining"]
    al.record(CONV, "app/x.py", _ok(), diff=_as_sent(DIFF_X_NEW))     # positive control
    assert "app/x.py" not in al.wait_state(CONV, got["expected"], 0, got["superseded"])["remaining"]


# ── a diff revised within one response is not awaited ─────────────────────
# Reported 2026-11-17: the model wrote a diff, then a corrected version of
# it further down the same response.  The UI faded the first as "Superseded
# by a later revision below"; the chip still said "Waiting for 2 of 2 diffs"
# and the turn resumed only when both versions had been applied.

REVISED = ("First attempt:\n\n" + DIFF_X_OLD + "\nThat anchors on the wrong value. Corrected:\n\n"
           + DIFF_X + "\nand its test:\n\n" + DIFF_Y + "\nApply both.\n")


def test_a_fence_revised_later_in_the_same_response_is_not_expected():
    got = al.expected_from_text(REVISED)
    by = {e["path"]: e for e in got["expected"]}
    assert by["app/x.py"]["count"] == 1
    assert by["app/x.py"]["keys"] == [al.diff_key(_body(DIFF_X))]
    assert got["superseded"] == {al.diff_key(_body(DIFF_X_OLD))}
    assert by["tests/test_x.py"]["count"] == 1                        # the rest is untouched
    turn = al.expected_for_turn([REVISED])
    assert turn["expected"] == got["expected"] and turn["superseded"] == got["superseded"]
    assert al.chip_view(al.wait_state(CONV, turn["expected"], 0, turn["superseded"]))["total"] == 2


def test_an_apply_of_the_faded_version_does_not_stand_in_for_the_live_one():
    turn = al.expected_for_turn([REVISED])
    al.record(CONV, "app/x.py", _ok(), diff=_as_sent(DIFF_X_OLD))
    assert "app/x.py" in al.wait_state(CONV, turn["expected"], 0, turn["superseded"])["remaining"]
    al.record(CONV, "app/x.py", _ok(), diff=_as_sent(DIFF_X))         # positive control
    assert "app/x.py" not in al.wait_state(CONV, turn["expected"], 0, turn["superseded"])["remaining"]


def test_the_next_turns_note_does_not_list_the_faded_version_as_unapplied():
    conv = _conv(REVISED)
    al.begin_turn(CONV, copy.deepcopy(conv))
    al.record(CONV, "app/x.py", _ok(), diff=_as_sent(DIFF_X))
    note = al.begin_turn(CONV, copy.deepcopy(conv))
    assert note.count("no apply recorded\n") == 1                     # only test_x.py
    # Applying the faded version instead leaves the live one unapplied.
    al.reset_for_tests()
    al.begin_turn(CONV, copy.deepcopy(conv))
    al.record(CONV, "app/x.py", _ok(), diff=_as_sent(DIFF_X_OLD))
    assert al.begin_turn(CONV, copy.deepcopy(conv)).count("no apply recorded\n") == 2


@pytest.mark.parametrize("second, replaced", [
    (f"{_AT} def load\n-a = 1\n+a = 7\n+b = 7\n c = 1\n", True),     # same section hint: a revision
    (f"{_AT} def save\n-q = 1\n+q = 7\n+r = 7\n s = 1\n", False),    # another section: its own edit
    (f"{_AT} def load\n-a = 1\n+a = 7\n c = 1\n", True),              # a one-line revision
])
def test_hint_only_hunk_headers_are_compared_by_their_section(second, replaced):
    head = "diff --git a/app/x.py b/app/x.py\n--- a/app/x.py\n+++ b/app/x.py\n"
    first = head + f"{_AT} def load\n-a = 1\n+a = 2\n+b = 2\n c = 1\n"
    [e] = al.expected_from_text(_fence(first) + "\nThen:\n\n" + _fence(head + second))["expected"]
    assert e["count"] == (1 if replaced else 2)


@pytest.mark.asyncio
async def test_hold_resumes_once_the_live_version_is_applied():
    script = [[TextDelta(content=REVISED), StreamEnd(stop_reason="end_turn")], _final()]

    async def user(provider):
        await _until_waiting(provider)
        al.record(CONV, "tests/test_x.py", _ok(("1",)))
        al.record(CONV, "app/x.py", _ok(), diff=_as_sent(DIFF_X_OLD))   # the faded one
        await asyncio.sleep(1.2)
        assert len(provider.calls) == 1, "the faded version ended the hold"
        al.record(CONV, "app/x.py", _ok(), diff=_as_sent(DIFF_X))

    provider, events = await _run(script, user, hold_for_diff_apply=True)
    assert len(provider.calls) == 2, "still waiting on the superseded version"
    waits = _of(events, "diff_wait")
    assert waits[0]["state"] == "waiting" and waits[0]["total"] == 2    # x.py once, test_x.py
    assert waits[-1]["outcome"] == "complete"
    report = _user_texts(provider.calls[1])[-1]
    assert "were applied" in report and "not_applied" not in report


async def _until_calls(provider, n):
    for _ in range(300):
        if len(provider.calls) >= n:
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.3)


@pytest.mark.asyncio
async def test_after_a_reply_the_hold_still_waits_for_the_earlier_diffs():
    revised = [TextDelta(content="Corrected:\n\n" + DIFF_X_NEW + "\nApply this one instead.\n"),
               StreamEnd(stop_reason="end_turn")]

    async def user(provider):
        from app.server import pending_feedback_lists
        await _until_waiting(provider)
        pending_feedback_lists.setdefault(CONV, []).append(
            {"type": "feedback", "message": "check diff one first"})
        await _until_calls(provider, 2)
        assert len(provider.calls) == 2, "the reply did not reach the model"
        al.record(CONV, "app/x.py", _ok(), diff=_as_sent(DIFF_X_NEW))
        await asyncio.sleep(1.2)
        assert len(provider.calls) == 2, "resumed with the earlier test diff still unapplied"
        al.record(CONV, "tests/test_x.py", _ok(("1",)))               # positive control

    provider, events = await _run([_diffs_then_end(), revised, _final()], user,
                                  hold_for_diff_apply=True)
    assert len(provider.calls) == 3
    report = _user_texts(provider.calls[2])[-1]
    assert "were applied" in report and "tests/test_x.py" in report and "not_applied" not in report
    waits = [e for e in _of(events, "diff_wait") if e["state"] == "waiting"]
    assert waits[-1]["total"] == 2          # both files, not just the revised one
    assert _of(events, "diff_wait")[-1]["outcome"] == "complete"


@pytest.mark.asyncio
async def test_an_apply_made_before_the_reply_still_counts_after_it():
    async def user(provider):
        from app.server import pending_feedback_lists
        await _until_waiting(provider)
        al.record(CONV, "app/x.py", _ok())
        await asyncio.sleep(0.3)
        pending_feedback_lists.setdefault(CONV, []).append(
            {"type": "feedback", "message": "is the test right?"})
        await _until_calls(provider, 2)
        await asyncio.sleep(1.0)
        assert len(provider.calls) == 2, "ended without the test diff applied"
        al.record(CONV, "tests/test_x.py", _ok(("1",)))

    answer = [TextDelta(content="Yes, it is."), StreamEnd(stop_reason="end_turn")]
    provider, events = await _run([_diffs_then_end(), answer, _final()], user,
                                  hold_for_diff_apply=True)
    assert len(provider.calls) == 3
    assert _of(events, "diff_wait")[-1]["outcome"] == "complete"


@pytest.mark.asyncio
async def test_resuming_after_the_hold_shows_the_waiting_for_model_spinner():
    # The resume notice is a text chunk, which the client takes as "the model
    # is streaming" and sets the state to idle; the state event must come
    # after it or the spinner stays hidden until the model's first token.
    async def user(provider):
        await _until_waiting(provider)
        al.record(CONV, "app/x.py", _ok())
        al.record(CONV, "tests/test_x.py", _ok(("1",)))

    provider, events = await _run([_diffs_then_end(), _final()], user, hold_for_diff_apply=True)
    assert len(provider.calls) == 2
    notice = next(i for i, e in enumerate(events)
                  if e.get("type") == "text" and "All diffs applied" in e.get("content", ""))
    after = events[notice + 1:]
    nxt = next((e for e in after if e.get("type") in ("text", "processing_state")), None)
    assert nxt is not None and nxt.get("type") == "processing_state"
    assert nxt.get("state") == "awaiting_model_response"
