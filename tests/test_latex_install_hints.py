"""Install-hint metadata must cover the GLOBALLY loaded optional packages.

The defect this pins is a second-order consequence of the shared-preamble fix
(see tests/test_latex_math_packages.py).  ``siunitx`` became a package loaded
for every LaTeX profile, but guarded with ``\\IfFileExists`` so it degrades
instead of aborting when absent.  Degrading silently is the right failure mode
ONLY IF the user is told how to stop it degrading -- and the ``tlmgr install``
line in the not-installed notice was built purely from PER-PROFILE metadata
(``tl_packages`` + ``optional_tl_packages``).

So on an install without siunitx, ``\\si{\\watt}`` in a pgfplots axis label
silently produced no unit, and nothing anywhere told the user which package to
install.  Only ``circuitikz`` named siunitx, and only because it happens to
require it for its own reasons.

The fix mirrors the preamble fix: a module-level base list, surfaced through
``LatexProfile.effective_optional_tl_packages`` so a profile added in future
inherits the install hint without its author knowing this file exists.
"""
import pytest

from app.services.latex_profiles import (
    PROFILES,
    _BASE_OPTIONAL_PACKAGES,
    _BASE_OPTIONAL_TL_PACKAGES,
    install_command,
)

ALL_PROFILE_KEYS = sorted(PROFILES)


# ---------------------------------------------------------------------------
# The base list reaches every profile
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", ALL_PROFILE_KEYS)
def test_every_profile_advertises_siunitx_in_its_install_hint(key):
    """Whole-registry, so a NEW profile inherits this without extra wiring.

    Parametrised rather than asserted on a single profile deliberately: the
    original bug was one profile (circuitikz) happening to name siunitx while
    the others did not.
    """
    assert "siunitx" in PROFILES[key].effective_optional_tl_packages, (
        f"profile {key!r} loads siunitx (guarded, via _BASE_OPTIONAL_PACKAGES) "
        "but does not advertise it in its install hint, so a user whose TeX "
        "lacks siunitx gets a silently unit-less label and no way to fix it"
    )


@pytest.mark.parametrize("key", ALL_PROFILE_KEYS)
def test_effective_list_has_no_duplicates(key):
    """circuitikz declares siunitx itself; the merge must not double it."""
    eff = PROFILES[key].effective_optional_tl_packages
    assert len(eff) == len(set(eff)), f"duplicate entries for {key!r}: {eff}"


def test_profile_declared_optional_packages_are_preserved():
    """The merge must ADD to per-profile metadata, never replace it.

    chemfig's mhchem is the case that would regress: it unlocks \\ce{}
    equations and must stay in the install line.
    """
    assert "mhchem" in PROFILES["chemfig"].effective_optional_tl_packages


def test_circuitikz_keeps_siunitx_as_a_hard_requirement():
    """siunitx is genuinely REQUIRED for circuitikz (its labels lean on \\SI),
    so it must stay in tl_packages there -- i.e. it may still be reported as a
    reason a circuit render failed.  The base list adds a hint for the other
    profiles; it must not downgrade circuitikz's own requirement.
    """
    assert "siunitx" in PROFILES["circuitikz"].tl_packages


# ---------------------------------------------------------------------------
# The invariant that prevents the next drift
# ---------------------------------------------------------------------------

def test_every_globally_guarded_package_has_an_install_hint_entry():
    """The actual lesson, encoded.

    The bug was a guarded global load with no matching install metadata.  Any
    future addition to _BASE_OPTIONAL_PACKAGES reintroduces it unless the TL
    name is added too -- this fails the moment the two lists disagree.
    """
    guarded = {p.name for p in _BASE_OPTIONAL_PACKAGES}
    advertised = set(_BASE_OPTIONAL_TL_PACKAGES)
    missing = guarded - advertised
    assert not missing, (
        f"package(s) {sorted(missing)} are loaded for every profile but absent "
        "from _BASE_OPTIONAL_TL_PACKAGES, so no install hint mentions them"
    )


# ---------------------------------------------------------------------------
# End to end: the rendered notice
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", ALL_PROFILE_KEYS)
def test_rendered_tlmgr_line_names_siunitx(key):
    """Asserts on the STRING the user is shown, not just the metadata."""
    profile = PROFILES[key]
    needed = list(profile.tl_packages) + list(
        profile.effective_optional_tl_packages)
    line = install_command(needed)
    assert line.startswith("tlmgr install ")
    assert "siunitx" in line
    # install_command de-dupes via sorted(set(...)), so circuitikz -- which
    # names siunitx in BOTH lists -- must still mention it exactly once.
    assert line.split().count("siunitx") == 1
