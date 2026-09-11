"""HTML-03 regression: LaTeX math in the HTML export must be RENDERED to KaTeX
markup, not left as literal ``$...$`` / ``$$...$$`` source text.

The defect (fixed in ``_markdown_to_html_basic`` via ``_render_math_batch``):
``_markdown_to_html_basic`` had no math handling — a ``$$\\int_0^1 ...$$`` block
and inline ``$E=mc^2$`` were treated as ordinary prose, HTML-escaped and dropped
into a ``<p>``. The fidelity harness probe measured ``katex_count == 0`` and
``check_math_rendering`` FAILED.

The fix extracts each math expression to a placeholder (AFTER code/inline-code
extraction so a ``$`` inside code is never mistaken for math, and BEFORE the
prose escape so the emitted markup is not double-escaped) and shells out to Node
+ KaTeX to render it to self-contained MathML (``output: "mathml"`` — native
browser math, no external fonts/CSS, so the standalone document stays
self-contained). It is defensive: when Node or the ``katex`` module is
unavailable, or an individual expression fails to render, that expression falls
back to its original escaped LaTeX text, so HTML export never hard-fails.

These are browser-free static assertions on the exported HTML source. The
end-to-end proof (``.katex`` elements in the rendered document) is the shared
fidelity apparatus check ``math_rendering`` (tests/export_fidelity).

Guardrails asserted here:
  * an inline ``$...$`` and a display ``$$...$$`` both render to KaTeX markup
    (``class="katex"`` / ``<math>``) with no leftover ``$$`` delimiters (the fix
    works);
  * the rendered math is self-contained (no ``url(`` font refs, no external
    stylesheet ``<link>``);
  * a ``$`` INSIDE a fenced/inline code span is NOT treated as math (the code's
    literal ``$`` survives, no KaTeX injected into code);
  * a ``<script>`` smuggled into a math expression is neutralized (no raw
    ``<script>`` tag, no ``javascript:`` href — CWE-79 / security intact).
"""
from __future__ import annotations

import re


def _export_html(content: str) -> str:
    from app.utils.conversation_exporter import export_conversation_for_paste

    messages = [
        {"role": "human", "content": "Show me some math."},
        {"role": "assistant", "content": content},
    ]
    result = export_conversation_for_paste(
        messages,
        format_type="html",
        target="public",
        version="9.9.9",
        model="test-model",
        provider="test-provider",
    )
    return result["content"]


def _katex_available() -> bool:
    from app.utils import conversation_exporter as ce

    return getattr(ce, "_KATEX_AVAILABLE", False)


_BT = "`" * 3

_MATH_CONTENT = (
    "Inline math $E = mc^2$ and a display block:\n\n"
    "$$\\int_0^1 x^2 \\, dx = \\frac{1}{3}$$\n"
)


def test_inline_and_display_math_render_to_katex():
    """An inline ``$...$`` and a display ``$$...$$`` both render to KaTeX
    markup, with no literal ``$$`` delimiters left in the output."""
    import pytest

    if not _katex_available():
        pytest.skip("KaTeX/node unavailable; fallback path exercised elsewhere")
    html = _export_html(_MATH_CONTENT)
    # at least the two fixture expressions rendered
    assert html.count('class="katex') >= 2, (
        f'expected >=2 KaTeX elements; got {html.count(chr(34) + "class=" + chr(34))} '
        f'(katex-class count {html.count("class=" + chr(34) + "katex")})'
    )
    assert "<math" in html, "expected MathML <math> markup"
    # the raw display delimiters must be gone (rendered, not literal source)
    assert "$$" not in html, "leftover literal $$ delimiters — math not rendered"


def test_rendered_math_is_self_contained():
    """KaTeX MathML output carries no external font/CSS dependency, so the
    standalone document stays self-contained."""
    import pytest

    if not _katex_available():
        pytest.skip("KaTeX/node unavailable")
    html = _export_html(_MATH_CONTENT)
    assert "url(" not in html, "math output must not reference external fonts via url()"
    assert not re.search(r"<link[^>]+stylesheet", html, re.IGNORECASE), (
        "math output must not add an external stylesheet link"
    )


def test_dollar_inside_code_is_not_treated_as_math():
    """A ``$`` inside a fenced code block is literal shell/text, NOT math — no
    KaTeX must be injected into code, and the ``$`` must survive."""
    content = (
        "A shell snippet:\n\n"
        + _BT + "bash\n"
        'echo "$HOME costs $5"\n'
        + _BT + "\n"
    )
    html = _export_html(content)
    # the literal dollar signs survive inside the code block
    m = re.search(r'<code class="language-bash">(.*?)</code>', html, re.DOTALL)
    assert m is not None
    assert "$HOME" in m.group(1) and "$5" in m.group(1), (
        "literal $ inside code must not be consumed as math"
    )
    # no KaTeX markup injected into the code region
    assert 'class="katex' not in m.group(1)


