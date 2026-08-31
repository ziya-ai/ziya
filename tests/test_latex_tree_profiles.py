"""Labelled-tree (forest) and proof-tree (bussproofs) LaTeX profiles.

These two profiles were added together because they answer the same shape of
question -- "show me the structure of this derivation/constituency/taxonomy" --
in notations that the graph renderers cannot express.  graphviz can draw *a*
tree; it cannot draw a syntax tree with a triangle over an elided constituent
or a sequent-calculus inference bar, which is precisely what these fields read.

THE COMPILE TESTS ARE THE POINT OF THIS FILE.  Its first version asserted only
structure -- registration, wrap environment, package order -- and passed 15/15
against a bussproofs profile on which EVERY POSSIBLE BODY FAILED.  The
``standalone`` document class typesets its body in an ``\\hbox`` (LR mode) and
bussproofs' ``prooftree`` expands to ``\\begin{center}...\\end{center}``, which
is vertical material, so the compile aborted with "Not allowed in LR mode" for
any input whatsoever.  No structural assertion can see that: the profile was
registered, wrapped correctly, and ordered its packages correctly.  Only
running the toolchain shows it.  Hence ``needs_forest`` / ``needs_bussproofs``
below, and hence every claim the paired skill makes to the model has a
compiling counterpart here.

What is asserted, and why each assertion is load-bearing:

  * WRAPPING.  A model emits a bare body (``[S [NP] [VP]]`` or
    ``\\AxiomC{A}...``) far more often than a fully-delimited one, so the
    profile must supply the environment.  It must equally NOT supply it twice:
    a double ``\\begin{forest}`` is closed by the inner ``\\end``, and the
    compile dies with a mismatched-environment error and produces no image.

  * LR MODE.  ``prooftree`` is redefined by the profile to drop its ``center``
    wrapper and emit ``\\DisplayProof`` (a horizontal box) instead, which is
    both legal under ``standalone`` and crops tight -- measured 281x46px
    against 363x94px for the minipage remedy, on identical ink.

  * ``\\fCenter``.  bussproofs' sequent (no-C) commands align on ``\\fCenter``,
    which the package defaults to ``\\relax``.  That default renders the
    sequent with NO turnstile: a plausible-looking, factually wrong proof,
    which is worse than an error.  A body cannot repair it, because the
    renderer's prescan rejects every macro-definition primitive, so the
    definition has to come from the profile preamble.

  * FOREST LIBRARIES.  ``roof`` -- the triangle over an elided constituent, and
    the single strongest reason to prefer forest over graphviz for syntax
    trees -- lives in ``forest-lib-linguistics``, which forest does not
    auto-load.  Without it ``[NP, roof [...]]`` dies in pgfkeys with "I do not
    know the key '/tikz/roof'".

  * XCOLOR ORDERING (D-004).  xcolor is a once-only package: whoever loads it
    first wins its options.  forest pulls xcolor in unoptioned via pgf, so if
    the profile does not pre-load it with the extended name sets, a CSS/SVG
    colour name on a node is a FATAL "Undefined color".  Asserting mere
    PRESENCE of xcolor would pass while the bug is live; the assertion is
    therefore on ORDER.

  * INSTALL HINTS.  Neither package ships with a basic TeX install, so the
    not-installed path is the *expected* first experience rather than an edge
    case.  Asserted over the WHOLE registry, so a profile added later inherits
    the requirement without its author having to know this test exists.
"""
import re

import pytest

from app.services.latex_profiles import PROFILES, LatexProfile
from app.services.latex_renderer import LatexRenderer


TREE_PROFILE_KEYS = ("forest", "bussproofs")

#: The environment each profile must wrap a bare body in.
WRAP_ENVS = {
    "forest": "forest",
    "bussproofs": "prooftree",
}

#: A bare body in each notation: what a model actually emits when asked for a
#: tree, with no environment of its own.
BARE_BODIES = {
    "forest": "[S [NP [Kim]] [VP [sleeps]]]",
    "bussproofs": "\\AxiomC{$A$}\n\\AxiomC{$B$}\n\\BinaryInfC{$A \\wedge B$}",
}


