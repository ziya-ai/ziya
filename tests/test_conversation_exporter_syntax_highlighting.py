"""HTML-01 regression: a language-tagged fenced code block in the HTML export
must render with SYNTAX HIGHLIGHTING (multiple distinctly-colored token spans),
not as one uniform color.

The defect (fixed in _markdown_to_html_basic via _highlight_code_block): every
non-diff fence was emitted as a bare ``<pre><code class="language-{lang}">``
whose escaped content carried a single uniform text color — the fidelity
harness probe measured 0 token spans / 1 distinct color and
``check_syntax_highlighting`` FAILED (needs >=1 span across >=2 colors).

The fix runs Pygments (pure-Python) over the code and emits inline-styled
``<span style="color:...">`` token spans (keyword / string / comment / ... hues)
inside ``<pre><code>``. It is defensive: when Pygments is unavailable, the
language is unknown, or the language is a visualization type (mermaid,
graphviz, ...) that a later pass renders from RAW source, it falls back to the
previous uncolored block so HTML export never hard-fails and viz rendering is
not corrupted.

These are browser-free static assertions on the exported HTML source. The
end-to-end proof (distinct COMPUTED token colors in the rendered document) is
the shared fidelity apparatus check ``syntax_highlighting``
(tests/export_fidelity).

Guardrails asserted here:
  * a ``python`` block yields multiple inline-styled token spans across >=2
    distinct declared colors (the fix works, and stays self-contained — inline
    styles, no external Prism CSS);
  * a ``mermaid`` (visualization) block is LEFT as raw ``language-mermaid``
    source with no token spans, so the downstream diagram renderer still sees
    its source intact;
  * an unknown language degrades gracefully to a plain escaped block (no crash,
    no spans);
  * a ``<script>`` inside a highlighted code block is HTML-escaped (CWE-79
    escaping still runs on the highlighted path — security intact).
"""
from __future__ import annotations

import re


def _export_html(content: str) -> str:
    from app.utils.conversation_exporter import export_conversation_for_paste

    messages = [
        {"role": "human", "content": "Show me some code."},
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


_BT = "`" * 3

_PY_CONTENT = (
    "A python block:\n\n"
    + _BT + "python\n"
    "def add(a, b):\n"
    "    # a comment\n"
    '    s = "hello"\n'
    "    return a + b\n"
    + _BT + "\n"
)


def _inline_span_colors(html: str):
    """Distinct declared text colors of inline-styled spans in the export."""
    return set(re.findall(r'<span style="[^"]*color:\s*(#[0-9a-fA-F]{3,6})', html))


def test_python_block_has_multiple_distinct_token_colors():
    """A python fence renders as inline-styled token spans across >=2 distinct
    colors (keyword/string/comment/...), not one uniform color."""
    import pytest

    if not _pygments_available():
        pytest.skip("pygments unavailable; fallback path exercised elsewhere")
    html = _export_html(_PY_CONTENT)
    colors = _inline_span_colors(html)
    assert len(colors) >= 2, (
        f"python code block should carry >=2 distinct token colors "
        f"(syntax highlighting); got {colors}"
    )
    # sanity: the highlighted block is still a language-python code block
    assert 'class="language-python"' in html


def test_python_block_is_self_contained_inline_styles():
    """Highlighting is inline (no external Prism stylesheet reference), so the
    standalone document stays self-contained."""
    import pytest

    if not _pygments_available():
        pytest.skip("pygments unavailable")
    html = _export_html(_PY_CONTENT)
    # token spans use inline style="color:..." rather than class="token ..."
    assert re.search(r'<span style="[^"]*color:', html), (
        "expected inline-styled token spans"
    )
    # no external stylesheet <link> (e.g. a Prism CDN sheet) — everything is
    # inlined, so the document is self-contained.
    assert not re.search(r'<link[^>]+stylesheet', html, re.IGNORECASE)
    # tokens are NOT class-based (which would need an external Prism sheet)
    assert 'class="token' not in html


def test_mermaid_block_is_not_highlighted():
    """A visualization language is left as raw ``language-mermaid`` source with
    NO token spans, so the downstream diagram renderer sees its source intact."""
    content = (
        "A mermaid diagram:\n\n"
        + _BT + "mermaid\n"
        "graph TD; A-->B;\n"
        + _BT + "\n"
    )
    html = _export_html(content)
    assert 'class="language-mermaid"' in html
    # the raw source survives (escaped), so the viz pass can render it
    assert 'graph TD; A--&gt;B;' in html or 'graph TD; A-->B;' in html
    # isolate the mermaid <pre><code> block and assert it has no styled spans
    m = re.search(
        r'<code class="language-mermaid">(.*?)</code>', html, re.DOTALL
    )
    assert m is not None
    assert '<span style=' not in m.group(1), (
        "mermaid source must not be tokenized/highlighted"
    )


def test_unknown_language_degrades_gracefully():
    """An unknown language falls back to a plain escaped block (no crash, no
    token spans) — HTML export never hard-fails on highlighting."""
    content = (
        "Unknown lang:\n\n"
        + _BT + "totallyfakelang9000\n"
        "some < raw > code & text\n"
        + _BT + "\n"
    )
    html = _export_html(content)
    assert 'class="language-totallyfakelang9000"' in html
    m = re.search(
        r'<code class="language-totallyfakelang9000">(.*?)</code>',
        html,
        re.DOTALL,
    )
    assert m is not None
    # content is plainly escaped, no inline-styled token spans
    assert '<span style=' not in m.group(1)
    assert '&lt; raw &gt;' in m.group(1) and '&amp;' in m.group(1)


def test_script_in_highlighted_block_is_escaped():
    """A <script> tag inside a highlighted code block is HTML-escaped, not
    emitted raw (CWE-79 escaping runs on the Pygments path too)."""
    content = (
        "Sneaky python:\n\n"
        + _BT + "python\n"
        's = "<script>alert(1)</script>"\n'
        + _BT + "\n"
    )
    html = _export_html(content)
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;' in html


def _pygments_available() -> bool:
    from app.utils import conversation_exporter as ce

    return getattr(ce, "_PYGMENTS_AVAILABLE", False)
