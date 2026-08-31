"""
Tests for the server-side LaTeX diagram renderer (app.services.latex_renderer)
and its profile registry (app.services.latex_profiles).

Why this file matters more than a typical render test: LaTeX is Turing-complete
with filesystem access, so a regression in the prescan or the sandbox profile is
a file-disclosure bug, not a cosmetic rendering glitch.  The security assertions
here are the primary value; the render assertions are secondary and skip cleanly
when TeX is absent.

Layers under test, in order of load-bearing-ness:

  1. ``-no-shell-escape``     defeats \\write18
  2. ``sandbox-exec``         defeats \\input{/etc/passwd} -- REQUIRED, because
                              openin_any=p does NOT block \\input
  3. prescan deny-list        defeats known constructs before compilation, and
                              is the ONLY layer on platforms with no sandbox

plus timeout with process-group kill, and output size caps.
"""

import re
import shutil
import tempfile
from pathlib import Path

import pytest

from app.services.latex_profiles import (
    LATEX_DIAGRAM_TYPES,
    PROFILES,
    get_profile,
    install_command,
)
from app.services.latex_renderer import (
    Capability,
    LatexRenderer,
    MAX_BODY_CHARS,
)


@pytest.fixture
def renderer(tmp_path):
    """A renderer with an isolated cache so tests never share state."""
    return LatexRenderer(cache_dir=tmp_path / "cache")


#: Skip render tests (not security tests) when no TeX toolchain is present.
_cap = LatexRenderer().probe()
needs_tex = pytest.mark.skipif(
    not _cap.available, reason="no TeX toolchain (pdflatex/gs or latex/dvisvgm)"
)
needs_svg = pytest.mark.skipif(
    not (_cap.has_latex and _cap.has_dvisvgm), reason="dvisvgm not installed"
)
needs_circuitikz = pytest.mark.skipif(
    not LatexRenderer()._kpsewhich("circuitikz.sty"),
    reason="circuitikz not installed",
)


# ---------------------------------------------------------------------------
# Prescan: the deny-list.  These run without TeX.
# ---------------------------------------------------------------------------

#: Each entry must be rejected.  The digit-suffixed forms are regression
#: coverage for a real bypass: a TeX control word ends at the first non-letter,
#: so ``\def1`` invokes ``\def``, but ``\b`` finds no word boundary between
#: ``f`` and ``1`` and the rule silently failed to match.
ATTACKS = [
    # shell escape
    r"\write18{id}",
    r"\immediate\write18{rm -rf /}",
    # file system access -- \input is the one that defeats openin_any=p
    r"\input{/etc/passwd}",
    r"\input /etc/passwd",
    r"\input/etc/passwd",
    r"\INPUT{/etc/passwd}",
    r"\include{/etc/hosts}",
    r"\InputIfFileExists{/etc/passwd}{}{}",
    r"\openin\z=/etc/passwd",
    # digit-suffix bypasses of the above
    r"\openin1=/etc/hosts",
    r"\read1 to \line",
    r"\write16{x}",
    r"\openout1=/tmp/escaped",
    # package / class injection
    r"\usepackage{shellesc}",
    r"\usepackage1{shellesc}",
    r"\RequirePackage{shellesc}",
    r"\documentclass{article}",
    # macro definition (also the unbounded-expansion vector)
    r"\def\x{\x}\x",
    r"\def1",
    r"\gdef1",
    r"\edef1",
    r"\xdef1",
    r"\let1\relax",
    r"\newcommand{\x}{}",
    # catcode games, which defeat the other filters
    "\\catcode`\\#=12",
    r"\catcode1=12",
    # indirect construction
    r"\csname inpu\endcsname",
    r"\csname1",
    r"\expandafter1",
    # raw driver / PDF object access
    r"\special{ps: dangerous}",
    r"\special1{ps:}",
    r"\pdfliteral{...}",
    r"\pdfximage{}",
    r"\directlua{os.execute('id')}",
    r"\directlua1{}",
    # output routine
    r"\shipout\box0",
    r"\output1",
]

