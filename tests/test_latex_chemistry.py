"""
Tests for chemistry rendering support in the LaTeX renderer.

Every behaviour asserted here was driven by a failure that was actually
reproduced, and each test is written to fail again if that failure returns:

  1. An optional package must never be able to break a render.  Guarding
     ``chemmacros`` on its own ``.sty`` looked correct and was actively
     harmful: ``tlmgr install chemmacros`` puts the file on disk without its
     58-package dependency closure, so it loaded and then died fatally,
     breaking plain ``\\chemfig`` structure rendering.  chemmacros is therefore
     NOT used -- Lewis structures come from chemfig's own bundled module.

  2. ``\\chemmove`` draws from a default position on the first pass and only
     lands correctly on the second, so a single pass silently produces a
     *wrong* diagram rather than a failed one (measured: 10 red pixels on
     pass 1, 18 on pass 2, converging at 2).

  3. The dvisvgm driver records a bogus y-coordinate for pgf position marks
     (50586364 vs the correct 229375 from pdflatex), so SVG output silently
     omits the arrow entirely.  Such bodies must be forced to PNG.
"""

import pytest

from app.services.latex_profiles import (
    LatexPackage,
    get_profile,
    requires_position_marks,
)
from app.services.latex_renderer import (
    MAX_LATEX_PASSES,
    Capability,
    LatexRenderer,
    RenderResult,
)


@pytest.fixture
def renderer(tmp_path):
    """A renderer with an isolated cache so tests never share state."""
    return LatexRenderer(cache_dir=tmp_path / "cache")


_cap = LatexRenderer().probe()
needs_tex = pytest.mark.skipif(not _cap.available, reason="no TeX toolchain")
needs_mhchem = pytest.mark.skipif(
    not LatexRenderer()._kpsewhich("mhchem.sty"), reason="mhchem not installed"
)


def _full_capability() -> Capability:
    """A machine with every render path available."""
    return Capability(
        has_latex=True, has_dvisvgm=True, has_pdflatex=True,
        has_ghostscript=True, has_standalone=True,
    )


# ---------------------------------------------------------------------------
# Optional package loading.  Pure string generation -- no TeX required.
# ---------------------------------------------------------------------------

def test_optional_package_is_guarded_by_its_own_sty():
    rendered = LatexPackage("mhchem", "version=4").render_optional()
    assert r"\IfFileExists{mhchem.sty}" in rendered
    assert r"\usepackage[version=4]{mhchem}" in rendered


def test_optional_guard_has_an_empty_else_branch():
    """An absent package must expand to nothing, not to an error."""
    rendered = LatexPackage("mhchem").render_optional()
    assert rendered.endswith("{}")


def test_required_packages_are_not_guarded():
    """chemfig defines the diagram type, so guarding it would mask a real
    misconfiguration as a silently blank render."""
    document = get_profile("chemfig").build_document(
        r"\chemfig{*6(-=-=-=)}", standalone=True)
    assert r"\usepackage{chemfig}" in document
    assert r"\IfFileExists{chemfig.sty}" not in document


def test_chemfig_offers_equation_support_via_mhchem():
    names = {pkg.name for pkg in get_profile("chemfig").optional_packages}
    assert "mhchem" in names, r"\ce{} requires mhchem"


def test_mhchem_version_is_pinned():
    """Unpinned, mhchem's default version varies by release and v3 parses
    \\ce{} arrows incompatibly -- equations would silently change shape."""
    mhchem = next(p for p in get_profile("chemfig").optional_packages
                  if p.name == "mhchem")
    assert "version=" in mhchem.options


def test_chemmacros_is_not_used():
    """Regression guard for the reproduced breakage.

    chemmacros cannot be loaded safely: its dependency closure is 58 packages
    deep and an incomplete install (which `tlmgr install chemmacros` produces)
    kills ALL chemfig rendering, not just the chemmacros features.
    """
    profile = get_profile("chemfig")
    document = profile.build_document(r"\chemfig{A}", standalone=True)
    assert "chemmacros" not in document
    assert "chemmacros" not in profile.optional_tl_packages


