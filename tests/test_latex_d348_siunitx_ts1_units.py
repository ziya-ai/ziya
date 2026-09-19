r"""D-348 / G-1722c9 -- siunitx units that route through the absent TS1
companion font must be rerouted to maths symbols (circuitikz-w1-03).

Backstory
---------
D-056 already redefined ``\SIUnitSymbolMicro`` after the siunitx load, but that
symbol macro is a siunitx *v2* hook.  Under siunitx *v3* (TeX Live 2026) the
unit macros ``\micro`` AND ``\ohm`` (also ``\celsius`` / ``\degree``) emit the
TS1 text-companion glyphs (``\textmu`` / ``\textohm`` / ...) directly, bypassing
the symbol hooks.  The Type1 font backing TS1 (``tcrm*``) is absent from the
TeX Live basic tree and cannot be generated inside the render sandbox, so
``\SI{10}{\micro\farad}`` next to ``\SI{4.7}{\kilo\ohm}`` still aborts with
"Font tcrm1000 at 600 not found -> no output PDF".

Fix (``LatexProfile.build_document``): after siunitx loads, redefine the
offending UNITS themselves via ``\DeclareSIUnit`` to their maths-font
equivalents (``\mu`` / ``\Omega`` / ``\circ``), so TS1 is never touched
regardless of the siunitx major version.  Guarded by ``\ifdefined\DeclareSIUnit``
so it is a no-op when siunitx is absent.

Structural (a fatal missing-font abort that never reaches the rasteriser), so
theme-independent: the redefinition is emitted identically on both paths.
These tests FAIL on the pre-fix profile -- it emits only the v2 symbol hook,
which does not stop the tcrm1000 abort.
"""
import struct

import pytest

from app.services.latex_profiles import get_profile
from app.services.latex_renderer import LatexRenderer

_BODY = (r"\draw (0,0) to[V=\SI{12}{\volt}] (0,3) to[R=\SI{4.7}{\kilo\ohm}] "
         r"(3,3) to[C=\SI{10}{\micro\farad}] (3,0) to[L=\SI{2.2}{\milli\henry}] (0,0);")


def _doc(theme: str) -> str:
    profile = get_profile("circuitikz")
    assert profile is not None
    return profile.build_document(_BODY, standalone=True, fmt="png", theme=theme)


def test_unit_redefinitions_present_both_themes():
    for theme in ("light", "dark"):
        doc = _doc(theme)
        assert r"\ifdefined\DeclareSIUnit" in doc, f"missing on {theme}"
        assert r"\DeclareSIUnit\micro{\ensuremath{\mu}}" in doc
        assert r"\DeclareSIUnit\ohm{\ensuremath{\Omega}}" in doc
        # redefinition must follow the siunitx load, or it is undefined/clobbered
        assert doc.index("{siunitx}") < doc.index(r"\DeclareSIUnit\micro")


@pytest.mark.timeout(60)
def test_microfarad_and_kiloohm_schematic_compiles_both_themes():
    r"""End-to-end: the canonical analog schematic renders in both themes.

    Pre-fix this aborts with the tcrm1000 font error and returns no image
    because \ohm still routes through TS1 even with the v2 micro hook."""
    renderer = LatexRenderer()
    cap = renderer.probe()
    if not cap.available:
        pytest.skip("LaTeX toolchain unavailable")
    for theme in ("light", "dark"):
        res = renderer.render("circuitikz", _BODY, fmt="png", theme=theme)
        assert res.ok, f"{theme}: {res.error_kind}: {res.error[:120]}"
        # A real PNG with non-trivial dimensions was produced.
        assert res.content[:8] == b"\x89PNG\r\n\x1a\n"
        w, h = struct.unpack(">II", res.content[16:24])
        assert w > 0 and h > 0
