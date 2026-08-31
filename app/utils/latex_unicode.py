r"""
Unicode symbol transliteration for model-authored LaTeX bodies (D-005).

The problem
-----------
A model labels a circuit or structure with the symbol a human would read --
``\micro`` sign, degree, ohm, arrows, the true minus sign -- as a raw Unicode
codepoint (``5 µF``, ``90°``, ``10 Ω``, ``A → B``, ``−5 V``).  Stock (pdf)LaTeX
routes most of these through the TS1 / text-companion encoding, whose Type1
fonts (``tcrm*``) are ABSENT from a BasicTeX / minimal TeX Live install, so the
compile dies with "Font ... not set up for use" / "Unicode character not set up
for use with LaTeX" and produces no image -- for a diagram that is otherwise
perfectly valid.

The fix
-------
Rewrite each such codepoint to a MATH-mode macro wrapped in ``\ensuremath``
BEFORE the compile.  This deliberately sidesteps the missing TS1 fonts entirely:
``\ensuremath{\mu}`` / ``\ensuremath{^\circ}`` / ``\ensuremath{\Omega}`` render
from the maths fonts (``cmmi`` / ``cmsy``), which every install ships, rather
than from the text-companion fonts, which a minimal install does not.  It is
also context-safe: ``\ensuremath`` typesets its body in maths mode whether the
surrounding text is maths or not, so the same substitution is correct inside and
outside ``$...$``.  ``U+2212`` (true minus) collapses to an ASCII ``-``.

Genuinely unsupported scripts (CJK, emoji) are intentionally NOT in the table:
there is no maths-font equivalent, so they still fail -- with the existing
actionable "no font for non-Latin scripts" message from
``latex_renderer._extract_error`` -- rather than being silently corrupted.

Contract: advisory only.  ``transliterate(body) -> (body, applied)`` degrades
to ``(body, ())`` on any fault and never raises, so a table defect cannot break
a render that would otherwise succeed.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


#: Codepoint -> replacement.  Every replacement is either plain ASCII or an
#: ``\ensuremath{...}`` maths macro, so none depends on the absent TS1 fonts.
_TRANSLITERATIONS: dict[str, str] = {
    # micro / mu
    "\u00b5": r"\ensuremath{\mu}",   # µ MICRO SIGN
    "\u03bc": r"\ensuremath{\mu}",   # μ GREEK SMALL LETTER MU
    # degree
    "\u00b0": r"\ensuremath{^\circ}",  # °
    # ohm
    "\u2126": r"\ensuremath{\Omega}",  # Ω OHM SIGN
    "\u03a9": r"\ensuremath{\Omega}",  # Ω GREEK CAPITAL OMEGA
    # arithmetic / relations
    "\u2212": "-",                     # − MINUS SIGN
    "\u00d7": r"\ensuremath{\times}",  # ×
    "\u00f7": r"\ensuremath{\div}",    # ÷
    "\u00b1": r"\ensuremath{\pm}",     # ±
    "\u2213": r"\ensuremath{\mp}",     # ∓
    "\u2260": r"\ensuremath{\neq}",    # ≠
    "\u2264": r"\ensuremath{\leq}",    # ≤
    "\u2265": r"\ensuremath{\geq}",    # ≥
    "\u2248": r"\ensuremath{\approx}", # ≈
    "\u221e": r"\ensuremath{\infty}",  # ∞
    "\u221a": r"\ensuremath{\surd}",   # √
    "\u2211": r"\ensuremath{\sum}",    # ∑
    "\u220f": r"\ensuremath{\prod}",   # ∏
    "\u222b": r"\ensuremath{\int}",    # ∫
    "\u2202": r"\ensuremath{\partial}",# ∂
    "\u2207": r"\ensuremath{\nabla}",  # ∇
    "\u00b7": r"\ensuremath{\cdot}",   # ·
    "\u2022": r"\ensuremath{\bullet}", # •
    "\u2032": r"\ensuremath{\prime}",  # ′
    # arrows
    "\u2192": r"\ensuremath{\rightarrow}",     # →
    "\u2190": r"\ensuremath{\leftarrow}",      # ←
    "\u2194": r"\ensuremath{\leftrightarrow}", # ↔
    "\u21d2": r"\ensuremath{\Rightarrow}",     # ⇒
    "\u21d0": r"\ensuremath{\Leftarrow}",      # ⇐
    "\u21d4": r"\ensuremath{\Leftrightarrow}", # ⇔
    "\u2191": r"\ensuremath{\uparrow}",        # ↑
    "\u2193": r"\ensuremath{\downarrow}",      # ↓
    # common Greek (labels: α, β, ω, θ, λ, ...)
    "\u03b1": r"\ensuremath{\alpha}",
    "\u03b2": r"\ensuremath{\beta}",
    "\u03b3": r"\ensuremath{\gamma}",
    "\u03b4": r"\ensuremath{\delta}",
    "\u03b5": r"\ensuremath{\epsilon}",
    "\u03b8": r"\ensuremath{\theta}",
    "\u03bb": r"\ensuremath{\lambda}",
    "\u03c0": r"\ensuremath{\pi}",
    "\u03c1": r"\ensuremath{\rho}",
    "\u03c3": r"\ensuremath{\sigma}",
    "\u03c4": r"\ensuremath{\tau}",
    "\u03c6": r"\ensuremath{\phi}",
    "\u03c9": r"\ensuremath{\omega}",
    "\u0394": r"\ensuremath{\Delta}",
    "\u03a3": r"\ensuremath{\Sigma}",
    "\u03a6": r"\ensuremath{\Phi}",
    "\u03a0": r"\ensuremath{\Pi}",
    # fractions
    "\u00bd": r"\ensuremath{\tfrac{1}{2}}",  # ½
    "\u00bc": r"\ensuremath{\tfrac{1}{4}}",  # ¼
    "\u00be": r"\ensuremath{\tfrac{3}{4}}",  # ¾
    # superscripts (labels such as ``mol⁻¹``, ``m²``, ``10⁻³``, ``xⁿ``).  Each
    # is a TS1 / text-companion glyph that a minimal TeX install cannot set
    # (the missing ``tcrm*`` fonts), so ``kJ·mol⁻¹`` aborted the whole render;
    # routing them through a maths superscript ``^{...}`` sidesteps TS1 exactly
    # as the symbols above do.  ``\ensuremath{^-}`` is a bare superscript on an
    # empty base, which compiles the same way the existing ``\ensuremath{^\circ}``
    # (degree) entry does.
    "\u2070": r"\ensuremath{^0}",   # ⁰
    "\u00b9": r"\ensuremath{^1}",   # ¹ SUPERSCRIPT ONE
    "\u00b2": r"\ensuremath{^2}",   # ² SUPERSCRIPT TWO
    "\u00b3": r"\ensuremath{^3}",   # ³ SUPERSCRIPT THREE
    "\u2074": r"\ensuremath{^4}",   # ⁴
    "\u2075": r"\ensuremath{^5}",   # ⁵
    "\u2076": r"\ensuremath{^6}",   # ⁶
    "\u2077": r"\ensuremath{^7}",   # ⁷
    "\u2078": r"\ensuremath{^8}",   # ⁸
    "\u2079": r"\ensuremath{^9}",   # ⁹
    "\u207a": r"\ensuremath{^+}",   # ⁺
    "\u207b": r"\ensuremath{^-}",   # ⁻ SUPERSCRIPT MINUS
    "\u207c": r"\ensuremath{^=}",   # ⁼
    "\u207d": r"\ensuremath{^(}",   # ⁽
    "\u207e": r"\ensuremath{^)}",   # ⁾
    "\u207f": r"\ensuremath{^n}",   # ⁿ
    # subscripts (chemistry labels such as ``H₂O``, ``CO₂``, ``SO₄``) -- the
    # same TS1 hazard, routed through a maths subscript ``_{...}``.
    "\u2080": r"\ensuremath{_0}",   # ₀
    "\u2081": r"\ensuremath{_1}",   # ₁
    "\u2082": r"\ensuremath{_2}",   # ₂
    "\u2083": r"\ensuremath{_3}",   # ₃
    "\u2084": r"\ensuremath{_4}",   # ₄
    "\u2085": r"\ensuremath{_5}",   # ₅
    "\u2086": r"\ensuremath{_6}",   # ₆
    "\u2087": r"\ensuremath{_7}",   # ₇
    "\u2088": r"\ensuremath{_8}",   # ₈
    "\u2089": r"\ensuremath{_9}",   # ₉
    "\u208a": r"\ensuremath{_+}",   # ₊
    "\u208b": r"\ensuremath{_-}",   # ₋
    # spacing that would otherwise route through TS1 / an absent glyph
    "\u2009": r"\,",                # THIN SPACE
    "\u00a0": " ",                  # NO-BREAK SPACE -> ordinary space
}


def transliterate(body: str) -> tuple[str, tuple[str, ...]]:
    """Replace supported Unicode technical symbols with maths-font macros.

    Returns ``(new_body, applied)``.  Only codepoints in the table are touched;
    unsupported scripts (CJK, emoji) are left in place so the renderer's
    existing "no font for non-Latin scripts" message still fires for them.
    Advisory: any fault degrades to ``(body, ())``.
    """
    try:
        applied: list[str] = []
        seen: set[str] = set()
        out = body
        for ch, repl in _TRANSLITERATIONS.items():
            if ch in out:
                out = out.replace(ch, repl)
                if ch not in seen:
                    seen.add(ch)
                    applied.append(
                        f"transliterated U+{ord(ch):04X} ({ch!r}) -> {repl} "
                        "(routes through the maths fonts, which a minimal TeX "
                        "install ships, instead of the absent TS1 fonts)")
        return out, tuple(applied)
    except Exception:                      # pragma: no cover - defensive
        logger.exception("latex unicode transliteration failed; body unchanged")
        return body, ()
