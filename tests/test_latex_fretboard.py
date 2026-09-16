r"""fretboard profile: chord-diagram boxes for guitar / ukulele / bass.

Pins every seam the new type crosses: the input normaliser
(app/utils/fretboard_lint.py) in isolation, its registration in
latex_profiles.PROFILES, its dispatch from latex_renderer.render, and -- when a
TeX toolchain is present -- an end-to-end compile of each documented form
through the real renderer on both output paths.

The registry / dispatch tests FAIL before the "fretboard" entry and the
``_lint_fretboard`` branch land, which is the point: they certify the wiring,
not just the halves.
"""
from __future__ import annotations

import re

import pytest

from app.utils.fretboard_lint import (
    normalize_chord_name,
    normalize_fretboard,
    normalize_positions,
)


# ---------------------------------------------------------------------------
# normaliser, in isolation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("x02210", "x,0,2,2,1,0"),            # the form every chord chart uses
    ("X02210", "x,0,2,2,1,0"),            # uppercase mute
    ("x o 2 2 1 o", "x,0,2,2,1,0"),       # space separated, 'o' for open
    ("x,3,2,0,1,0", "x,3,2,0,1,0"),       # already canonical: unchanged
    ("x,x,12,14,14,12", "x,x,12,14,14,12"),  # two-digit frets only via commas
    ("0003", "0,0,0,3"),                  # four strings: ukulele
    ("--0232", "x,x,0,2,3,2"),            # '-' as mute
])
def test_normalize_positions(raw, expected):
    assert normalize_positions(raw) == expected


@pytest.mark.parametrize("raw", ["", "x", "abc", "x0221T", "x,0,q,2,1,0"])
def test_normalize_positions_rejects_garbage(raw):
    # Not a shape this module understands: left for TeX rather than guessed.
    assert normalize_positions(raw) is None


@pytest.mark.parametrize("name, expected", [
    ("Am", "Am"),
    ("F#", r"F{\sharp}"),
    ("F#m", r"F{\sharp}m"),               # braced so 'm' is not eaten by \sharp
    (r"D/F\#", r"D/F{\sharp}"),
    ("E♭", r"E{\flat}"),
    ("B♭maj7", r"B{\flat}maj7"),
    ("Bb", "Bb"),                         # 'b' is ambiguous (B minor?): untouched
])
def test_normalize_chord_name(name, expected):
    assert normalize_chord_name(name) == expected


def test_shorthand_lines_expand_to_chord_calls():
    body = (
        "Am x02210\n"
        "C: x32010\n"
        "F 133211 barre=1 fingers=134211\n"
        "G7 0212\n"
    )
    out, applied = normalize_fretboard(body)
    assert out.splitlines() == [
        r"\chord{Am}{x,0,2,2,1,0}",
        r"\chord{C}{x,3,2,0,1,0}",
        r"\chord[barre=1,fingers={1,3,4,2,1,1}]{F}{1,3,3,2,1,1}",
        r"\chord{G7}{0,2,1,2}",
    ]
    assert any("shorthand" in a for a in applied)


def test_shorthand_is_not_applied_when_body_is_latex():
    # A body with any backslash command is real LaTeX; a bare-looking line in
    # it must not be rewritten.
    body = "\\chord{C}{x,3,2,0,1,0}\nAm x02210\n"
    out, _ = normalize_fretboard(body)
    assert "Am x02210" in out


def test_chord_calls_are_normalised_in_place():
    body = (
        r"\chord{F#m}{x 2 4 4 2 2}  "
        r"\chord[fingers=002310]{Am}{X02210} "
        r"\chord{E♭}{x,6,8,8,8,6}"
    )
    out, applied = normalize_fretboard(body)
    assert r"\chord{F{\sharp}m}{x,2,4,4,2,2}" in out
    assert r"\chord[fingers={0,0,2,3,1,0}]{Am}{x,0,2,2,1,0}" in out
    assert r"\chord{E{\flat}}{x,6,8,8,8,6}" in out
    assert len(applied) >= 4


def test_other_control_words_starting_with_chord_are_left_alone():
    body = r"\chordx{y}{z} \chord{C}{x32010}"
    out, _ = normalize_fretboard(body)
    assert r"\chordx{y}{z}" in out
    assert r"\chord{C}{x,3,2,0,1,0}" in out


def test_canonical_body_is_byte_identical():
    body = "\\chord[barre=1,fingers={1,3,4,2,1,1}]{F}{1,3,3,2,1,1}\n\\chord{Am}{x,0,2,2,1,0}"
    out, applied = normalize_fretboard(body)
    assert out == body
    assert applied == ()


# ---------------------------------------------------------------------------
# registry + document assembly
# ---------------------------------------------------------------------------

def _profiles():
    from app.services.latex_profiles import PROFILES, LATEX_DIAGRAM_TYPES, get_profile
    return PROFILES, LATEX_DIAGRAM_TYPES, get_profile


def test_fretboard_profile_is_registered():
    PROFILES, LATEX_DIAGRAM_TYPES, get_profile = _profiles()
    assert "fretboard" in PROFILES
    assert "fretboard" in LATEX_DIAGRAM_TYPES
    assert get_profile(" FretBoard ") is PROFILES["fretboard"]


def test_fretboard_needs_only_pgf():
    # The whole point of hand-rolling the macro: no chord package to install.
    PROFILES, _, _ = _profiles()
    p = PROFILES["fretboard"]
    assert set(p.tl_packages) == {"pgf"}
    assert p.probe_files == ("tikz.sty",)
    assert p.wrap_env is None


