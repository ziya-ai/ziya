"""Undo of a zero-context deletion must put the line back where it was.

THE DEFECT THIS EXISTS FOR
--------------------------
``git diff -U0`` writes a deletion of line 3 as ``@@ -3 +2,0 @@`` ("after new
line 2").  Apple ``patch -R`` (2.0-12u11) reverses that as an insertion one
line EARLY and exits 0.  ``/api/unapply-changes`` calls the reverse pipeline
with no ``expected_content``, so Stage 1's result is accepted unverified and
the one-click undo reports success on a wrong file.  The forward direction of
the same change (``@@ -2,0 +3 @@``) is placed correctly by ``patch``, so the
fix reverses the text ourselves and applies it forward.

The reverse fixture runner can never see this: it always passes
``expected_content=original``, so Stage 1's wrong output is rejected and a
later stage repairs it.  These tests call the pipeline the way the route does.

Also covered: ``reverse_diff`` left mixed-form headers (``-3 +2,0``) unswapped,
because its two regexes only knew ``-a,b +c,d`` and ``-a +c``.
"""
import os
import shutil
import tempfile

import pytest

from app.utils.diff_utils.core.diff_reverser import reverse_diff

ORIGINAL = '[tool]\nname = "x"\nlegacy = true\nclassifiers = [\n    "One",\n]\n'
AFTER = ORIGINAL.replace("legacy = true\n", "")
HEAD = "diff --git a/p.toml b/p.toml\n--- a/p.toml\n+++ b/p.toml\n"
DELETE = HEAD + "@@ -3 +2,0 @@\n-legacy = true\n"         # exactly what git -U0 emits
DELETE_LONG = HEAD + "@@ -3,1 +2,0 @@\n-legacy = true\n"


@pytest.fixture
def workdir(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", d)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _undo(d, content, diff):
    from app.utils.diff_utils.pipeline.reverse_pipeline import apply_reverse_diff_pipeline
    p = os.path.join(d, "p.toml")
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    r = apply_reverse_diff_pipeline(diff, p)          # no expected_content, as the route calls it
    with open(p, encoding="utf-8") as f:
        return r, f.read()


@pytest.mark.parametrize("diff", [DELETE, DELETE_LONG], ids=["short", "long"])
def test_undo_zero_context_deletion_restores_position(workdir, diff):
    r, out = _undo(workdir, AFTER, diff)
    assert r.get("status") == "success", r
    assert out == ORIGINAL


def test_undo_zero_context_insertion_still_correct(workdir):
    """Control: the -R path that already worked must keep working."""
    ins = HEAD + "@@ -2,0 +3 @@\n+legacy = true\n"
    r, out = _undo(workdir, ORIGINAL, ins)
    assert r.get("status") == "success", r
    assert out == AFTER


@pytest.mark.parametrize("header,expected", [
    ("@@ -3 +2,0 @@", "@@ -2,0 +3 @@"),
    ("@@ -2,0 +3 @@", "@@ -3 +2,0 @@"),
    ("@@ -3,1 +2,0 @@", "@@ -2,0 +3,1 @@"),
    ("@@ -5 +5 @@ ctx", "@@ -5 +5 @@ ctx"),
    ("@@ -1,2 +1,3 @@", "@@ -1,3 +1,2 @@"),
])
def test_reverse_diff_swaps_every_header_form(header, expected):
    out = reverse_diff(HEAD + header + "\n-a\n")
    assert expected in out.splitlines()