def test_lewis_structures_come_from_chemfigs_bundled_module():
    """chemfig ships chemfig-lewis.tex but does not auto-load it, so the
    profile must, or \\lewis is unavailable for no good reason."""
    document = get_profile("chemfig").build_document(
        r"\chemfig{\lewis{2:6:,O}}", standalone=True)
    assert r"\input{chemfig-lewis.tex}" in document
    # Guarded, so a chemfig build without the module degrades rather than dies.
    assert r"\IfFileExists{chemfig-lewis.tex}" in document


def test_optional_packages_are_listed_in_the_install_hint(renderer, monkeypatch):
    """Installing mhchem alongside is strictly better, so name it."""
    monkeypatch.setattr(LatexRenderer, "_kpsewhich", staticmethod(lambda _f: False))
    monkeypatch.setattr(
        LatexRenderer, "probe",
        lambda self, refresh=False: Capability(
            has_pdflatex=True, has_ghostscript=True, tex_distribution="x"),
    )
    result = renderer.render("chemfig", r"\chemfig{A}")
    assert result.error_kind == "not_installed"
    assert "mhchem" in result.install_hint


def test_absent_optional_package_is_never_reported_as_missing(renderer, monkeypatch):
    """A missing optional package must not be blamed for a failed render."""
    def only_chemfig(filename):
        return filename == "chemfig.sty"

    monkeypatch.setattr(LatexRenderer, "_kpsewhich", staticmethod(only_chemfig))
    monkeypatch.setattr(LatexRenderer, "probe",
                        lambda self, refresh=False: _full_capability())
    monkeypatch.setattr(
        LatexRenderer, "_compile",
        lambda self, doc, target, cap: RenderResult(ok=True, content=b"x", fmt=target),
    )
    result = renderer.render("chemfig", r"\chemfig{A}", use_cache=False)
    assert result.ok, "an absent optional package must not block the render"


# ---------------------------------------------------------------------------
# Position marks: extra passes, and PNG forcing.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    r"\chemfig{@{a}A-@{b}B}\chemmove{\draw(a)--(b);}",
    r"\begin{tikzpicture}[remember picture]\node(x){A};\end{tikzpicture}",
])
def test_position_marks_are_detected(body):
    assert requires_position_marks(body)


@pytest.mark.parametrize("body", [
    r"\chemfig{*6(-=-=-=)}",
    r"\draw (0,0) -- (1,1);",
    r"\ce{2H2 + O2 -> 2H2O}",
    r"\chemfig{\lewis{2:6:,O}}",
])
def test_ordinary_bodies_are_not_treated_as_position_marks(body):
    assert not requires_position_marks(body)


def test_position_marks_force_png_over_svg(renderer, monkeypatch):
    """SVG would render successfully while silently dropping the arrow."""
    monkeypatch.setattr(LatexRenderer, "_kpsewhich", staticmethod(lambda _f: True))
    monkeypatch.setattr(LatexRenderer, "probe",
                        lambda self, refresh=False: _full_capability())
    seen = {}

    def fake_compile(self, document, target, cap):
        seen["target"] = target
        return RenderResult(ok=True, content=b"x", fmt=target)

    monkeypatch.setattr(LatexRenderer, "_compile", fake_compile)
    renderer.render("chemfig",
                    r"\chemfig{@{a}A-@{b}B}\chemmove{\draw(a)--(b);}",
                    fmt="svg", use_cache=False)
    assert seen["target"] == "png"


def test_svg_is_retained_for_bodies_without_position_marks(renderer, monkeypatch):
    """The PNG forcing must stay narrow, or every diagram needlessly loses
    selectable text and dark-mode recolouring."""
    monkeypatch.setattr(LatexRenderer, "_kpsewhich", staticmethod(lambda _f: True))
    monkeypatch.setattr(LatexRenderer, "probe",
                        lambda self, refresh=False: _full_capability())
    seen = {}

    def fake_compile(self, document, target, cap):
        seen["target"] = target
        return RenderResult(ok=True, content=b"x", fmt=target)

    monkeypatch.setattr(LatexRenderer, "_compile", fake_compile)
    renderer.render("chemfig", r"\chemfig{*6(-=-=-=)}",
                    fmt="svg", use_cache=False)
    assert seen["target"] == "svg"