# ---------------------------------------------------------------------------
# Toolchain gates.  Structural tests run everywhere; compile tests need the
# package actually installed, and are skipped rather than failed when absent --
# a missing package is a property of the machine, not of the profile.
# ---------------------------------------------------------------------------
_renderer = LatexRenderer()
_cap = _renderer.probe()
needs_tex = pytest.mark.skipif(not _cap.available, reason="no TeX toolchain")
needs_forest = pytest.mark.skipif(
    not (_cap.available and _renderer._kpsewhich("forest.sty")),
    reason="forest not installed",
)
needs_bussproofs = pytest.mark.skipif(
    not (_cap.available and _renderer._kpsewhich("bussproofs.sty")),
    reason="bussproofs not installed",
)
needs_linguistics = pytest.mark.skipif(
    not (_cap.available and _renderer._kpsewhich("forest-lib-linguistics.sty")),
    reason="forest linguistics library not installed",
)


def _render(key, body):
    """Render through the real service, with caching off.

    Caching is disabled because these bodies are deliberately near-identical
    across tests, and a cache hit would let a later test pass on an earlier
    test's artifact -- reporting success for a compile that never ran.
    """
    return LatexRenderer().render(key, body, fmt="auto", use_cache=False)


def _assert_rendered(result, what):
    assert result.ok, (
        f"{what} failed to compile: kind={result.error_kind!r} "
        f"error={(result.error or '')[:400]}"
    )


# ---------------------------------------------------------------------------
# Registry membership
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key", TREE_PROFILE_KEYS)
def test_profile_is_registered(key):
    assert key in PROFILES, (
        f"no {key!r} profile: a ```{key} fence has nothing to compile and "
        f"degrades to a literal code block"
    )
    assert isinstance(PROFILES[key], LatexProfile)
    # The frontend registry (constants/latexProfiles.ts) keys off this exact
    # string, and the cross-layer test asserts the two agree, so a typo here
    # surfaces there rather than at render time.
    assert PROFILES[key].key == key


# ---------------------------------------------------------------------------
# Wrapping: supplied when absent, never supplied twice
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key", TREE_PROFILE_KEYS)
def test_bare_body_is_wrapped_in_the_expected_environment(key):
    env = WRAP_ENVS[key]
    doc = PROFILES[key].build_document(BARE_BODIES[key], standalone=True)
    assert f"\\begin{{{env}}}" in doc, (
        f"profile {key!r} did not wrap a bare body in {env!r}; the body is not "
        f"valid LaTeX on its own and the compile aborts"
    )
    assert f"\\end{{{env}}}" in doc


@pytest.mark.parametrize("key", TREE_PROFILE_KEYS)
def test_self_delimited_body_is_not_double_wrapped(key):
    env = WRAP_ENVS[key]
    body = f"\\begin{{{env}}}\n{BARE_BODIES[key]}\n\\end{{{env}}}"
    doc = PROFILES[key].build_document(body, standalone=True)
    assert doc.count(f"\\begin{{{env}}}") == 1, (
        f"profile {key!r} wrapped a body that already opened {env!r}; the "
        f"inner \\end closes the outer \\begin and the compile dies with a "
        f"mismatched-environment error"
    )


# ---------------------------------------------------------------------------
# The defect structural tests could not see: does a body actually compile?
# ---------------------------------------------------------------------------
@needs_forest
def test_bare_forest_body_compiles():
    _assert_rendered(_render("forest", BARE_BODIES["forest"]), "bare forest tree")


@needs_bussproofs
def test_bare_bussproofs_body_compiles():
    """The regression guard for "Not allowed in LR mode".

    Unpatched, this fails for EVERY bussproofs body, because ``prooftree``
    expands to a ``center`` environment and ``standalone`` typesets its body in
    an ``\\hbox``.  There is no body that works around it, which is why the
    remedy is in the profile preamble rather than in guidance to the model.
    """
    _assert_rendered(
        _render("bussproofs", BARE_BODIES["bussproofs"]), "bare proof tree"
    )


