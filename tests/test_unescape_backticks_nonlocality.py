#!/usr/bin/env python3
"""
Regression: unescape_backticks_from_llm is non-local.

Whether an escaped backtick (backslash-backtick) on ONE added line is
unescaped depends on whether an UNRELATED line elsewhere in the same diff
contains a run of two-or-more escaped backticks.  The whole-diff-text guard
(``if '\\`\\`' in text: return text``) makes the transform's decision on any
single line depend on the rest of the diff, so the identical added line comes
out differently in two diffs that differ only in an unrelated neighbour.

This is the deterministic core of the failed diff-1 apply in this session:
an added JSDoc block carrying single-escaped backticks alongside code that
carried triple-escaped backticks corrupted the applied TypeScript (tsc
TS1109 at the language_validation stage).  The tsc rejection itself is
environment-dependent (tsc must be installed), so it cannot be pinned in a
portable fixture; the non-locality of the transform below is the same bug,
reproduced without any toolchain dependency.

These tests DEMONSTRATE the defect: the non-locality assertion is written to
PASS on the current (buggy) behaviour so the harness records the true state,
and is documented so a future fix that makes the transform local will flip
it — at which point the assertion should be inverted to lock in the fix.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.diff_utils.parsing.diff_parser import unescape_backticks_from_llm

_BT = chr(96)            # `
_ESC = chr(92) + _BT     # \`


def _added_lines(diff_text: str):
    return [l[1:] for l in unescape_backticks_from_llm(diff_text).splitlines()
            if l.startswith('+')]


def test_single_escape_alone_is_unescaped_to_real_backticks():
    """A lone added comment with single-escaped backticks and no other
    escaped-backtick run in the diff: the guard does not fire, so the
    escapes are stripped to real backticks."""
    diff = "@@ -1,1 +1,2 @@\n x\n+// see the {e}scope{e} field\n".format(e=_ESC)
    assert _added_lines(diff) == ["// see the {b}scope{b} field".format(b=_BT)]


def test_same_line_preserved_when_unrelated_neighbour_has_triple():
    """The IDENTICAL added comment line, now accompanied by an unrelated
    added line containing a triple escaped-backtick run: the whole-diff
    guard fires and the escapes on BOTH lines are preserved."""
    diff = ("@@ -1,1 +1,3 @@\n x\n"
            "+// see the {e}scope{e} field\n"
            "+const F = {t};\n").format(e=_ESC, t=_ESC * 3)
    added = _added_lines(diff)
    assert added[0] == "// see the {e}scope{e} field".format(e=_ESC)


def test_transform_is_nonlocal_same_input_line_two_outcomes():
    """The defect, stated directly: one and the same added line yields two
    different results depending only on an unrelated neighbour line.  A
    correct (local) transform would produce identical output for that line
    in both diffs.

    This assertion passes on the current buggy behaviour.  When the
    transform is made local, 'alone' and 'neighbour' will become equal
    and this assertion must be inverted to lock in the fix — see the
    module docstring.
    """
    added_line = "+// see the {e}scope{e} field".format(e=_ESC)
    diff_alone = "@@ -1,1 +1,2 @@\n x\n" + added_line + "\n"
    diff_neighbour = ("@@ -1,1 +1,3 @@\n x\n" + added_line + "\n"
                      "+const F = {t};\n").format(t=_ESC * 3)
    alone = _added_lines(diff_alone)[0]
    neighbour = _added_lines(diff_neighbour)[0]
    # Non-local: the same input line comes out differently.
    assert alone != neighbour, (
        "Transform appears to have become local (same line, same output). "
        "If intentional, invert this to assertEqual and drop the xfail note."
    )
    # And concretely: alone got real backticks, neighbour kept the escapes.
    assert _BT in alone and _ESC not in alone
    assert _ESC in neighbour


# ── File-grounded path (the fix) ────────────────────────────────────────
#
# When the target file's content is supplied, the preserve-vs-unescape
# decision is grounded in the file: a context/removal line containing
# backslash-backtick either matches the file verbatim (the file genuinely
# contains the escapes -> preserve) or matches only after unescaping (the
# escaping is a transport artifact -> unescape).  This makes the decision
# LOCAL to the evidence, independent of unrelated added lines — resolving
# the non-locality documented above for diffs that carry file evidence.

def _added_lines_with_file(diff_text: str, file_content: str):
    return [l[1:] for l in
            unescape_backticks_from_llm(diff_text, file_content=file_content).splitlines()
            if l.startswith('+')]


def test_file_grounded_unescapes_when_file_has_real_backticks():
    """A context line with escaped backticks that matches the file only
    after unescaping proves the escaping is transport artifact — the whole
    diff is unescaped, even in the presence of a triple-escape neighbour
    that would have made the heuristic path preserve."""
    ctx = " // uses the {e}scope{e} field".format(e=_ESC)
    diff = ("@@ -1,1 +1,3 @@\n" + ctx + "\n"
            "+// see the {e}scope{e} field\n"
            "+const F = {t};\n").format(e=_ESC, t=_ESC * 3)
    file_content = "// uses the {b}scope{b} field\n".format(b=_BT)
    added = _added_lines_with_file(diff, file_content)
    assert added[0] == "// see the {b}scope{b} field".format(b=_BT)
    assert _ESC not in added[1]  # triple also unescaped consistently


def test_file_grounded_preserves_when_file_has_escapes_verbatim():
    """A removal line matching the file verbatim (escapes and all) proves
    the backslash-backticks are genuine content — preserved."""
    diff = ("@@ -1,1 +1,1 @@\n"
            "-const t = `{e}{e}{e}${{x}}`;\n"
            "+const t = `{e}{e}${{x}}`;\n").format(e=_ESC)
    file_content = "const t = `{e}{e}{e}${{x}}`;\n".format(e=_ESC)
    out = unescape_backticks_from_llm(diff, file_content=file_content)
    assert out == diff


def test_file_grounded_no_signal_falls_back_to_heuristics():
    """When no context/removal line with escapes matches the file either
    way, the file gives no signal and the pre-existing heuristics apply
    unchanged (here: the multi-escape guard preserves)."""
    diff = ("@@ -1,1 +1,2 @@\n x\n"
            "+// see the {e}scope{e} field\n"
            "+const F = {t};\n").format(e=_ESC, t=_ESC * 3)
    file_content = "totally unrelated file content\n"
    out = unescape_backticks_from_llm(diff, file_content=file_content)
    assert out == diff  # heuristic guard preserved, as without file content