#: Real diagram bodies that must NOT be rejected.  Several are chosen to prove
#: the lookahead does not over-match a longer control word that merely starts
#: with a denied name (\readlist, \writeup, \inputs, \defaultname).
LEGITIMATE = [
    r"\draw (0,0) to[R, l=$R_1$] (2,0);",
    r"\node[ground] at (0,-1) {};",
    r"\foreach \i in {0,...,5} {\draw (\i*1.2,0) circle (2pt);}",
    r"\draw[->] (0,0) -- ++(2,1) node[midway,sloped]{signal};",
    r"\draw (0,0) to[V=$V_{cc}$, invert] (0,2);",
    r"\node[rotate=37] at (1,1) {\SI{4.7}{\kilo\ohm}};",
    r"\draw (0,0) rectangle (2,2);",
    r"\readlist\data{1,2,3}",
    r"\writeup{notes}",
    r"\inputs",
    r"\defaultname",
]


@pytest.mark.parametrize("body", ATTACKS)
def test_prescan_rejects_dangerous_constructs(body):
    reason = LatexRenderer.prescan(body)
    assert reason, f"prescan failed to reject {body!r}"


@pytest.mark.parametrize("body", LEGITIMATE)
def test_prescan_allows_real_diagram_bodies(body):
    assert LatexRenderer.prescan(body) is None, (
        f"prescan wrongly rejected {body!r}: {LatexRenderer.prescan(body)}"
    )


def test_prescan_rejects_oversized_body():
    reason = LatexRenderer.prescan("x" * (MAX_BODY_CHARS + 1))
    assert reason is not None
    assert "too large" in reason


def test_render_reports_rejection_without_invoking_tex(renderer):
    result = renderer.render("tikz", r"\input{/etc/passwd}")
    assert not result.ok
    assert result.error_kind == "rejected"
    assert not result.content


def test_render_rejects_unknown_diagram_type(renderer):
    result = renderer.render("definitely-not-a-profile", r"\draw (0,0);")
    assert not result.ok
    assert result.error_kind == "internal"


# ---------------------------------------------------------------------------
# Sandbox profile.  Asserted structurally so a regression is caught even on
# platforms where sandbox-exec cannot run.
# ---------------------------------------------------------------------------

def test_sandbox_profile_denies_sensitive_reads_and_confines_writes(tmp_path):
    profile = LatexRenderer._sandbox_profile(tmp_path)
    # The paths that a leak would target.
    for denied in ("/etc", "/private/etc", "/Users"):
        assert f'(deny file-read* (subpath "{denied}"))' in profile
    # Writes denied globally, then re-allowed only for the render's temp dir.
    assert "(deny file-write*)" in profile
    assert f'(allow file-write* (subpath "{tmp_path}"))' in profile
    assert "(deny network*)" in profile
    # The temp dir must remain readable or TeX cannot read its own source.
    assert f'(allow file-read* (subpath "{tmp_path}"))' in profile


@pytest.mark.skipif(not shutil.which("sandbox-exec"), reason="macOS sandbox only")
def test_sandbox_is_applied_to_toolchain_invocations(renderer, monkeypatch):
    """The sandbox must actually wrap argv, not merely be available.

    Asserts at the Popen boundary rather than by monkeypatching ``_run``.  The
    wrapping is applied *inside* ``_run``, so a fake ``_run`` only ever sees the
    unwrapped argv its caller passed and would pass even if the sandbox logic
    were deleted -- the assertion has to sit downstream of the wrapping to mean
    anything.
    """
    seen = {}

    class FakePopen:
        """Enough of Popen for the compile step; records the engine argv.

        Must also satisfy ``subprocess.run`` (context manager + wait), because
        ``_kpsewhich`` shares this module's ``subprocess`` reference.
        """
        def __init__(self, argv, **kwargs):
            self.args = list(argv)
            self.pid = -1
            self.returncode = 0
            if "doc.tex" in self.args:      # the LaTeX invocation, not a probe
                seen.setdefault("argv", list(argv))

        def communicate(self, input=None, timeout=None):
            return ("", "")

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    # Pin the capability so the render reaches _compile without probing.
    renderer._capability = Capability(
        has_latex=True, has_dvisvgm=True, has_pdflatex=True,
        has_ghostscript=True, has_sandbox=True, has_standalone=True,
    )
    monkeypatch.setattr(LatexRenderer, "_kpsewhich", staticmethod(lambda _f: True))
    monkeypatch.setattr("app.services.latex_renderer.subprocess.Popen", FakePopen)
    renderer.render("tikz", r"\draw (0,0) circle (1);", use_cache=False)
    assert seen["argv"][0] == "sandbox-exec"
    assert "-p" in seen["argv"]
    assert any(a in ("latex", "pdflatex") for a in seen["argv"]), \
        "the real engine must still be invoked under the sandbox"


