"""Shared math/unit preamble: amsmath, amssymb and siunitx reach every profile.

Two defects of one class motivated this, both found by rendering and both
FATAL (no image at all, for an otherwise valid body):

  * NO profile declared amsmath, so \\dfrac in an axis label or legend entry,
    \\text in a TikZ node, or \\boldsymbol in a chemfig label died with
    "Undefined control sequence".  Reproduced identically under `pgfplots`
    and `tikz`.
  * only the `circuitikz` profile declared siunitx, so \\si{\\watt} in a
    pgfplots xlabel -- the canonical way to put a unit on an axis -- died the
    same way.

Both gaps belong to every profile that typesets a label, so the fix lives in
shared machinery and these tests assert over the WHOLE registry: a profile
added later must inherit the preamble without its author knowing this
happened.
"""
import pytest

from app.services.latex_profiles import (
    PROFILES,
    LatexPackage,
    LatexProfile,
    _BASE_MATH_PACKAGES,
    _BASE_OPTIONAL_PACKAGES,
)

# A body with no math and no units: these packages must be unconditional, not
# gated on sniffing the body.  A sniffer would miss math/units arriving through
# a style or legend key rather than a literal $...$ in the body text.
PLAIN_BODY = "\\draw (0,0) -- (1,1);"

ALL_PROFILES = sorted(PROFILES)


@pytest.mark.parametrize("key", ALL_PROFILES)
@pytest.mark.parametrize("pkg", ("amsmath", "amssymb"))
def test_every_profile_loads_the_math_packages(key, pkg):
    doc = PROFILES[key].build_document(PLAIN_BODY, standalone=True)
    assert f"\\usepackage{{{pkg}}}" in doc, (
        f"profile {key!r} does not load {pkg}; an amsmath/amssymb command in a "
        f"label aborts the render with no image"
    )


@pytest.mark.parametrize("key", ALL_PROFILES)
def test_every_profile_can_typeset_units(key):
    # Either a required load (circuitikz declares its own) or the shared
    # guarded one -- but \si must resolve somewhere for every profile.
    doc = PROFILES[key].build_document(PLAIN_BODY, standalone=True)
    assert ("\\usepackage{siunitx}" in doc
            or "\\IfFileExists{siunitx.sty}" in doc), (
        f"profile {key!r} cannot typeset \\si/\\SI/\\qty"
    )


@pytest.mark.parametrize("key", ALL_PROFILES)
def test_shared_packages_stay_in_the_preamble(key):
    # A \usepackage after \begin{document} is itself fatal, so pin that the
    # insertion points did not drift past it.
    doc = PROFILES[key].build_document(PLAIN_BODY, standalone=True)
    body_start = doc.index("\\begin{document}")
    assert doc.index("\\usepackage{amsmath}") < body_start
    unit = ("\\usepackage{siunitx}" if "\\usepackage{siunitx}" in doc
            else "\\IfFileExists{siunitx.sty}")
    assert doc.index(unit) < body_start


@pytest.mark.parametrize("key", ALL_PROFILES)
def test_amsmath_precedes_siunitx(key):
    """siunitx documents that amsmath should be loaded before it.

    This is the reason the shared math packages are emitted FIRST rather than
    after the profile's own list: circuitikz declares siunitx itself, so an
    "after" placement would have put siunitx ahead of amsmath there.
    """
    doc = PROFILES[key].build_document(PLAIN_BODY, standalone=True)
    unit = ("\\usepackage{siunitx}" if "\\usepackage{siunitx}" in doc
            else "\\IfFileExists{siunitx.sty}")
    assert doc.index("\\usepackage{amsmath}") < doc.index(unit)


