"""D-033 (group G-29d67c): chemfig embedded in a tikzpicture must keep its
TikZ path-terminating semicolons.

Root cause (differs from the triage hypothesis, which blamed a contrast-clamp
gap): ``strip_statement_terminators`` treated EVERY top-level ``;`` as a stray
statement separator borrowed from another dialect and dropped it.  But a
chemfig molecule is frequently drawn inside a ``tikzpicture``
(``\\node {\\chemfig{...}};``), where each ``\\node``/``\\draw`` is terminated
by a MANDATORY ``;``.  Stripping those aborted the compile with
"Giving up on this path. Did you forget a semicolon?" -- no image in EITHER
theme (chemfig-w3-12, chemfig-w3-13).

The fix tracks TikZ path-environment depth and only strips ``;`` while OUTSIDE
such an environment, so a bare ``\\chemfig{...};`` body (chemfig-w4-12) still
has its stray terminators removed.

These assertions FAIL against the pre-fix code (which stripped the tikz ``;``)
and pass with it.  Both themes are exercised because a theme defect is only
resolved when both render.
"""
import json
import os

import pytest

from app.utils.chemfig_lint import strip_statement_terminators

_SPEC_DIR = os.path.join(".ziya", "gfx-sweep", "specs", "chemfig")


def _load(spec_id: str) -> str:
    with open(os.path.join(_SPEC_DIR, f"{spec_id}.json")) as fh:
        return json.load(fh)["definition"]


def test_semicolons_inside_tikzpicture_are_kept():
    """Every ``\\node ... ;`` terminator inside a tikzpicture survives."""
    body = (
        "\\begin{tikzpicture}\n"
        "\\node[fill=Navy, text=black] at (0,1) {a};\n"
        "\\node[fill=DarkOrange, text=white] at (0,0) {b};\n"
        "\\end{tikzpicture}"
    )
    out, applied = strip_statement_terminators(body)
    assert out == body, "tikz path terminators must not be stripped"
    assert applied == ()
    assert out.count(";") == 2


def test_w3_13_spec_semicolons_preserved():
    body = _load("chemfig-w3-13")
    expected = body.count(";")
    out, applied = strip_statement_terminators(body)
    assert out.count(";") == expected
    assert applied == ()


def test_bare_chemfig_terminators_still_stripped():
    """A ``\\chemfig{...};`` body with no tikz env keeps the recovery behaviour."""
    body = _load("chemfig-w4-12")
    out, applied = strip_statement_terminators(body)
    assert ";" not in out
    assert applied and "dropped 2" in applied[0]


def test_scope_nesting_inside_tikzpicture_kept():
    body = (
        "\\begin{tikzpicture}\n"
        "\\begin{scope}\n"
        "\\draw (0,0) -- (1,1);\n"
        "\\end{scope}\n"
        "\\node at (0,0) {x};\n"
        "\\end{tikzpicture}"
    )
    out, applied = strip_statement_terminators(body)
    assert out == body
    assert applied == ()


@pytest.mark.parametrize("spec_id", ["chemfig-w3-12", "chemfig-w3-13"])
def test_specs_compile_both_themes(spec_id):
    """End-to-end: the previously-aborting specs now produce pixels in both themes."""
    from app.services.latex_renderer import latex_renderer

    cap = latex_renderer.probe()
    if not cap.available:
        pytest.skip("no LaTeX toolchain available")
    body = _load(spec_id)
    for theme in ("light", "dark"):
        res = latex_renderer.render("chemfig", body, fmt="png", theme=theme,
                                    use_cache=False)
        assert res.ok, f"{spec_id} {theme}: {res.error_kind}: {res.error}"
        assert res.content