def test_fretboard_document_defines_chord_macro_and_passes_body_through():
    PROFILES, _, _ = _profiles()
    body = r"\chord{Am}{x,0,2,2,1,0}"
    doc = PROFILES["fretboard"].build_document(body, standalone=True)
    assert r"\newcommand{\chord}[3][]" in doc
    assert r"\usepackage{tikz}" in doc
    # xcolor with the extended name sets FIRST, ahead of tikz (D-004).
    assert doc.index("xcolor") < doc.index(r"\usepackage{tikz}")
    # No wrap: the macro opens its own tikzpicture per chord.
    assert doc.count(r"\begin{tikzpicture}") == 1      # the one inside the macro
    assert body in doc


def test_frontend_registry_mirrors_backend():
    # The cross-layer guard lives in the jest suite; this is the Python-side
    # half so a backend-only landing is caught here too.
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "frontend/src/constants/latexProfiles.ts"
    ts = src.read_text()
    assert re.search(r"'fretboard':\s*'fretboard'", ts)
    for alias in ("guitar-chord", "ukulele-chord", "chord-box"):
        assert re.search(rf"'{alias}':\s*'fretboard'", ts), alias
    # 'chord' is the flow-matrix renderer; it must never route here.
    assert not re.search(r"'chord':\s*'fretboard'", ts)
    assert not re.search(r"'chord-diagram':\s*'fretboard'", ts)


# ---------------------------------------------------------------------------
# dispatch through the renderer
# ---------------------------------------------------------------------------

def test_render_dispatches_fretboard_lint(monkeypatch):
    from app.services import latex_renderer as lr

    r = lr.LatexRenderer()
    seen: dict = {}

    def fake_lint(body):
        seen["body"] = body
        return body, ("marker",), ()

    monkeypatch.setattr(lr.LatexRenderer, "_lint_fretboard", staticmethod(fake_lint))
    # Stop before any toolchain use: report nothing installed.
    monkeypatch.setattr(r, "probe", lambda: lr.Capability())
    res = r.render("fretboard", "Am x02210", use_cache=False)
    assert seen["body"] == "Am x02210"
    assert res.ok is False and res.error_kind == "not_installed"


def test_lint_fretboard_expands_shorthand():
    from app.services.latex_renderer import LatexRenderer
    body, fixes, warnings = LatexRenderer._lint_fretboard("Am x02210\nC x32010")
    assert body.splitlines() == [r"\chord{Am}{x,0,2,2,1,0}", r"\chord{C}{x,3,2,0,1,0}"]
    assert fixes and warnings == ()


# ---------------------------------------------------------------------------
# end to end, when a toolchain exists
# ---------------------------------------------------------------------------

def _toolchain():
    from app.services.latex_renderer import LatexRenderer
    r = LatexRenderer()
    cap = r.probe()
    if not cap.available:
        pytest.skip("no LaTeX toolchain")
    if r.missing_for_profile(__import__("app.services.latex_profiles", fromlist=["PROFILES"]).PROFILES["fretboard"]):
        pytest.skip("tikz not installed")
    return r, cap


E2E_BODY = (
    "Am x02210\n"
    "C x32010\n"
    "F# 244322 barre=2 fingers=134211\n"     # barre + fingering + sharp
    "A 577655\n"                             # automatic '5fr' base
    "E♭ x,6,8,8,8,6\n"                       # unicode flat, commas
    "C 0003\n"                               # ukulele
)


@pytest.mark.parametrize("fmt", ["svg", "png"])
def test_fretboard_compiles_end_to_end(fmt):
    r, cap = _toolchain()
    if fmt == "svg" and not (cap.has_latex and cap.has_dvisvgm):
        pytest.skip("no DVI/SVG path")
    if fmt == "png" and not (cap.has_pdflatex and cap.has_ghostscript):
        pytest.skip("no PDF/PNG path")
    res = r.render("fretboard", E2E_BODY, fmt=fmt, use_cache=False)
    assert res.ok, res.error
    assert res.fmt == fmt
    assert len(res.content) > 2000
    if fmt == "svg":
        svg = res.content.decode("utf-8", "replace")
        # Six boxes -> six bold name labels; dvisvgm emits text as paths, so
        # count the drawn geometry instead: at least the 6 nuts + strings.
        assert svg.count("<path") >= 40


def test_fretboard_dark_png_uses_light_ink():
    r, cap = _toolchain()
    if not (cap.has_pdflatex and cap.has_ghostscript):
        pytest.skip("no PDF/PNG path")
    res = r.render("fretboard", "Am x02210", fmt="png", use_cache=False, theme="dark")
    assert res.ok, res.error
    try:
        from PIL import Image
        import io
    except ImportError:                     # pragma: no cover
        pytest.skip("Pillow not available")
    im = Image.open(io.BytesIO(res.content)).convert("RGB")
    colours = set(im.getdata())
    assert (31, 31, 31) in colours          # the dark page
    assert (237, 237, 237) in colours       # light ink, not black-on-dark


def test_fretboard_bare_hash_in_name_does_not_abort():
    # 'F#' unnormalised is a fatal "macro parameter character" error; the lint
    # must have made it compile.
    r, cap = _toolchain()
    res = r.render("fretboard", r"\chord{F#}{2,4,4,3,2,2}", use_cache=False)
    assert res.ok, res.error
