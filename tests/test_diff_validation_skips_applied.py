"""End-of-stream diff validation must not re-judge a diff the user has
already put through the Apply button.

Pattern found 2026-11-14 across recent conversations (8f0cb7ab, 25017168,
3260fc6a, a6e7164c): a response ends with diffs, the diff-apply hold keeps
the stream open, the user applies them ("All diffs applied -- continuing"),
the model runs tests and often writes a second diff for the same file, which
is applied too.  Only then does stream_with_tools return and server.py run
DiffValidationHook over the WHOLE accumulated text, dry-running every fence
against the file as it is now.  The first fence's removed lines are gone, so
it is reported "did not apply cleanly", marked disabled in the transcript,
and a correction pass is started whose only possible answer is "the file
already contains the change".

The hook now asks the apply ledger first (apply_ledger.settled_parts).

Every "is not rejected" assertion is paired with a control showing the same
fence IS rejected without the ledger entry, so the test fails on the old code.
"""
import pytest

from app.utils import apply_ledger
from app.utils.diff_validation_hook import DiffValidationHook

CONV = "conv-applied-skip"
REL = "pkg/mod.py"
OTHER = "pkg/other.py"

# The file after diff 1 was applied and a later diff (diff 2) rewrote the
# same function again: diff 1's removed lines are gone and its added lines
# are gone too, so it neither applies nor reads as already applied.  This is
# the shape of the traced turn (moveChat: timedFetchOk -> res.json() ->
# timedFetchJson).
MOVED_ON = (
    "def move(src, dst):\n"
    "    try:\n"
    "        body = fetch_json(src, dst)\n"
    "        return {'status': 'moved', 'note': body.get('note')}\n"
    "    except HttpError as e:\n"
    "        if e.status == 404:\n"
    "            return {'status': 'absent'}\n"
    "        raise\n"
    "\n"
    "\n"
    "def unrelated():\n"
    "    return 1\n"
)

_HDR = f"diff --git a/{REL} b/{REL}\n--- a/{REL}\n+++ b/{REL}\n"
D1 = (_HDR + "@@ -1,6 +1,8 @@\n"
      " def move(src, dst):\n"
      "     res = fetch_ok(src, dst)\n"
      "-    if res.ok:\n"
      "-        return 'moved'\n"
      "-    if res.status == 404:\n"
      "-        return 'absent'\n"
      "+    if res.ok:\n"
      "+        body = res.json()\n"
      "+        return {'status': 'moved', 'note': body.get('note')}\n"
      "+    if res.status == 404:\n"
      "+        return {'status': 'absent'}\n"
      "     raise HttpError(res.status)")
D2 = (_HDR + "@@ -1,8 +1,8 @@\n"
      " def move(src, dst):\n"
      "-    res = fetch_ok(src, dst)\n"
      "-    if res.ok:\n"
      "-        body = res.json()\n"
      "-        return {'status': 'moved', 'note': body.get('note')}\n"
      "-    if res.status == 404:\n"
      "-        return {'status': 'absent'}\n"
      "-    raise HttpError(res.status)\n"
      "+    try:\n"
      "+        body = fetch_json(src, dst)\n"
      "+        return {'status': 'moved', 'note': body.get('note')}\n"
      "+    except HttpError as e:\n"
      "+        if e.status == 404:\n"
      "+            return {'status': 'absent'}\n"
      "+        raise")
# A fence for REL that was never applied and cannot apply.
D3_BROKEN = (_HDR + "@@ -40,3 +40,3 @@\n not in the file at all\n"
             "-nothing like this = 2\n+y = 3\n nor is this line")
BROKEN_OTHER = (f"diff --git a/{OTHER} b/{OTHER}\n--- a/{OTHER}\n+++ b/{OTHER}\n"
                "@@ -40,3 +40,3 @@\n not in the file at all\n-nothing like this = 2\n+y = 3\n nor is this line")


def fence(body):
    return f"```diff\n{body}\n```\n"


@pytest.fixture
def proj(tmp_path, monkeypatch):
    (tmp_path / "pkg").mkdir()
    (tmp_path / REL).write_text(MOVED_ON)
    (tmp_path / OTHER).write_text("x = 1\ny = 2\nz = 3\n")
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))
    from app.context import set_project_root
    set_project_root(str(tmp_path))
    apply_ledger.reset_for_tests()
    yield tmp_path
    apply_ledger.reset_for_tests()


def run(content, conversation_id=CONV):
    hook = DiffValidationHook(enabled=True, auto_regenerate=True, conversation_id=conversation_id)
    feedback = hook.validate_and_enhance(content, model_messages=[], send_event=None)
    return hook, feedback


def rejected(hook):
    return [r["file_path"] for r in hook.last_rejected]


def test_control_without_a_ledger_entry_the_stale_fence_is_rejected(proj):
    hook, feedback = run(fence(D1))
    assert rejected(hook) == [REL] and feedback
    assert hook.settled_diffs == []


def test_an_applied_diff_is_not_validated_against_the_file_it_changed(proj):
    apply_ledger.record(CONV, REL, {"status": "success"}, diff=D1)
    hook, feedback = run("first:\n\n" + fence(D1) + "\n**✅ All diffs applied — continuing.**\n")
    assert feedback is None and rejected(hook) == []
    assert hook.successful_diffs == [REL]
    assert [(s["file_path"], s["reason"]) for s in hook.settled_diffs] == [(REL, "applied")]
    # The marker the frontend disables a fence by is the body hash; none issued.
    assert hook.failed_diff_details == []


