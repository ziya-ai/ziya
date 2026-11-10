"""A diff whose path is relative to the server's launch directory, not the project.

Observed 2026-11-09 (chat f48b3ae9): four diffs for
``workspace/KuiperEAR-switch-mgr/src/hal/hal.cpp`` were rejected with "the
target file does not exist".  The shell tool runs in the server's launch
directory (/Users/dcohn), where that path exists; the chat request's project
root was ~/workspace/ziya-0.4.0.1, where it does not.  The validator checked
the project root (correct) but the hook then READ the file from
ZIYA_USER_CODEBASE_DIR (the launch dir) and injected it as "current content
for diff correction", so the model was told its path was fine and re-emitted
the same four diffs verbatim.

The hook now reads from the same root the validator checked, and the
missing-target feedback names that root and, when the path resolves under
the launch directory instead, says whether the file is inside the project
under another relative path or outside it altogether.
"""

import os

import pytest

from app.context import get_project_root_or_none, set_project_root
from app.utils.diff_utils.validation.pipeline_validator import (
    missing_target_feedback,
    validate_diff_with_full_pipeline,
)
from app.utils.diff_validation_hook import DiffValidationHook

REL = "workspace/switch-mgr/src/hal/hal.cpp"
BODY = "int delta = 500;\nint kvDelta = 4000;\nint other = 1;\n"


def _patch(rel):
    return (
        f"diff --git a/{rel} b/{rel}\n--- a/{rel}\n+++ b/{rel}\n"
        "@@ -1,3 +1,3 @@\n int delta = 500;\n-int kvDelta = 4000;\n+int kvDelta = 5000;\n int other = 1;\n"
    )


def _fence(rel):
    return "```diff\n" + _patch(rel) + "```\n"


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """launch/ holds the file; proj/ (the project root) does not."""
    launch = tmp_path / "launch"
    proj = tmp_path / "proj"
    (launch / os.path.dirname(REL)).mkdir(parents=True)
    (launch / REL).write_text(BODY)
    proj.mkdir()
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(launch))
    monkeypatch.chdir(launch)
    saved = get_project_root_or_none()
    set_project_root(str(proj))
    try:
        yield launch, proj
    finally:
        if saved is not None:
            set_project_root(saved)


def test_validator_names_the_root_and_the_launch_dir_resolution(roots):
    launch, proj = roots
    res = validate_diff_with_full_pipeline(_patch(REL), REL)
    assert res["status"] == "error" and res["total_hunks"] == 0
    fb = res["model_feedback"]
    assert "does not exist" in fb            # reader_reason keys on this
    assert str(proj) in fb                   # which root was checked
    assert str(launch) in fb                 # where the path really resolves
    assert "outside the project" in fb
    assert "mark as new file creation" not in fb


def test_validator_gives_the_project_relative_path_when_the_file_is_inside(tmp_path, monkeypatch):
    # Launch dir is the PARENT of the project: the model wrote "proj/src/a.py".
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "a.py").write_text("x = 1\n")
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    fb = missing_target_feedback("proj/src/a.py", str(proj))
    assert "Inside the project the same file is src/a.py" in fb
    assert "outside the project" not in fb


def test_plain_typo_keeps_the_new_file_hint(roots):
    launch, proj = roots
    fb = missing_target_feedback("nowhere/at/all.py", str(proj))
    assert str(proj) in fb
    assert "mark as new file creation" in fb
    assert str(launch) not in fb


def test_hook_does_not_inject_a_file_from_the_launch_dir(roots):
    launch, proj = roots
    hook = DiffValidationHook(enabled=True, auto_regenerate=True)
    # Seam: the hook's reader resolves against the validator's root.
    assert hook.read_file_for_context(REL) is None

    messages = []
    feedback = hook.validate_and_enhance(_fence(REL), model_messages=messages, send_event=None)
    assert hook.last_rejected and hook.last_rejected[0]["reason"] == "the target file does not exist"
    assert messages == [], "no 'current content' may be injected from a different root"
    assert REL not in hook.added_files
    assert feedback is not None and "outside the project" in feedback


def test_hook_positive_control_reads_from_the_project_root(roots):
    launch, proj = roots
    (proj / os.path.dirname(REL)).mkdir(parents=True)
    (proj / REL).write_text("int delta = 500;\nint kvDelta = 4000;\nint other = 1;\n// project copy\n")
    hook = DiffValidationHook(enabled=True, auto_regenerate=True)
    # Both roots hold the file; the project's copy is the one read.
    assert "project copy" in (hook.read_file_for_context(REL) or "")

    messages = []
    feedback = hook.validate_and_enhance(_fence(REL), model_messages=messages, send_event=None)
    assert feedback is None
    assert hook.successful_diffs == [REL]
