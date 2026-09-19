r"""
Regression test for D-359 (unknown-bipole-key-fatal), fix group G-ff699b.

circuitikz-w4-13 mixes component-key dialects in one body and includes the
run-together guess ``to[americanresistor=$R_3$]``.  circuitikz defines no
single-token ``americanresistor`` key (the real spelling is ``american
resistor`` or the short ``R``), so pgfkeys aborts the whole compile on the
unknown key -- no image at all.  ``circuitikz_lint._fix_bipole_key_aliases``
rewrites just that key token to the canonical short form ``R`` while leaving
the value and every valid sibling key untouched.

The assertions carry a direction check (the raw spec exhibits the unknown key)
so they certify the bug, not merely current behaviour, and import the REAL
modules under test.  This is a recovery (compile-structure) defect, not a theme
defect, so it is theme-independent by construction.
"""
import json
from pathlib import Path

from app.services.latex_renderer import LatexRenderer
from app.utils.circuitikz_lint import autofix as circuitikz_autofix

_SPEC_DIR = Path(__file__).resolve().parents[1] / ".ziya" / "gfx-sweep" / "specs" / "circuitikz"


def _load(spec_id: str) -> str:
    return json.loads((_SPEC_DIR / f"{spec_id}.json").read_text())["definition"]


def test_d359_w4_13_unknown_american_bipole_key_rewritten_from_spec():
    raw = _load("circuitikz-w4-13")

    # Direction: the raw body names the unknown one-word key that aborts pgfkeys.
    assert "americanresistor" in raw

    sanitized = LatexRenderer._sanitize_input(raw)
    fixed, applied, _ = circuitikz_autofix(sanitized)

    # The unknown key is gone, rewritten to the canonical short form.
    assert "americanresistor" not in fixed
    assert "to[R=$R_3$]" in fixed
    assert any("bipole key" in note for note in applied)

    # Subtractive-elsewhere: the valid sibling keys in the same body survive.
    assert "to[resistor=$R_1$]" in fixed
    assert "to[R=$R_2$]" in fixed
    assert "to[capacitor=$C_1$]" in fixed
    assert "to[C=$C_2$]" in fixed


def test_d359_all_american_aliases_map_to_short_keys():
    body = (
        r"\draw (0,0) to[americanresistor=$R$] (2,0)"
        r" to[americaninductor=$L$] (4,0) to[americancapacitor=$C$] (6,0);"
    )
    fixed, applied, _ = circuitikz_autofix(body)
    assert "to[R=$R$]" in fixed
    assert "to[L=$L$]" in fixed
    assert "to[C=$C$]" in fixed
    assert len(applied) == 3


def test_d359_valid_body_untouched_and_idempotent():
    """A body that names only valid keys (incl. the two-word ``american
    resistor``) is left byte-for-byte alone, and a second pass is a no-op."""
    valid = (
        r"\draw (0,0) to[R=$R_1$] (2,0) to[american resistor] (4,0)"
        r" to[C] (6,0) node[americanresistor label]{};"
    )
    once, applied, _ = circuitikz_autofix(valid)
    # node[...] is not a bipole option list, so an american* token there is
    # never rewritten; the two-word key and short keys are already valid.
    assert once == valid
    assert applied == ()
    twice, _, _ = circuitikz_autofix(once)
    assert twice == once
