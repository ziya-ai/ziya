"""An unterminated diff text must not fuse its last '+' line with the next line.

THE DEFECT THIS EXISTS FOR
--------------------------
2026-09-24: a two-hunk, zero-context diff for pyproject.toml was applied from a
chat fence whose body had lost its final newline. The system_patch stage (Apple
``patch`` 2.0-12u11) read the unterminated final ``+keywords = [...]`` line as
"no newline", joined it with the following ``classifiers = [`` line, exited 0,
and the pipeline reported success. The file stopped parsing as TOML.

In unified-diff format a missing newline is expressed ONLY by an explicit
``\\ No newline at end of file`` marker, so every public entry point now
terminates the diff text first (``terminate_diff_text``). The forward case is
also a fixture (tests/diff_test_cases/MRE_unterminated_final_add_joins_next_line);
this file covers the seams the fixture runner does not reach -- the reverse
(unapply) pipeline and the CLI's ``apply_diff_atomically`` -- plus the explicit
marker control, and proves each test fails when the helper is disabled.
"""
import os
import shutil
import tempfile

import pytest

from app.utils.diff_utils.core import text_normalization
from app.utils.diff_utils.core.text_normalization import terminate_diff_text

ORIGINAL = (
    "[tool]\n"
    'name = "x"\n'
    'keywords = ["a", "b"]\n'
    "classifiers = [\n"
    '    "One",\n'
    "]\n"
)
EXPECTED = ORIGINAL.replace('keywords = ["a", "b"]', 'keywords = ["a", "b", "c"]')
# deliberately unterminated: no newline after the final '+' line
DIFF = (
    "diff --git a/p.toml b/p.toml\n"
    "--- a/p.toml\n"
    "+++ b/p.toml\n"
    "@@ -3,1 +3,1 @@\n"
    '-keywords = ["a", "b"]\n'
    '+keywords = ["a", "b", "c"]'
)
FUSED = 'keywords = ["a", "b", "c"]classifiers = ['


@pytest.fixture
def workdir(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", d)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _write(d, content):
    p = os.path.join(d, "p.toml")
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return p


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _disable_fix(monkeypatch):
    monkeypatch.setattr(text_normalization, "terminate_diff_text", lambda s: s)


# --- the helper -----------------------------------------------------------

def test_helper_terminates_only_unterminated_non_empty_text():
    assert terminate_diff_text("a\n+b") == "a\n+b\n"
    assert terminate_diff_text("a\n+b\n") == "a\n+b\n"
    assert terminate_diff_text("") == ""
    assert terminate_diff_text("   ") == "   "
    marker = "+x\n\\ No newline at end of file"
    assert terminate_diff_text(marker) == marker + "\n"


# --- forward pipeline (the incident path) ---------------------------------

def _forward(path, diff):
    from app.utils.diff_utils.pipeline.pipeline_manager import apply_diff_pipeline
    return apply_diff_pipeline(diff, path)


def test_forward_pipeline_does_not_fuse_lines(workdir):
    p = _write(workdir, ORIGINAL)
    r = _forward(p, DIFF)
    assert r.get("status") == "success", r
    assert _read(p) == EXPECTED


def test_forward_pipeline_fuses_lines_without_the_fix(workdir, monkeypatch):
    """Premise: the forward test above fails if the fix is removed."""
    _disable_fix(monkeypatch)
    p = _write(workdir, ORIGINAL)
    _forward(p, DIFF)
    assert FUSED in _read(p)


# --- reverse (unapply) pipeline -------------------------------------------

def _reverse(path, diff):
    from app.utils.diff_utils.pipeline.reverse_pipeline import apply_reverse_diff_pipeline
    return apply_reverse_diff_pipeline(diff, path)


def test_reverse_pipeline_restores_original(workdir):
    p = _write(workdir, EXPECTED)
    r = _reverse(p, DIFF)
    assert r.get("status") == "success", r
    assert _read(p) == ORIGINAL


# In reverse, the side patch -R WRITES is the '-' side.  An unterminated final
# '+' line (DIFF above) is only matched, so undo is unaffected by it -- verified
# 2026-09-24.  The hazard is an unterminated final '-' line: undo writes it with
# no newline and fuses it with the next line.
REV_ORIGINAL = (
    "[tool]\n"
    'name = "x"\n'
    "legacy = true\n"
    "classifiers = [\n"
    '    "One",\n'
    "]\n"
)
REV_AFTER = REV_ORIGINAL.replace("legacy = true\n", "")
REV_DIFF = (  # deliberately unterminated: no newline after the final '-' line
    "diff --git a/p.toml b/p.toml\n"
    "--- a/p.toml\n"
    "+++ b/p.toml\n"
    "@@ -3 +2,0 @@\n"
    "-legacy = true"
)


def test_reverse_pipeline_does_not_fuse_unterminated_removal(workdir):
    p = _write(workdir, REV_AFTER)
    r = _reverse(p, REV_DIFF)
    assert r.get("status") == "success", r
    out = _read(p)
    assert "legacy = truename" not in out and "legacy = trueclassifiers" not in out
    # Byte-equality holds now that zero-context deletion undo places the line
    # correctly (tests/test_diff_reverse_zero_context.py).
    assert out == REV_ORIGINAL


def test_reverse_pipeline_fuses_unterminated_removal_without_the_fix(workdir, monkeypatch):
    """Premise: the reverse test above fails if the fix is removed."""
    _disable_fix(monkeypatch)
    p = _write(workdir, REV_AFTER)
    _reverse(p, REV_DIFF)
    out = _read(p)
    assert "legacy = true\n" not in out
    assert "legacy = truename" in out or "legacy = trueclassifiers" in out


# --- CLI atomic applicator ------------------------------------------------

def test_atomic_applicator_does_not_fuse_lines(workdir):
    from app.utils.diff_utils.application.git_diff import apply_diff_atomically
    p = _write(workdir, ORIGINAL)
    r = apply_diff_atomically(p, DIFF)
    # None means "can't handle, fall back"; the file must not be corrupted either way
    assert FUSED not in _read(p)
    if r is not None and r.get("status") == "success":
        assert _read(p) == EXPECTED


# --- control: an explicit marker at true EOF still means "no newline" ------

def test_explicit_no_newline_marker_at_eof_is_still_honoured(workdir):
    orig = "a\nb\nlast\n"
    diff = (
        "diff --git a/p.toml b/p.toml\n--- a/p.toml\n+++ b/p.toml\n"
        "@@ -3 +3 @@\n-last\n+final\n\\ No newline at end of file"
    )
    p = _write(workdir, orig)
    r = _forward(p, diff)
    assert r.get("status") == "success", r
    assert _read(p) == "a\nb\nfinal"
