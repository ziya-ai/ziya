"""A multi-file diff fence is validated file by file, against each real path.

Observed 2026-10-03: one fence held diffs for app/config/write_policy.py,
app/mcp/tools/fileio.py and app/mcp_servers/write_policy.py.  The hook took
the FIRST file's path for the whole fence and ran the pipeline on a temp copy
named by basename.  The pipeline's basename fallback then merged both
``write_policy.py`` sections into one 6-hunk diff against the config file,
the mcp_servers hunks could not land, the post-apply language check rolled
the rest back, and the notice read "hunks 1-6 of 6 did not match".  The
frontend applies each file separately against its real path, so the user
saw every hunk apply.  fileio.py was never validated at all.

This is not an off-by-N tolerance gap; the validator was checking hunks
against the wrong file.
"""

import os

import pytest

from app.utils.diff_validation_hook import DiffValidationHook

A = "pkg/config/policy.py"
B = "pkg/servers/policy.py"
C = "pkg/tools/io.py"


def _lines(n, tag):
    return "".join(f"{tag}_{i} = {i}\n" for i in range(1, n + 1))


@pytest.fixture
def proj(tmp_path, monkeypatch):
    for rel, tag in ((A, "a"), (B, "b"), (C, "c")):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_lines(20, tag))
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path))
    from app.context import set_project_root
    set_project_root(str(tmp_path))
    return tmp_path


def _section(rel, tag, line, new, old=None):
    old = old if old is not None else f"{tag}_{line} = {line}"
    ctx_b = f"{tag}_{line - 1} = {line - 1}"
    ctx_a = f"{tag}_{line + 1} = {line + 1}"
    return (
        f"diff --git a/{rel} b/{rel}\n--- a/{rel}\n+++ b/{rel}\n"
        f"@@ -{line - 1},3 +{line - 1},3 @@\n"
        f" {ctx_b}\n-{old}\n+{new}\n {ctx_a}\n"
    )


def _bad_section(rel):
    """A hunk sharing no text with the file: no stage can place it."""
    return (
        f"diff --git a/{rel} b/{rel}\n--- a/{rel}\n+++ b/{rel}\n"
        "@@ -11,3 +11,3 @@\n"
        " class Unrelated(Exception):\n"
        "-    raise RuntimeError('never present')\n"
        "+    pass\n"
        "     # trailing comment absent everywhere\n"
    )


def _fence(*sections):
    return "```diff\n" + "".join(sections).rstrip("\n") + "\n```\n"


def _run(content):
    hook = DiffValidationHook(conversation_id="c1")
    msgs = []
    feedback = hook.validate_and_enhance(content, msgs)
    return hook, feedback


def test_same_basename_files_in_one_fence_validate_cleanly(proj):
    content = _fence(
        _section(A, "a", 5, "a_5 = 'changed'"),
        _section(C, "c", 8, "c_8 = 'changed'"),
        _section(B, "b", 12, "b_12 = 'changed'"),
    )
    hook, feedback = _run(content)
    assert feedback is None, feedback
    assert hook.last_rejected == []
    # Positive: every file in the fence was actually validated.
    assert sorted(set(hook.successful_diffs)) == sorted([A, B, C])


def test_bad_hunk_in_a_later_file_is_caught_and_named(proj):
    content = _fence(
        _section(A, "a", 5, "a_5 = 'changed'"),
        _bad_section(B),
    )
    hook, feedback = _run(content)
    assert feedback
    assert [r["file_path"] for r in hook.last_rejected] == [B]
    assert A in hook.successful_diffs


def test_rejection_carries_the_whole_fence_hash(proj):
    # The frontend marks a fence unappliable by its body hash, so a part's
    # rejection must carry the hash of the fence it came from.
    from app.utils.diff_validation_hook import diff_body_hash
    body = (
        _section(A, "a", 5, "a_5 = 'changed'")
        + _bad_section(B)
    ).rstrip("\n")
    hook, _ = _run("```diff\n" + body + "\n```\n")
    assert hook.last_rejected[0]["body_hash"] == diff_body_hash(body)


def test_off_by_n_header_still_passes(proj):
    # Control for the tolerance the prompt promises: a start line off by 3
    # with correct context is accepted.
    sec = _section(A, "a", 10, "a_10 = 'changed'").replace("@@ -9,3 +9,3 @@", "@@ -6,3 +6,3 @@")
    hook, feedback = _run(_fence(sec))
    assert feedback is None, feedback
    assert hook.successful_diffs == [A]