# ---------------------------------------------------------------------------
# Capability probing and the not-installed notice.
# ---------------------------------------------------------------------------

def test_capability_requires_a_complete_pipeline():
    assert not Capability().available
    # latex alone is useless without a DVI converter.
    assert not Capability(has_latex=True).available
    assert not Capability(has_pdflatex=True).available


def test_capability_prefers_svg_when_dvisvgm_present():
    svg_capable = Capability(has_latex=True, has_dvisvgm=True)
    assert svg_capable.preferred_format == "svg"
    png_only = Capability(has_pdflatex=True, has_ghostscript=True)
    assert png_only.preferred_format == "png"
    assert Capability().preferred_format == ""


def test_not_installed_result_names_the_packages_to_install(renderer, monkeypatch):
    """The UI shows install instructions, so they must be actionable."""
    monkeypatch.setattr(LatexRenderer, "_kpsewhich", staticmethod(lambda _f: False))
    monkeypatch.setattr(
        LatexRenderer, "probe",
        lambda self, refresh=False: Capability(
            has_pdflatex=True, has_ghostscript=True,
            missing_toolchain=("dvisvgm", "standalone"),
        ),
    )
    result = renderer.render("circuitikz", r"\draw (0,0) to[R] (2,0);")
    assert not result.ok
    assert result.error_kind == "not_installed"
    assert result.install_hint.startswith("tlmgr install ")
    assert "circuitikz" in result.missing_packages
    # Toolchain gaps fold into the same command so one paste fixes everything.
    assert "dvisvgm" in result.install_hint


def test_no_tex_at_all_is_distinguished_from_missing_packages(renderer, monkeypatch):
    monkeypatch.setattr(
        LatexRenderer, "probe", lambda self, refresh=False: Capability()
    )
    result = renderer.render("tikz", r"\draw (0,0) circle (1);")
    assert result.error_kind == "not_installed"
    assert "No TeX installation" in result.error


# ---------------------------------------------------------------------------
# Profile registry: the extension point.
# ---------------------------------------------------------------------------

def test_get_profile_is_case_and_whitespace_insensitive():
    assert get_profile("  CircuiTikZ  ").key == "circuitikz"
    assert get_profile("nonexistent") is None
    assert get_profile("") is None


def test_every_profile_declares_install_metadata():
    """Without tl_packages the not-installed notice cannot be built."""
    for key, profile in PROFILES.items():
        assert profile.key == key
        assert profile.tl_packages, f"{key} has no tl_packages"
        assert profile.probe_files, f"{key} has no probe_files"


def test_latex_diagram_types_matches_registry():
    assert LATEX_DIAGRAM_TYPES == frozenset(PROFILES)


def test_install_command_is_sorted_and_deduplicated():
    cmd = install_command(["dvisvgm", "circuitikz", "circuitikz"])
    assert cmd == "tlmgr install circuitikz dvisvgm"
    assert install_command([]) == ""


def test_svg_documents_override_the_pgf_driver():
    """Load-bearing, not cosmetic.

    Under plain ``latex`` PGF defaults to pgfsys-dvips.def and emits the drawing
    as PostScript specials that dvisvgm cannot interpret, so the graphics vanish
    and only text survives.  The override must be present for SVG and absent for
    PDF/PNG, where it collapses the output to text instead.
    """
    profile = get_profile("circuitikz")
    body = r"\draw (0,0) to[R] (2,0);"
    svg_doc = profile.build_document(body, standalone=True, fmt="svg")
    png_doc = profile.build_document(body, standalone=True, fmt="png")
    assert r"\def\pgfsysdriver{pgfsys-dvisvgm.def}" in svg_doc
    assert "pgfsysdriver" not in png_doc


def test_body_that_already_opens_the_environment_is_not_double_wrapped():
    profile = get_profile("circuitikz")
    pre_wrapped = (
        "\\begin{circuitikz}\n\\draw (0,0) to[R] (2,0);\n\\end{circuitikz}"
    )
    doc = profile.build_document(pre_wrapped, standalone=True, fmt="png")
    assert doc.count("\\begin{circuitikz}") == 1