# ---------------------------------------------------------------------------
# Multi-pass compilation.
# ---------------------------------------------------------------------------

def _stub_passes(monkeypatch, rerun_forever: bool):
    """Record each LaTeX invocation; optionally always request a rerun."""
    calls: list[str] = []

    def fake_run(self, argv, cwd, cap):
        calls.append(argv[0])
        first_pass = len(calls) == 1
        (cwd / "doc.log").write_text(
            "Rerun to get cross-references right."
            if (rerun_forever or first_pass) else "no more work")
        (cwd / "doc.pdf").write_bytes(b"%PDF-1.5 stub")
        (cwd / "doc.png").write_bytes(b"\x89PNG\r\n\x1a\nstub")
        return ""

    monkeypatch.setattr(LatexRenderer, "_run", fake_run)
    return calls


def test_a_rerun_request_triggers_exactly_one_more_pass(renderer, monkeypatch):
    calls = _stub_passes(monkeypatch, rerun_forever=False)
    document = get_profile("chemfig").build_document(
        r"\chemfig{A}", standalone=True)
    renderer._compile(document, "png",
                      Capability(has_pdflatex=True, has_ghostscript=True))
    assert calls.count("pdflatex") == 2


def test_rerun_loop_is_bounded(renderer, monkeypatch):
    """A document that always asks for a rerun must not loop forever."""
    calls = _stub_passes(monkeypatch, rerun_forever=True)
    document = get_profile("chemfig").build_document(
        r"\chemfig{A}", standalone=True)
    renderer._compile(document, "png",
                      Capability(has_pdflatex=True, has_ghostscript=True))
    assert calls.count("pdflatex") == MAX_LATEX_PASSES


def test_timeout_on_any_pass_stops_immediately(renderer, monkeypatch):
    monkeypatch.setattr(LatexRenderer, "_run", lambda self, a, c, p: None)
    document = get_profile("chemfig").build_document(
        r"\chemfig{A}", standalone=True)
    result = renderer._compile(document, "png",
                               Capability(has_pdflatex=True, has_ghostscript=True))
    assert not result.ok
    assert result.error_kind == "timeout"


# ---------------------------------------------------------------------------
# Error messages.
# ---------------------------------------------------------------------------

def test_ce_without_mhchem_names_the_package_to_install():
    log = ("! Undefined control sequence.\n"
           "<argument> \\ce \n               {Br_2}\n")
    message = LatexRenderer._extract_error(log)
    assert "mhchem" in message
    assert "tlmgr install mhchem" in message


def test_unknown_command_still_gets_the_generic_message():
    log = "! Undefined control sequence.\n<argument> \\notreal \n   {x}\n"
    assert r"\notreal" in LatexRenderer._extract_error(log)


def test_lewis_is_not_misattributed_to_chemmacros():
    """\\lewis ships with chemfig, so blaming chemmacros would send the user
    to install a package that cannot help and may break their install."""
    log = "! Undefined control sequence.\n<argument> \\lewis \n     {2.,N}\n"
    assert "chemmacros" not in LatexRenderer._extract_error(log)


# ---------------------------------------------------------------------------
# End-to-end.  Skipped cleanly when the toolchain or packages are absent.
# ---------------------------------------------------------------------------

@needs_tex
def test_plain_structure_still_renders(renderer):
    """The headline regression guard: an optional-package change must never
    break the base capability."""
    result = renderer.render("chemfig", r"\chemfig{*6(-=-=-=)}",
                             fmt="png", use_cache=False)
    assert result.ok, result.error


@needs_tex
def test_lewis_structure_renders_without_chemmacros(renderer):
    result = renderer.render("chemfig", r"\chemfig{H-\lewis{2:6:,O}-H}",
                             fmt="png", use_cache=False)
    assert result.ok, result.error