@needs_bussproofs
def test_self_delimited_bussproofs_body_compiles():
    """The passthrough path must survive the redefined environment too."""
    body = (
        "\\begin{prooftree}\n"
        "\\AxiomC{$A$}\\UnaryInfC{$A \\vee B$}\n"
        "\\end{prooftree}"
    )
    _assert_rendered(_render("bussproofs", body), "self-delimited proof tree")


@needs_bussproofs
def test_sequent_form_compiles_with_a_turnstile_from_the_preamble():
    """bussproofs' no-C commands align on ``\\fCenter``.

    Its package default is ``\\relax``, so the antecedent and succedent abut
    with nothing between them -- a proof that renders and is wrong.  The body
    cannot fix it (see test_macro_definitions_are_rejected_in_bodies), so the
    profile must.
    """
    body = "\\Axiom$\\Gamma \\fCenter A$\n\\UnaryInf$\\Gamma \\fCenter A \\vee B$"
    _assert_rendered(_render("bussproofs", body), "sequent-style proof")


@needs_bussproofs
def test_bussproofs_abbreviations_and_amsmath_compile():
    body = (
        "\\EnableBpAbbreviations\n"
        "\\AXC{$\\dfrac{a}{b} \\in \\mathbb{Q}$}\n"
        "\\UIC{$\\exists q \\in \\mathbb{Q}$}"
    )
    _assert_rendered(_render("bussproofs", body), "abbreviated proof with amsmath")


@needs_linguistics
def test_forest_roof_compiles():
    """``roof`` is in forest-lib-linguistics, which forest does not auto-load."""
    body = "[S [NP, roof [the big dog]] [VP [barks]]]"
    _assert_rendered(_render("forest", body), "forest tree with a roof")


@needs_forest
def test_forest_movement_arrow_compiles():
    """A trailing ``\\draw`` between named nodes, with an arrows.meta tip.

    Two separate things are asserted by one body: that forest permits TikZ
    after the bracket (it does -- the nodes are in scope), and that the profile
    declares ``arrows.meta``, without which ``-Stealth`` is a fatal "Unknown
    arrow tip kind".  A bare ``->`` would pass without the library and so
    would not test the declaration.
    """
    body = (
        "[CP [DP, name=wh [what]] [C [VP [V [saw]] [DP, name=gap [$t$]]]]]\n"
        "\\draw[-Stealth, dashed] (gap) to[out=south west, in=south] (wh);"
    )
    _assert_rendered(_render("forest", body), "forest tree with a movement arrow")


@needs_forest
def test_forest_svg_colour_name_compiles():
    """D-004 as a compile, not just as package ordering."""
    body = "[S, draw, Crimson [NP, text=Navy [dog]] [VP]]"
    _assert_rendered(_render("forest", body), "forest tree with SVG colour names")


# ---------------------------------------------------------------------------
# Why the two remedies above must live in the profile and not in the body
# ---------------------------------------------------------------------------
@needs_tex
@pytest.mark.parametrize("primitive", (
    "\\def\\fCenter{\\vdash}",
    "\\newcommand{\\fCenter}{\\vdash}",
    "\\renewcommand{\\fCenter}{\\vdash}",
))
def test_macro_definitions_are_rejected_in_bodies(primitive):
    """The premise of the preamble-side fixes.

    If a body could define macros, ``\\fCenter`` would be the author's problem
    and the profile would have no business setting it.  The prescan rejects
    every definition primitive, so the profile is the only place it can come
    from.  Asserted rather than assumed, because if the prescan were ever
    relaxed the reasoning behind the preamble entry changes.
    """
    result = _render("bussproofs", f"{primitive}\n\\AxiomC{{$A$}}\\UnaryInfC{{$B$}}")
    assert not result.ok and result.error_kind == "rejected", (
        f"{primitive!r} was not rejected by the prescan; the profile-side "
        f"\\fCenter definition rests on bodies being unable to supply one"
    )