@pytest.mark.parametrize("key", ALL_PROFILES)
def test_optioned_xcolor_still_wins_the_once_only_load(key):
    """The D-004 contract, stated as what it actually is.

    xcolor is a once-only package, so a profile needing CSS/SVG colour names
    must load it WITH options before its own package requests it unoptioned.
    The contract is about beating that request -- not about being the literally
    first \\usepackage in the file -- so it is asserted against the profile's
    other packages.  amsmath/amssymb sitting earlier is harmless because
    neither requests xcolor (verified: they pull only amsbsy, amsfonts, amsopn
    and amstext).
    """
    profile = PROFILES[key]
    xcolor = [p for p in profile.packages if p.name == "xcolor" and p.options]
    if not xcolor:
        pytest.skip(f"{key} does not pre-load an optioned xcolor")
    doc = profile.build_document(PLAIN_BODY, standalone=True)
    xcolor_at = doc.index(xcolor[0].render())
    for pkg in profile.packages:
        if pkg.name == "xcolor":
            continue
        assert xcolor_at < doc.index(pkg.render()), (
            f"{key}: optioned xcolor no longer precedes {pkg.name}; CSS/SVG "
            f"colour names will abort as 'Undefined color'"
        )


def test_profile_declaring_amsmath_itself_is_not_double_loaded():
    """Guards against a fatal "Option clash for package amsmath".

    A profile that later needs amsmath WITH options must win; emitting the
    shared unoptioned load as well makes that optioned load fatal.  The dedupe
    is load-bearing, not tidiness.
    """
    doc = LatexProfile(
        key="fake",
        packages=(LatexPackage("amsmath", "intlimits"),),
    ).build_document(PLAIN_BODY, standalone=True)
    assert "\n\\usepackage{amsmath}\n" not in doc
    assert "\\usepackage[intlimits]{amsmath}" in doc
    # amssymb was not declared, so it is still supplied.
    assert "\n\\usepackage{amssymb}\n" in doc


def test_required_siunitx_suppresses_the_guarded_one():
    # circuitikz's real shape: siunitx declared as REQUIRED.  The shared
    # guarded load must stand down rather than load it a second time.
    doc = PROFILES["circuitikz"].build_document(PLAIN_BODY, standalone=True)
    assert "\\usepackage{siunitx}" in doc
    assert "\\IfFileExists{siunitx.sty}" not in doc


def test_optional_declaration_also_suppresses_the_shared_load():
    # Same clash risk via optional_packages.
    #
    # Asserted on the standalone emitted LINE, not a substring count: the
    # \IfFileExists wrapper names the package twice (amssymb.sty and
    # \usepackage{amssymb}), so counting substrings passes whether or not the
    # shared load was suppressed.  That is the assertion that was wrong the
    # first time this file was written.
    doc = LatexProfile(
        key="fake",
        optional_packages=(LatexPackage("amssymb"),),
    ).build_document(PLAIN_BODY, standalone=True)
    assert "\n\\usepackage{amssymb}\n" not in doc
    assert "\\IfFileExists{amssymb.sty}" in doc


def test_shared_packages_are_unoptioned():
    # An option on an always-on shared load is what CREATES clash risk for
    # every profile at once; keep them bare.
    for pkg in _BASE_MATH_PACKAGES + _BASE_OPTIONAL_PACKAGES:
        assert pkg.options == "", (
            f"{pkg.name} carries options in a shared list; that makes any "
            f"profile-level load of it fatal"
        )


def test_unit_package_is_guarded_not_required():
    """siunitx must never be able to break a render.

    It does not ship in every minimal install, and an unconditional load would
    take down every diagram of the type -- including ones using no units --
    which is exactly the chemmacros failure recorded in
    LatexPackage.render_optional.  amsmath/amssymb are required instead
    because they DO ship in scheme-basic.
    """
    assert [p.name for p in _BASE_OPTIONAL_PACKAGES] == ["siunitx"]
    doc = PROFILES["pgfplots"].build_document(PLAIN_BODY, standalone=True)
    assert "\\IfFileExists{siunitx.sty}" in doc
    assert "\n\\usepackage{siunitx}\n" not in doc


def test_svg_driver_override_stays_first():
    # The dvisvgm driver \def must precede every \usepackage; inserting shared
    # packages ahead of the profile list must not have jumped it.
    doc = PROFILES["pgfplots"].build_document(PLAIN_BODY, standalone=True,
                                              fmt="svg")
    assert doc.index("pgfsys-dvisvgm.def") < doc.index("\\usepackage")
