"""
Regression test: the zero-hunks early-return diagnostic must reach the model.

Background
----------
When a diff is emitted with a bare "@@" hunk header (no "-n,m +n,m" line
numbers), the diff pipeline bails out early at pipeline_manager with:

    "Diff parsed to zero hunks despite containing N change line(s) —
     malformed or missing hunk header (expected '@@ -n,m +n,m @@')."

That early return never builds any hunk objects, so
validate_diff_with_full_pipeline saw an EMPTY failed_hunks list, which made
has_any_failure False, which meant format_model_feedback was never called and
result["model_feedback"] stayed "".  The diagnostic was logged to stderr but
never placed in model_feedback, so the regeneration prompt handed the model an
empty "Problem:" section.  The model literally responded "tell me the exact
validation error message" because it never saw one.

The fix adds an `elif status == "error" and total_hunks == 0` branch that
surfaces the pipeline's own `error` string into model_feedback.

These tests drive the REAL validate_diff_with_full_pipeline (not a mock) and
assert that:
  1. The diagnostic is surfaced (model_feedback is non-empty and mentions the
     expected hunk-header format).  -> guards the fix.
  2. A well-formed-but-non-matching diff still produces feedback through the
     normal failed-hunks path.  -> guards against the elif shadowing that path.
"""

import os
import pytest

from app.utils.diff_utils.validation.pipeline_validator import (
    validate_diff_with_full_pipeline,
)


@pytest.fixture
def codebase_with_file(tmp_path, monkeypatch):
    """Point the validator's project root at a temp dir holding a real file.

    validate_diff_with_full_pipeline resolves the target file via
    get_project_root_or_none() (falling back to ZIYA_USER_CODEBASE_DIR), then
    copies it into its own temp dir before running the pipeline.  We set both
    the ContextVar and the env var so resolution succeeds regardless of which
    path is taken, and restore the ContextVar afterwards.
    """
    target_name = "sample.py"
    (tmp_path / target_name).write_text(
        "def greet():\n    return 'hello'\n"
    )

    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))

    from app.context import set_project_root, get_project_root
    saved_root = None
    try:
        saved_root = get_project_root()
    except Exception:
        saved_root = None
    set_project_root(str(tmp_path))
    try:
        yield target_name
    finally:
        if saved_root is not None:
            set_project_root(saved_root)


def test_zero_hunks_surfaces_diagnostic(codebase_with_file):
    """A diff that parses to zero hunks must surface a non-empty diagnostic.

    This is the priority fix.  The trigger here is a diff with change lines but
    NO '@@' header line at all: the bare-'@@' case is now repaired upstream by
    the preprocessor (see test_bare_hunk_header_now_applies), so we use the
    headerless input that genuinely still reaches the zero-hunks early return.

    The original bug: zero hunks parsed -> empty failed_hunks -> has_any_failure
    False -> format_model_feedback never called -> model_feedback stayed "".
    The diagnostic only reached stderr; the model got an empty "Problem:".
    The fix adds an `elif status == "error" and total_hunks == 0` branch that
    surfaces the pipeline's own error string instead.
    """
    target = codebase_with_file
    headerless_diff = (
        f"diff --git a/{target} b/{target}\n"
        f"--- a/{target}\n"
        f"+++ b/{target}\n"
        # No '@@' line at all -> nothing for the preprocessor to synthesize,
        # so this still parses to zero hunks.
        "-    return 'hello'\n"
        "+    return 'hi'\n"
    )

    result = validate_diff_with_full_pipeline(headerless_diff, file_path=target)

    # The pipeline must classify this as an unusable diff with no hunks...
    assert result["status"] == "error"
    assert result["total_hunks"] == 0

    # ...and crucially, the model must be told WHY, not handed an empty string.
    feedback = result["model_feedback"]
    assert feedback, (
        "model_feedback was empty — the zero-hunks diagnostic did not reach "
        "the model (this is the exact bug the fix addresses)."
    )
    # The surfaced text should mention the expected hunk-header format so the
    # model knows what to correct.
    assert "@@" in feedback
    assert "hunk header" in feedback.lower() or "hunks" in feedback.lower()


def test_bare_hunk_header_now_applies(codebase_with_file):
    """Option A: a bare '@@' header must now be repaired and APPLY cleanly.

    Previously a bare '@@' (an '@@' line with no '-n,m +n,m' range) parsed to
    zero hunks and was rejected.  The preprocessor now synthesizes a
    'ZIYA_NOPOS' placeholder header so the hunk is located by context and
    applied.  This drives the real production apply entry to prove the diff
    lands in the file rather than merely validating.
    """
    import os
    from app.context import get_project_root
    from app.utils.code_util import use_git_to_apply_code_diff

    target = codebase_with_file
    root = get_project_root()
    abs_target = os.path.join(root, target)

    bare_diff = (
        f"diff --git a/{target} b/{target}\n"
        f"--- a/{target}\n"
        f"+++ b/{target}\n"
        "@@\n"                       # <-- bare header, no -n,m +n,m
        "-    return 'hello'\n"
        "+    return 'hi'\n"
    )

    result = use_git_to_apply_code_diff(bare_diff, abs_target)
    status = result.get("status") if isinstance(result, dict) else result
    assert status == "success", f"bare-@@ diff did not apply (status={status!r})"

    with open(abs_target) as fh:
        contents = fh.read()
    assert "return 'hi'" in contents
    assert "return 'hello'" not in contents


def test_wellformed_nonmatching_diff_still_gives_feedback(codebase_with_file):
    """Guard: the new elif must not shadow the normal failed-hunk feedback path.

    A diff with a proper '@@ -n,m +n,m @@' header whose context does not match
    the file should fail through the has_any_failure path and still produce
    non-empty feedback.
    """
    target = codebase_with_file
    nonmatching_diff = (
        f"diff --git a/{target} b/{target}\n"
        f"--- a/{target}\n"
        f"+++ b/{target}\n"
        "@@ -1,2 +1,2 @@\n"
        " def nonexistent_function():\n"      # context not present in file
        "-    return 'gone'\n"
        "+    return 'changed'\n"
    )

    result = validate_diff_with_full_pipeline(nonmatching_diff, file_path=target)

    # Either it fails to apply or reports an error; in both cases the model
    # should receive actionable feedback rather than an empty string.
    if not result["can_apply"]:
        assert result["model_feedback"], (
            "A non-matching well-formed diff produced no feedback — the model "
            "would have nothing to correct."
        )