def test_standalone_absent_falls_back_to_article_with_empty_pagestyle():
    profile = get_profile("tikz")
    doc = profile.build_document(r"\draw (0,0);", standalone=False, fmt="png")
    assert "\\documentclass{article}" in doc
    # Without this a page number lands far below the art and defeats cropping.
    assert "\\pagestyle{empty}" in doc


# ---------------------------------------------------------------------------
# Log parsing: turns a 400-line TeX log into one actionable line.
# ---------------------------------------------------------------------------

def test_missing_package_error_is_reported_as_such():
    log = "! LaTeX Error: File `circuitikz.sty' not found.\n\nType X to quit"
    message = LatexRenderer._extract_error(log)
    assert "circuitikz.sty" in message
    assert "not installed" in message


def test_generic_tex_error_falls_back_to_the_first_bang_line():
    assert LatexRenderer._extract_error("! Emergency stop.") == "Emergency stop."
    assert LatexRenderer._extract_error("nothing wrong here") == ""


def test_unsupported_unicode_character_error_is_actionable():
    """A CJK/emoji codepoint aborts (pdf)LaTeX with a TWO-line message.

    Regression for defect-24: a circuitikz label carrying a Chinese
    character ("电流探针") aborts the compile with the verbatim log below
    (captured from a live ``latex`` run).  The generic ``! (.+)`` fallback
    surfaced only the FIRST line -- the sentence fragment "LaTeX Error:
    Unicode character 电 (U+7535)" that ends mid-clause with no verb, cause
    or remedy.  The fix names the character and gives an actionable remedy.
    """
    # Verbatim two-line block from the live compile (doc.log).
    log = (
        "! LaTeX Error: Unicode character \u7535 (U+7535)\n"
        "               not set up for use with LaTeX.\n\n"
        "See the LaTeX manual or LaTeX Companion for explanation.\n"
    )
    message = LatexRenderer._extract_error(log)

    # Non-vacuity: the OLD generic branch (still present as the fallback)
    # would have returned exactly the truncated fragment.  Prove the new
    # message is strictly better than that fragment.
    fragment = "LaTeX Error: Unicode character \u7535 (U+7535)"
    assert message != fragment

    # The remedy names the offending character AND its codepoint AND a fix.
    assert "\u7535" in message
    assert "U+7535" in message
    assert "cannot typeset" in message
    assert "Replace" in message or "remove" in message
    # Not a truncated fragment: it ends in a full stop, not "(U+7535)".
    assert message.rstrip().endswith(".")


def test_unicode_error_generalises_beyond_the_cjk_instance():
    """The remedy fires for any unsupported codepoint, not just U+7535.

    Confirms the fix repairs the whole class (emoji, other scripts), not the
    single defect-24 character.
    """
    log = "! LaTeX Error: Unicode character \U0001f600 (U+1F600)\n               not set up for use with LaTeX.\n"
    message = LatexRenderer._extract_error(log)
    assert "U+1F600" in message
    assert "cannot typeset" in message


def test_log_tail_is_bounded():
    tail = LatexRenderer._tail("\n".join(str(n) for n in range(500)), lines=40)
    assert len(tail.splitlines()) == 40
    assert tail.endswith("499")
    assert LatexRenderer._tail("") == ""


# ---------------------------------------------------------------------------
# Rendering.  Skipped cleanly when the toolchain is absent.
# ---------------------------------------------------------------------------

@needs_tex
def test_renders_tikz_to_png(renderer):
    result = renderer.render("tikz", r"\draw (0,0) circle (1);", fmt="png")
    assert result.ok, result.error
    assert result.fmt == "png"
    assert result.content.startswith(b"\x89PNG\r\n\x1a\n")


