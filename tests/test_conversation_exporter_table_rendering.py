"""HTML-06 regression: markdown tables must render as real ``<table>`` grids,
not as literal pipe-delimited text.

The defect (fixed in ``_markdown_to_html_basic`` via ``_render_markdown_table``):
the regex fallback had NO table support, so a GFM table
(``| A | B |`` / ``| --- | --- |`` / ``| 1 | 2 |``) was emitted as literal
pipe text with ``<br>`` line breaks. The cell TEXT survived (so the
content-completeness and text-extractability checks still passed) but the grid
LAYOUT was lost — the "looks wrong while the checks pass" case. This is exactly
the class of defect the composite visual judgement is meant to catch.

These are browser-free static assertions on the exported HTML source. The
end-to-end proof (a rendered ``<table>`` with cells and zero surviving
``| --- |`` delimiter rows in the innerText) is the shared fidelity apparatus
check ``table_rendering`` (tests/export_fidelity).

Mutation proof: reverting the ``_extract_tables`` wiring in
``_markdown_to_html_basic`` (so tables fall through to literal pipe text) flips
``test_markdown_table_becomes_html_table`` and
``test_no_literal_pipe_delimiter_survives`` to FAIL.
"""
from __future__ import annotations

import re

import html5lib


def _export_html(content: str) -> str:
    from app.utils.conversation_exporter import export_conversation_for_paste

    messages = [
        {"role": "human", "content": "Show me a table."},
        {"role": "assistant", "content": content},
    ]
    return export_conversation_for_paste(
        messages,
        format_type="html",
        target="public",
        version="9.9.9",
        model="test-model",
        provider="test-provider",
    )["content"]


_TABLE_CONTENT = (
    "Intro prose.\n\n"
    "| Name | Role | Team |\n"
    "| --- | :---: | ---: |\n"
    "| Ada | Eng | Core |\n"
    "| Bo | PM | Core |\n\n"
    "Closing prose."
)


def test_markdown_table_becomes_html_table():
    html = _export_html(_TABLE_CONTENT)
    assert "<table>" in html, "markdown table was not converted to a <table>"
    # count real cells (avoid matching <thead>/<tbody>): <th> or <th ...>
    n_th = len(re.findall(r"<th(?:\s[^>]*)?>", html))
    n_td = len(re.findall(r"<td(?:\s[^>]*)?>", html))
    assert n_th == 3, f"expected 3 header cells, got {n_th}"
    # 2 body rows x 3 columns = 6 <td>
    assert n_td == 6, f"expected 6 body cells, got {n_td}"
    assert re.search(r"<td[^>]*>Ada</td>", html), "cell text lost"
    assert re.search(r"<th[^>]*>Name</th>", html), "header text lost"


def test_no_literal_pipe_delimiter_survives():
    html = _export_html(_TABLE_CONTENT)
    # The delimiter row must NOT survive as literal text.
    assert "| --- |" not in html
    assert "| :---: |" not in html
    # No <br>-joined pipe run either.
    assert "|<br>|" not in html


def test_column_alignment_from_delimiter():
    html = _export_html(_TABLE_CONTENT)
    # :---: -> center, ---: -> right (applied to that column's th and td).
    assert "text-align:center" in html
    assert "text-align:right" in html


def test_table_is_well_formed_not_nested_in_p():
    """The block-level <table> must NOT be wrapped in a <p> (HTML-05 class of
    defect): a <table> inside a <p> triggers the same unclosed-tag recovery a
    <pre> did."""
    html = _export_html(_TABLE_CONTENT)
    assert "<p><table>" not in html and "<table></p>" not in html
    parser = html5lib.HTMLParser(strict=False)
    parser.parse(html)
    structural = [
        e for e in parser.errors
        if any(k in str(e[1]) for k in (
            "unexpected-end-tag", "end-tag-too-early",
            "unexpected-token-in-table", "table-in", "misplaced",
        ))
    ]
    assert structural == [], f"structural parse errors: {structural}"


def test_html_in_table_cell_is_escaped():
    """CWE-79: raw HTML in a table cell must be neutralized (escaped as text),
    never emitted as a live tag."""
    content = (
        "| Payload | Note |\n"
        "| --- | --- |\n"
        "| <script>alert(1)</script> | <img src=x onerror=alert(2)> |\n"
    )
    html = _export_html(content)
    assert "<table>" in html
    # escaped, present as text
    assert "&lt;script&gt;" in html
    # no live tag inside a cell
    assert not re.search(r"<td[^>]*><script>", html)
    assert not re.search(r"<td[^>]*><img[^>]*onerror", html, re.I)


def test_non_table_pipe_prose_is_not_a_table():
    """A stray pipe in prose (no delimiter row) must NOT be turned into a
    table (over-reach guard)."""
    content = "This sentence has a | pipe but is not a table.\n\nAnother paragraph."
    html = _export_html(content)
    assert "<table>" not in html
    assert "not a table" in html
