"""A patch on a file that an earlier fence in the same response creates.

Observed 2026-10-07: a response emitted a new-file diff for
``app/jobs/policy.py`` and then a small ``@@`` patch on top of it.  The
patch was validated before the user applied the new-file diff, so the
target was missing and it was refused.  The refusal is correct: every fence
must apply on its own against the live file, and a patch stacked on an
unapplied new-file diff forces an apply order on the user.  What was wrong
was the advice.  The generic missing-target feedback says "mark as new file
creation", which here would recreate the file from a three-line fragment,
so the model re-emitted the same patch and the turn was wasted.

The hook now remembers the files this response creates and, for a later
patch on one of them, names the cause and the correction: one complete
new-file diff.  An ordinary missing target keeps the old wording.
"""

import pytest

from app.utils.diff_validation_hook import DiffValidationHook, reader_reason

NEW = "pkg/new_module.py"
OTHER = "pkg/never_existed.py"


@pytest.fixture
def proj(tmp_path, monkeypatch):
    (tmp_path / "pkg").mkdir()
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))
    from app.context import set_project_root
    set_project_root(str(tmp_path))
    return tmp_path


def _new_file_fence(rel):
    return (
        "```diff\n"
        f"diff --git a/dev/null b/{rel}\nnew file mode 100644\n--- /dev/null\n+++ b/{rel}\n"
        "@@ -0,0 +1,3 @@\n+x = 1\n+y = 2\n+z = 3\n"
        "```\n"
    )


def _patch_fence(rel):
    return (
        "```diff\n"
        f"diff --git a/{rel} b/{rel}\n--- a/{rel}\n+++ b/{rel}\n"
        "@@ -1,3 +1,3 @@\n x = 1\n-y = 2\n+y = 22\n z = 3\n"
        "```\n"
    )


def test_patch_on_same_response_new_file_names_the_cause_and_the_fix(proj):
    hook = DiffValidationHook(enabled=True, auto_regenerate=True)
    content = _new_file_fence(NEW) + "\nthen a fix:\n\n" + _patch_fence(NEW)
    feedback = hook.validate_and_enhance(content, model_messages=[], send_event=None)

    assert NEW in hook.created_in_response
    assert hook.successful_diffs == [NEW], "the new-file fence itself must validate"
    # The patch is still refused: nothing on disk, and fences apply independently.
    assert len(hook.last_rejected) == 1
    rej = hook.last_rejected[0]
    assert rej["file_path"] == NEW
    assert "earlier diff in this response" in rej["reason"]
    assert rej["reason"] != "the target file does not exist"
    # The model is told to re-emit the whole file, not to "mark as new file".
    assert feedback is not None
    assert "complete new-file diff" in feedback
    assert "mark as new file creation" not in feedback


def test_ordinary_missing_target_keeps_the_generic_reason(proj):
    hook = DiffValidationHook(enabled=True, auto_regenerate=True)
    feedback = hook.validate_and_enhance(_patch_fence(OTHER), model_messages=[], send_event=None)
    assert OTHER not in hook.created_in_response
    assert hook.last_rejected[0]["reason"] == "the target file does not exist"
    assert feedback is not None and "earlier diff in this response" not in feedback


def test_reader_reason_prefers_the_pending_flag():
    assert reader_reason({"failed_hunks": [], "total_hunks": 0, "pending_new_file": True,
                          "model_feedback": "does not exist"}).startswith(
        "the target file is created by an earlier diff")


def test_reset_forgets_created_files(proj):
    hook = DiffValidationHook(enabled=True)
    hook.validate_and_enhance(_new_file_fence(NEW), model_messages=[], send_event=None)
    assert NEW in hook.created_in_response
    hook.reset_validation_state()
    assert hook.created_in_response == set()