@needs_svg
def test_renders_tikz_to_svg_with_real_geometry_and_text(renderer):
    """Guards the pgfsysdriver regression from the consumer side.

    A dvips-driver SVG still parses and still contains the labels; what it loses
    is the drawing.  Asserting on <path> count is therefore the assertion that
    actually detects the bug.

    The body must be four SEPARATE \\draw commands, not one ``rectangle``: pgf
    emits a rectangle as a single <path>, so the old fixture could only ever
    produce 1 and the >= 4 threshold was unsatisfiable.  Measured on this
    toolchain: four \\draw lines give 4 paths with the dvisvgm driver and 0
    with the dvips driver, which is exactly the gap this test exists to catch.
    """
    body = (r"\draw (0,0)--(3,0); \draw (3,0)--(3,2);"
            r" \draw (3,2)--(0,2); \draw (0,2)--(0,0);"
            r" \node at (1.5,1) {label};")
    result = renderer.render("tikz", body, fmt="svg")
    assert result.ok, result.error
    svg = result.content.decode("utf-8")
    assert svg.lstrip().startswith("<?xml") or svg.lstrip().startswith("<svg")
    assert svg.count("<path") >= 4, "drawing geometry missing (dvips driver?)"
    assert "<text" in svg, "text should stay selectable, not become glyph paths"


@needs_circuitikz
def test_renders_a_real_circuit(renderer):
    body = (
        r"\draw (0,0) to[R, l=$R_1$] (2,0) to[C, l=$C_1$] (4,0)"
        r" to[D] (4,-2) -- (0,-2) node[ground]{};"
    )
    result = renderer.render("circuitikz", body, fmt="auto")
    assert result.ok, result.error
    assert len(result.content) > 1000


def test_circuitikz_profile_loads_the_positioning_library():
    """``<key>=<dist> of <node>`` placement needs \\usetikzlibrary{positioning}.

    circuitikz builds on TikZ but does not load the positioning library
    itself.  Without it, ``\\node[adc, right=2.5cm of A]`` parses ``right=`` as
    a bare PGF-math key and dies with "Unknown operator `of'" -- a fatal abort
    with no image.  The library line must appear after the circuitikz package
    (which pulls in TikZ) so \\usetikzlibrary is defined when it runs.
    """
    profile = get_profile("circuitikz")
    doc = profile.build_document(r"\draw (0,0) to[R] (2,0);", standalone=True, fmt="svg")
    # positioning is the first library in the combined \usetikzlibrary line
    # (fit was added alongside it; see test_circuitikz_profile_loads_the_fit_library).
    assert "positioning" in doc
    assert r"\usetikzlibrary{positioning,fit}" in doc
    assert doc.index(r"\usepackage[american]{circuitikz}") < doc.index(
        r"\usetikzlibrary{positioning,fit}"
    )


@needs_circuitikz
def test_positioning_of_syntax_compiles_instead_of_fatal_erroring(renderer):
    """Regression for the ``... of ...`` chained-placement fatal compile abort.

    Pre-fix this body produced no image and the log carried
    "! Package PGF Math Error: Unknown operator `o' or `of' (in '2.5cm of A')."
    followed by "Fatal error occurred, no output PDF file produced!".  With the
    positioning library loaded it renders.  Uses ``op amp`` (which exposes the
    ``.out``/``.in`` port anchors) rather than the bare ``amp`` bipole, so the
    only thing under test is the positioning placement.
    """
    body = (
        r"\node[amp] (A) at (0,0) {};"
        r"\node[adc, right=2.5cm of A] (AD) {};"
        r"\node[dac, right=2.5cm of AD] (DA) {};"
        r"\draw (A) -- (AD) -- (DA);"
    )
    result = renderer.render("circuitikz", body, fmt="auto")
    # ok=True is the whole point: pre-fix this was a fatal abort with no output.
    assert result.ok, result.error
    # A real drawing, not an empty document -- three placed nodes and two wires.
    assert len(result.content) > 400


def test_circuitikz_profile_loads_the_fit_library():
    """``fit=(A)(B)`` bounding boxes need \\usetikzlibrary{fit}.

    Like positioning, circuitikz builds on TikZ but does not load the fit
    library itself.  Without it, ``\\node[draw, fit=(A)(B)]`` dies with
    "I do not know the key '/tikz/fit'" -- a fatal abort with no image.  The
    library line must appear after the circuitikz package (which pulls in
    TikZ) so \\usetikzlibrary is defined when it runs.
    """
    profile = get_profile("circuitikz")
    doc = profile.build_document(r"\draw (0,0) to[R] (2,0);", standalone=True, fmt="svg")
    assert r"\usetikzlibrary{positioning,fit}" in doc
    assert doc.index(r"\usepackage[american]{circuitikz}") < doc.index(
        r"\usetikzlibrary{positioning,fit}"
    )


