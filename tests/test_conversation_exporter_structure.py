"""HTML-05 regression: the exported .html must be WELL-FORMED, not merely
renderable via browser unclosed-tag error recovery.

The defect (fixed in _markdown_to_html_basic): the paragraph pass wrapped every
double-newline-separated chunk in ``<p>...</p>``, and its guard
``not p.strip().startswith('<')`` did not recognise the fenced-code-block
sentinel (``\\x00CODEBLOCK{n}\\x00``). The sentinel is later restored to a
block-level ``<pre>...</pre>``, so a code block came out as ``<p><pre>...</pre></p>``
— a block ``<pre>`` illegally nested in a ``<p>``. The browser closes the ``<p>``
early and the literal ``</p>`` becomes a stray end tag, which html5lib reports as
``unexpected-end-tag`` (i.e. the document only renders via error recovery, which
is fragile across paste services, email clients, and PDF-from-HTML).

These are browser-free static assertions on the exported HTML source. The
end-to-end proof (html5lib parse-error list from the real rendered document) is
the shared fidelity apparatus check ``structural_validity``
(tests/export_fidelity).
"""
from __future__ import annotations

import re

import html5lib


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


# Content shaped like the shared fixture: prose paragraphs framing fenced code
# blocks (python + diff), which is exactly what triggered <p><pre> nesting.
_MIXED_CONTENT = (
    "Intro prose paragraph one.\n\n"
    "Here is a Python function:\n\n"
    "```python\n"
    "def greet(name: str) -> str:\n"
    '    return f"hi {name}"\n'
    "```\n\n"
    "And a unified diff:\n\n"
    "```diff\n"
    "diff --git a/f.py b/f.py\n"
    "-old_line\n"
    "+new_line\n"
    "```\n\n"
    "Closing prose paragraph."
)


def _structural_parse_errors(html: str):
    """Return html5lib's STRUCTURAL parse errors (tag nesting / unexpected
    close) — the same subset the shared structural_validity check gates on."""
    parser = html5lib.HTMLParser(strict=False)
    parser.parse(html)
    errors = [f"{code}@{pos}" for pos, code, _ in parser.errors]
    keys = (
        "unexpected-end-tag", "expected-closing-tag", "unexpected-cell-end-tag",
        "eof-in", "unexpected-start-tag", "end-tag-too-early",
        "unexpected-token-in-table", "table-in", "misplaced",
    )
    return [e for e in errors if any(k in e for k in keys)]


class TestHtmlStructuralValidity:
    def test_code_block_not_nested_in_paragraph(self):
        """A fenced code block must render as a top-level <pre>, never wrapped
        in a <p> (the source of the malformed <p><pre>...</pre></p>)."""
        html = _export_html(_MIXED_CONTENT)
        assert "<p><pre>" not in html, (
            "exported HTML nests a block <pre> inside a <p> (HTML-05) — "
            "invalid markup that only renders via browser error recovery"
        )
        assert "</pre></p>" not in html

    def test_no_structural_parse_errors(self):
        """The full exported document parses with ZERO structural html5lib
        errors (no unclosed-tag / mis-nesting error recovery)."""
        html = _export_html(_MIXED_CONTENT)
        structural = _structural_parse_errors(html)
        assert structural == [], (
            f"exported HTML has {len(structural)} structural parse error(s): "
            f"{structural[:6]}"
        )

    def test_prose_paragraphs_still_wrapped(self):
        """The fix must NOT stop wrapping ordinary prose in <p> (regression
        guard: we narrowed the wrap condition, not disabled it)."""
        html = _export_html(_MIXED_CONTENT)
        assert re.search(r"<p>[^<]*Intro prose paragraph one", html), (
            "prose paragraph is no longer wrapped in <p> — the fix over-reached"
        )
        # And the code block content is still present (not dropped).
        assert 'class="language-python"' in html
        assert 'class="language-diff"' in html