@needs_tex
@needs_mhchem
def test_chemical_equation_renders(renderer):
    result = renderer.render("chemfig", r"\ce{2H2 + O2 -> 2H2O}",
                             fmt="png", use_cache=False)
    assert result.ok, result.error
    assert result.content.startswith(b"\x89PNG\r\n\x1a\n")


@needs_tex
@needs_mhchem
def test_reaction_scheme_with_equation_labels_renders(renderer):
    body = ("\\schemestart\n"
            "\\chemfig{*6(-=-=-=)}\n"
            "\\arrow{->[\\ce{Br2}][\\ce{FeBr3}]}\n"
            "\\chemfig{*6(-=(-Br)-=-=)}\n"
            "\\schemestop")
    result = renderer.render("chemfig", body, fmt="auto", use_cache=False)
    assert result.ok, result.error


@needs_tex
def test_chemmove_renders_as_png(renderer):
    """Asserts format and ink, not merely success: a one-pass or SVG render
    also 'succeeds' while omitting the arrow."""
    body = (r"\chemfig{@{a}CH_3-[:30]@{b}Br}"
            r"\chemmove{\draw[->,red,shorten <=4pt,shorten >=2pt]"
            r"(a)..controls +(90:8mm) and +(90:8mm)..(b);}")
    result = renderer.render("chemfig", body, fmt="auto", use_cache=False)
    assert result.ok, result.error
    assert result.fmt == "png", "position marks cannot be carried by SVG"
    assert len(result.content) > 3000, "the arrow appears to be missing"


# ---------------------------------------------------------------------------
# \charge separator.  Pins the syntax documented in Docs/Capabilities.md.
#
# chemfig defines \charge_g#1:#2[#3]=#4, (chemfig.tex:2240), so the angle is
# separated from the charge symbol by "=" and ":" introduces the optional
# radial offset.  Colon-by-analogy is the natural wrong guess, because chemfig
# spells BOND angles -[:30]; it fails with "Argument of \charge_g has an extra
# }", which names brace balance and never mentions the separator.  Both halves
# are asserted so a chemfig change that swapped them could not pass.
# ---------------------------------------------------------------------------

@needs_tex
def test_charge_uses_equals_as_its_separator(renderer):
    result = renderer.render(
        "chemfig", r"\chemfig{\charge{90=\|,180=\|}{O}}",
        fmt="png", use_cache=False)
    assert result.ok, result.error


@needs_tex
def test_charge_with_a_colon_separator_is_repaired_and_renders(renderer):
    r"""The colon-separator wrong guess is auto-repaired, not surfaced as an error.

    ``:`` is the natural (wrong) guess because chemfig spells BOND angles
    ``-[:30]``.  Before the charge repair shipped, this form failed to compile
    with "Argument of \charge_g has an extra }" -- a message that names brace
    balance and never mentions the separator.  ``app/utils/chemfig_charge.py``
    now promotes ``:`` to ``=`` and the render succeeds, reporting the fix so it
    is never silent.  (See test_chemfig_charge.py for the separator rule itself.)
    """
    result = renderer.render(
        "chemfig", r"\chemfig{\charge{90:\|}{O}}",
        fmt="png", use_cache=False)
    assert result.ok, result.error
    assert result.autofixes, "the separator repair must be reported, not silent"


@needs_tex
def test_math_mode_charge_argument_is_wrapped_and_renders(renderer):
    r"""\ominus et al. need their own $...$; the repair now supplies it.

    The raw failure ("Missing $ inserted") does not say which argument was at
    fault, so ``app/utils/chemfig_charge.py`` wraps a control-word payload in
    ``$...$`` and the render succeeds.  The pre-wrapped form must of course also
    render, unchanged.
    """
    repaired = renderer.render(
        "chemfig", r"\chemfig{\charge{90=\scriptstyle\ominus}{O}}",
        fmt="png", use_cache=False)
    assert repaired.ok, repaired.error
    assert repaired.autofixes, "the math wrap must be reported, not silent"

    wrapped = renderer.render(
        "chemfig", r"\chemfig{\charge{90=$\scriptstyle\ominus$}{O}}",
        fmt="png", use_cache=False)
    assert wrapped.ok, wrapped.error