@needs_circuitikz
def test_fit_key_compiles_instead_of_fatal_erroring(renderer):
    """Regression for the ``fit=(A)(B)`` fatal compile abort.

    Pre-fix this body produced no image and the log carried
    "! Package pgfkeys Error: I do not know the key '/tikz/fit', to which you
    passed '(A)(B)'" followed by "Fatal error occurred, no output PDF file
    produced!".  With the fit library loaded it renders.  Draws a dashed
    bounding box around two named junction nodes spanning a resistor -- a
    plausible subcircuit annotation.
    """
    body = (
        r"\draw (0,0) to[R, l=$R_1$] (3,0);"
        r"\node[circ] (A) at (0,0) {};"
        r"\node[circ] (B) at (3,0) {};"
        r"\node[draw, dashed, rounded corners, inner sep=4pt, fit=(A)(B)] {};"
    )
    result = renderer.render("circuitikz", body, fmt="auto")
    # ok=True is the whole point: pre-fix this was a fatal abort with no output.
    assert result.ok, result.error
    assert len(result.content) > 400


@needs_tex
def test_cache_returns_identical_bytes_and_reports_the_hit(renderer):
    body = r"\draw (0,0) -- (2,2);"
    first = renderer.render("tikz", body, fmt="png")
    second = renderer.render("tikz", body, fmt="png")
    assert first.ok and second.ok
    assert not first.cached
    assert second.cached
    assert first.content == second.content


@needs_tex
def test_use_cache_false_bypasses_the_cache(renderer):
    body = r"\draw (0,0) -- (1,1);"
    renderer.render("tikz", body, fmt="png")
    assert not renderer.render("tikz", body, fmt="png", use_cache=False).cached


def test_cache_key_separates_output_formats():
    """SVG and PNG must never collide, or one format serves the other's bytes."""
    assert (
        LatexRenderer._cache_key("same document", "svg")
        != LatexRenderer._cache_key("same document", "png")
    )


@needs_tex
def test_runaway_expansion_is_killed_and_leaves_no_processes(tmp_path):
    """Prescan blocks \\def, so this exercises the timeout directly.

    pdflatex spawns children and survives a naive terminate(), which is why the
    implementation kills the whole process group.
    """
    renderer = LatexRenderer(cache_dir=tmp_path / "cache", timeout=2)
    profile = get_profile("tikz")
    document = profile.build_document(
        r"\node at (0,0) {x};", standalone=True, fmt="png"
    ).replace(
        r"\node at (0,0) {x};", r"\loop\iftrue\repeat"
    )
    result = renderer._compile(document, "png", renderer.probe())
    assert not result.ok
    assert result.error_kind == "timeout"


# ---------------------------------------------------------------------------
# CircuiTikZ option-value lint wiring (F: pgfkeys-hostile '=' in a value)
# ---------------------------------------------------------------------------
_CIRCUIT_BROKEN = (
    r"\draw (0,0) to[V, l=$V_{in}$] (0,2)"
    "\n  " r"to[R, l=$R_C=\SI{2.2}{\kilo\ohm}$] (3,2)"
    "\n  " r"to[C, l=$C_1$] (3,0) to[short] (0,0) node[ground]{};"
)


def test_lint_circuitikz_braces_hostile_option_value(renderer):
    """The renderer's circuitikz lint hook braces a value with a bare '='."""
    body, fixes, warnings = renderer._lint_circuitikz(_CIRCUIT_BROKEN)
    assert r"l={$R_C=\SI{2.2}{\kilo\ohm}$}" in body
    assert len(fixes) == 1
    assert warnings == ()


def test_lint_circuitikz_leaves_clean_body_untouched(renderer):
    """A body with no hostile value is returned byte-identical (over-reach)."""
    clean = r"\draw (0,0) to[R, l=$R_1$] (2,0) to[C, l=$C_1$] (2,-2);"
    body, fixes, warnings = renderer._lint_circuitikz(clean)
    assert body == clean
    assert fixes == ()


def test_lint_circuitikz_hook_selected_for_profile_key():
    """render() dispatches the circuitikz body through the circuitikz lint."""
    import inspect
    from app.services.latex_renderer import LatexRenderer

    src = inspect.getsource(LatexRenderer.render)
    assert 'profile.key == "circuitikz"' in src
    assert "_lint_circuitikz" in src