# ---------------------------------------------------------------------------
# Preamble contents, so a fix cannot be silently reverted on a machine that
# skips the compile tests
# ---------------------------------------------------------------------------
def test_bussproofs_preamble_makes_prooftree_lr_safe():
    doc = PROFILES["bussproofs"].build_document(
        BARE_BODIES["bussproofs"], standalone=True
    )
    assert re.search(r"\\renewenvironment\s*\{prooftree\}", doc), (
        "the bussproofs profile does not redefine prooftree; its own "
        "definition wraps the proof in a center environment, which is vertical "
        "material and fatal under the standalone class's LR mode"
    )
    assert "\\DisplayProof" in doc, (
        "the redefined prooftree must still emit \\DisplayProof or no proof is "
        "typeset at all"
    )


def test_bussproofs_preamble_defines_the_sequent_separator():
    doc = PROFILES["bussproofs"].build_document(
        BARE_BODIES["bussproofs"], standalone=True
    )
    assert re.search(r"\\renewcommand\s*\{\\fCenter\}", doc), (
        "the bussproofs profile does not set \\fCenter; the package default is "
        "\\relax, so a sequent proof renders with no turnstile -- wrong output "
        "rather than an error"
    )
    assert "vdash" in doc, "\\fCenter is set to something other than a turnstile"


def test_forest_preamble_loads_the_linguistics_library():
    doc = PROFILES["forest"].build_document(BARE_BODIES["forest"], standalone=True)
    m = re.search(r"\\useforestlibrary\{([^}]*)\}", doc)
    assert m, (
        "the forest profile loads no forest library, so `roof` -- the triangle "
        "over an elided constituent -- is an unknown pgfkeys key"
    )
    assert "linguistics" in m.group(1)
    # \useforestlibrary is preamble-only and needs forest already loaded.
    assert doc.find("\\usepackage{forest}") < m.start(), (
        "\\useforestlibrary appears before \\usepackage{forest}"
    )


def test_forest_declares_the_arrow_tip_library():
    profile = PROFILES["forest"]
    assert "arrows.meta" in profile.libraries, (
        "forest does not declare arrows.meta; a movement arrow written with a "
        "named tip (-Stealth, the common choice) is a fatal 'Unknown arrow tip "
        "kind'"
    )


# ---------------------------------------------------------------------------
# D-004: xcolor must be pre-loaded with the extended name sets, and FIRST
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key", TREE_PROFILE_KEYS)
def test_xcolor_is_loaded_with_extended_names_before_the_profile_package(key):
    profile = PROFILES[key]
    doc = profile.build_document(BARE_BODIES[key], standalone=True)

    xcolor = re.search(r"\\usepackage\[[^\]]*svgnames[^\]]*\]\{xcolor\}", doc)
    assert xcolor, (
        f"profile {key!r} does not load xcolor with svgnames; a CSS/SVG colour "
        f"name on a node is then a fatal 'Undefined color' (D-004)"
    )

    own = doc.find(f"\\usepackage{{{key}}}")
    assert own != -1, f"profile {key!r} does not load its own package {key}"
    assert xcolor.start() < own, (
        f"profile {key!r} loads xcolor AFTER {key}; xcolor is a once-only "
        f"package, so the unoptioned load wins and the extended colour names "
        f"are unavailable"
    )


# ---------------------------------------------------------------------------
# Registry-wide invariant: every profile can report an actionable install
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key", sorted(PROFILES))
def test_every_profile_can_report_a_missing_install(key):
    """Absent packages must yield a ``tlmgr install`` line, not a bare failure.

    Deliberately over the whole registry rather than the two new profiles:
    neither forest nor bussproofs ships with a basic install, so this path is
    their normal first experience, and the same is true of any future profile.
    """
    profile = PROFILES[key]
    assert profile.probe_files, (
        f"profile {key!r} declares no probe_files, so the renderer cannot tell "
        f"'not installed' from 'broken diagram' and shows an error instead of "
        f"an install notice"
    )
    assert profile.tl_packages, (
        f"profile {key!r} declares no tl_packages, so the install notice "
        f"cannot name anything to install"
    )