def test_script_in_math_is_neutralized():
    """A ``<script>`` / ``javascript:`` smuggled into a math expression is
    neutralized — KaTeX escapes tokens and rejects dangerous hrefs."""
    content = (
        "Sneaky math: $</span><script>alert(1)</script>$ and "
        "$\\href{javascript:alert(1)}{x}$\n"
    )
    html = _export_html(content)
    # no real executable <script> element
    assert not re.search(r"<script[\s>]", html.lower()), (
        "math path must not emit a raw <script> tag"
    )
    # no live javascript: href (KaTeX drops \\href to a dangerous scheme)
    assert not re.search(r'href\s*=\s*["\']javascript:', html.lower()), (
        "math path must not emit a javascript: href"
    )


def test_math_export_never_hard_fails_without_katex(monkeypatch):
    """With KaTeX forced unavailable the export still succeeds, degrading each
    expression to its escaped LaTeX text (the dual-mode contract)."""
    from app.utils import conversation_exporter as ce

    monkeypatch.setattr(ce, "_KATEX_AVAILABLE", False)
    html = _export_html(_MATH_CONTENT)
    # export produced a document (no crash) and the LaTeX survives as text
    assert "mc^2" in html or "mc<sup>2</sup>" in html or "E = mc" in html
    # no KaTeX markup when the renderer is unavailable
    assert 'class="katex' not in html


# ---------------------------------------------------------------------------
# Corrections the exporter could not previously apply.
#
# Normalization used to be reimplemented (partially) in Python here, so the
# exporter and the browser disagreed. It now runs inside the Node program via
# the SHARED module frontend/src/utils/mathSanitizer.js, which the browser also
# imports. The structural half of that contract lives in
# tests/test_math_sanitizer_parity.py; these tests assert the CONSEQUENCE in
# exported HTML.
#
# ``errorColor`` — not ``katex-error`` — is the marker throughout. KaTeX 0.16.x
# recovers from a single unresolvable TOKEN per token and emits no error span at
# all, so asserting on the class passes while the glyph is visibly red.
# ---------------------------------------------------------------------------

_ERROR_COLOR = "#cc0000"

_LEAKED_STAR_MATH = "$$s^\\* - c^\\* = t_{\\text{dead}} + (d_A - d_B)$$\n"

#: Each of these rendered cleanly in the browser but exported with red glyphs.
_PREVIOUSLY_DIVERGENT_DOCS = {
    "leaked markdown star": _LEAKED_STAR_MATH,
    "text underscore": "$$\\text{ct_id_field} = 1$$\n",
    "multline environment": "$$\\begin{multline} a + b \\\\ + c \\end{multline}$$\n",
}


def _assert_no_error_glyphs(html: str, label: str) -> None:
    assert "<math" in html, f"{label}: expression did not render at all"
    assert _ERROR_COLOR not in html, f"{label}: painted in errorColor"
    assert f'mathcolor="{_ERROR_COLOR}"' not in html, f"{label}: MathML error colour"


def test_render_math_batch_routes_through_the_shared_sanitizer():
    """The seam: ``_render_math_batch`` must sanitize, so no caller has to
    remember to. Uses the leaked ``\\*`` because it is the case KaTeX recovers
    from silently."""
    import pytest

    from app.utils import conversation_exporter as ce

    if not _katex_available():
        pytest.skip("KaTeX/node/sanitizer unavailable")
    (rendered,) = ce._render_math_batch([{"tex": "s^\\* - c^\\*", "display": True}])
    assert rendered is not None, "expression must still render"
    assert _ERROR_COLOR not in rendered
    assert "\\*" not in rendered


def test_render_math_batch_preserves_legitimate_latex_escapes():
    """Negative control: a broad "strip backslashes" fix would pass the test
    above and break real LaTeX. Each of these must still typeset."""
    import pytest

    from app.utils import conversation_exporter as ce

    if not _katex_available():
        pytest.skip("KaTeX/node/sanitizer unavailable")
    # \\* here is a KaTeX line break, not a leaked markdown escape.
    exprs = [
        "\\begin{gathered} a \\\\* b \\end{gathered}",
        "\\frac{a}{b} \\cdot c^2",
        "\\text{a\\_b}",
        "50\\%",
    ]
    rendered = ce._render_math_batch([{"tex": e, "display": True} for e in exprs])
    for expr, out in zip(exprs, rendered):
        assert out is not None, f"{expr!r} failed to render"
        assert _ERROR_COLOR not in out, f"{expr!r} painted in errorColor"


def test_previously_divergent_math_exports_without_error_glyphs():
    """End to end, for every correction the exporter used to lack."""
    import pytest

    if not _katex_available():
        pytest.skip("KaTeX/node/sanitizer unavailable")
    for label, doc in _PREVIOUSLY_DIVERGENT_DOCS.items():
        _assert_no_error_glyphs(_export_html(doc), label)


def test_leaked_star_does_not_survive_as_literal_text():
    """Beyond the colour: the ``\\*`` token itself must be gone, not merely
    painted a different colour."""
    import pytest

    if not _katex_available():
        pytest.skip("KaTeX/node/sanitizer unavailable")
    html = _export_html(_LEAKED_STAR_MATH)
    assert "\\*" not in html, "literal \\* survived into the export"
