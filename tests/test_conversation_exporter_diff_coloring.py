"""HTML-02 regression: a ``diff`` fenced code block in the HTML export must
render with PER-LINE add/remove coloring, not as one uncolored blob.

The defect (fixed in _markdown_to_html_basic via _render_diff_code_block): a
``diff`` fence was treated like any other code fence — a single escaped
``<pre><code class="language-diff">`` with no per-line elements and no
green/red backgrounds. The fidelity harness measured 0 insert / 0 delete
elements (check_diff_coloring FAIL) and the raster diff_delete_red pixel count
fell below threshold (check_expected_color_presence FAIL).

The fix splits the diff into lines and wraps each added line in
``.diff-line-insert`` (GitHub-light green #e6ffec) and each removed line in
``.diff-line-delete`` (red #ffebe9), while file-header lines (``---``/``+++``)
and hunk headers (``@@``) are NOT treated as delete/insert.

These are browser-free static assertions on the exported HTML source. The
end-to-end proof (computed backgrounds of the per-line elements + raster pixel
counts in the rendered document) is the shared fidelity apparatus check
``diff_coloring`` / ``expected_color_presence`` (tests/export_fidelity).

Guardrails asserted here:
  * insert/delete markup EXISTS and is distinguishable (the fix works);
  * ``---``/``+++`` headers are NOT mis-classified as delete/insert;
  * the diff markup is WELL-FORMED (no block element nested in <p>, no
    html5lib structural parse errors) — the HTML-05 guarantee is preserved;
  * only BACKGROUND color varies per line — no per-line text ``color``, so the
    fix does not masquerade as syntax highlighting (HTML-01 stays a real,
    separate defect);
  * a ``<script>`` embedded in a diff line is HTML-escaped (security intact).
"""
from __future__ import annotations

import re

import html5lib


def _export_html(content: str) -> str:
    from app.utils.conversation_exporter import export_conversation_for_paste

    messages = [
        {"role": "human", "content": "Show me a diff."},
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

# A unified diff with context, a removed line, an added line, file headers and a
# hunk header — the same shape as the shared fixture's diff fence.
_DIFF_CONTENT = (
    "A unified diff:\n\n"
    + _BT + "diff\n"
    "diff --git a/f.py b/f.py\n"
    "--- a/f.py\n"
    "+++ b/f.py\n"
    "@@ -1,3 +1,3 @@\n"
    " context = 0\n"
    "-removed = 1\n"
    "+added = 2\n"
    + _BT + "\n"
)


def _structural_parse_errors(html: str):
    parser = html5lib.HTMLParser(strict=False)
    parser.parse(html)
    structural = []
    for (_pos, code, _data) in getattr(parser, "errors", []):
        if any(t in code for t in ("unexpected-end-tag", "end-tag", "unexpected-start-tag")):
            structural.append(code)
    return structural


def test_diff_has_per_line_insert_and_delete_elements():
    """The added line is an insert element and the removed line is a delete
    element — distinguishable per-line coloring, not one uncolored blob."""
    html = _export_html(_DIFF_CONTENT)
    # Count the SPAN markup (the class name also appears once in the embedded
    # CSS rule, so match the span element specifically).
    insert_spans = re.findall(r'<span class="diff-line diff-line-insert">', html)
    delete_spans = re.findall(r'<span class="diff-line diff-line-delete">', html)
    assert insert_spans, "no per-line diff INSERT element emitted"
    assert delete_spans, "no per-line diff DELETE element emitted"
    # exactly one of each real change line (the +added / -removed lines)
    assert len(insert_spans) == 1
    assert len(delete_spans) == 1


def test_diff_file_headers_not_classified_as_change():
    """``---``/``+++`` file-header lines must NOT be mis-tagged as delete/insert
    (they begin with -/+ but are not content changes)."""
    html = _export_html(_DIFF_CONTENT)
    # The delete span wraps the real removed line, never the '--- a/f.py' header.
    m = re.search(r'<span class="diff-line diff-line-delete">([^<]*)</span>', html)
    assert m is not None
    assert 'removed = 1' in m.group(1)
    assert '--- a/f.py' not in m.group(1)
    m_ins = re.search(r'<span class="diff-line diff-line-insert">([^<]*)</span>', html)
    assert m_ins is not None
    assert 'added = 2' in m_ins.group(1)
    assert '+++ b/f.py' not in m_ins.group(1)


def test_diff_markup_is_well_formed():
    """Per-line spans live inside <pre><code>; no block nested in <p>, and the
    document has zero html5lib structural parse errors (HTML-05 preserved)."""
    html = _export_html(_DIFF_CONTENT)
    assert '<p><pre' not in html
    assert '<pre class="diff-block">' in html
    errs = _structural_parse_errors(html)
    assert errs == [], f"unexpected structural parse errors: {errs}"


def test_diff_lines_have_no_text_color_override():
    """Diff coloring is BACKGROUND-only: no ``.diff-line*`` rule sets a text
    ``color``. A second text color would make the syntax-highlighting probe
    (which counts distinct token-span text colors) spuriously pass, falsely
    reporting HTML-01 (missing Prism) as fixed."""
    html = _export_html(_DIFF_CONTENT)
    # Isolate the .diff-line* CSS rules from the embedded <style> and assert
    # none declare a `color:` property (only `background:` is allowed).
    for m in re.finditer(r'\.diff-line[\w-]*\s*\{([^}]*)\}', html):
        body = m.group(1)
        assert 'color:' not in body.replace('background', ''), (
            f"a .diff-line rule sets a text color, which would masquerade as "
            f"syntax highlighting: {{{body}}}"
        )


def test_script_in_diff_line_is_escaped():
    """A <script> tag inside a diff line is HTML-escaped, not emitted raw
    (CWE-79 escaping still runs on the per-line diff path)."""
    payload = (
        "A diff with an injected tag:\n\n"
        + _BT + "diff\n"
        "+<script>alert(1)</script>\n"
        "-safe = 0\n"
        + _BT + "\n"
    )
    html = _export_html(payload)
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html