def test_the_traced_turn_two_applied_diffs_for_one_file(proj):
    """8f0cb7ab: diff 7 applied, tests run, fix diff for the same file applied."""
    apply_ledger.record(CONV, REL, {"status": "success"}, diff=D1)
    apply_ledger.record(CONV, REL, {"status": "success"}, diff=D2)
    hook, feedback = run(fence(D1) + "\ntests failed; fix:\n\n" + fence(D2))
    assert feedback is None and rejected(hook) == []
    assert len(hook.settled_diffs) == 2


def test_the_apply_button_text_is_matched_despite_rewritten_headers(proj):
    """The client trims the text and rewrites hunk headers before sending."""
    sent = D1.replace("@@ -1,6 +1,8 @@", "@@ -1,7 +1,9 @@") + "\n"
    assert sent != D1 + "\n"
    apply_ledger.record(CONV, "./" + REL, {"status": "success"}, diff=sent)
    hook, feedback = run(fence(D1))
    assert feedback is None and rejected(hook) == []


def test_an_unapplied_broken_diff_is_still_rejected_beside_an_applied_one(proj):
    apply_ledger.record(CONV, REL, {"status": "success"}, diff=D1)
    hook, feedback = run(fence(D1) + "\n" + fence(BROKEN_OTHER))
    assert rejected(hook) == [OTHER]
    assert feedback and OTHER in feedback
    assert REL in hook.successful_diffs


def test_another_conversations_applies_do_not_count(proj):
    apply_ledger.record("someone-else", REL, {"status": "success"}, diff=D1)
    hook, _ = run(fence(D1))
    assert rejected(hook) == [REL]


def test_an_earlier_fence_overtaken_by_an_applied_later_one_is_not_rejected(proj):
    """The model revised its diff after a reply; only the revision was applied."""
    apply_ledger.record(CONV, REL, {"status": "success"}, diff=D2)
    hook, feedback = run(fence(D1) + "\nrevised:\n\n" + fence(D2))
    assert feedback is None and rejected(hook) == []
    assert sorted(s["reason"] for s in hook.settled_diffs) == ["applied", "superseded"]
    # Superseded is not a success claim.
    assert hook.successful_diffs == [REL]


def test_a_later_unapplied_fence_is_still_validated(proj):
    """Only fences BEFORE the last applied one are overtaken."""
    apply_ledger.record(CONV, REL, {"status": "success"}, diff=D2)
    hook, _ = run(fence(D2) + "\n" + fence(D3_BROKEN))
    assert rejected(hook) == [REL]
    assert [s["reason"] for s in hook.settled_diffs] == ["applied"]


def test_a_failed_apply_is_not_reported_a_second_time(proj):
    """The hold already handed the model the failure and it has answered."""
    apply_ledger.record(CONV, REL, {"status": "error", "message": "nope"}, diff=D1)
    hook, feedback = run(fence(D1))
    assert feedback is None and rejected(hook) == []
    assert hook.successful_diffs == []
    assert hook.settled_diffs[0]["reason"] == "failed"


def test_conversation_id_is_taken_from_the_request_context(proj):
    """server.stream_chunks builds the hook without an id and sets the
    context var; validation runs in a worker thread that copies it."""
    import contextvars
    from app.context import set_conversation_id
    apply_ledger.record(CONV, REL, {"status": "success"}, diff=D1)

    def in_request():
        set_conversation_id(CONV)
        import concurrent.futures
        ctx = contextvars.copy_context()
        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            return ex.submit(ctx.run, run, fence(D1), None).result()

    hook, feedback = contextvars.copy_context().run(in_request)
    assert feedback is None and rejected(hook) == []


# ── apply_ledger.settled_parts ────────────────────────────────────────────

def _k(body):
    return (REL, apply_ledger.diff_key(body))


def test_settled_parts_merged_continuation_fence(proj):
    """The client sends a headed fence and its headerless continuation as
    one diff; the headed part must still be recognised on its own."""
    cont = "@@ -11,2 +13,3 @@\n def unrelated():\n     return 1\n+# tail"
    text = fence(D1) + "\n" + fence(cont)
    assert apply_ledger.settled_parts(CONV, text) == {}
    apply_ledger.record(CONV, REL, {"status": "success"}, diff=D1 + "\n" + cont)
    assert apply_ledger.settled_parts(CONV, text) == {_k(D1): "applied"}


def test_settled_parts_loose_path_match_needs_a_known_turn_start(proj):
    apply_ledger.record(CONV, REL, {"status": "success"}, diff="+unrelated text\n")
    assert apply_ledger.settled_parts(CONV, fence(D1)) == {}       # turn start unknown
    apply_ledger.reset_for_tests()
    apply_ledger.begin_turn(CONV, [{"role": "user", "content": "go"}])
    assert apply_ledger.turn_start_seq(CONV) == 0
    apply_ledger.record(CONV, REL, {"status": "partial"}, diff="+unrelated text\n")
    assert apply_ledger.settled_parts(CONV, fence(D1)) == {_k(D1): "partial"}


def test_settled_parts_newest_result_for_a_key_wins_and_bad_input_is_empty(proj):
    apply_ledger.record(CONV, REL, {"status": "error"}, diff=D1)
    apply_ledger.record(CONV, REL, {"status": "success"}, diff=D1)
    assert apply_ledger.settled_parts(CONV, fence(D1)) == {_k(D1): "applied"}
    assert apply_ledger.settled_parts(None, fence(D1)) == {}
    assert apply_ledger.settled_parts(CONV, "") == {}
    assert apply_ledger.settled_parts(CONV, "no fences here") == {}
