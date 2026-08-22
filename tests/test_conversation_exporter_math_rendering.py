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
