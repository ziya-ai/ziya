"""Guard for QUAL-01: the PDF driver synthesises a navigable outline.

These tests exercise the driver's outline-synthesis helpers directly
(``_map_sentinels_to_pages`` + ``_synthesize_outline``) WITHOUT a browser, so
they run in the fast (browser-free) tier alongside test_pipeline_wiring.  They
prove the two-pass mechanism:

  * a probe PDF carrying per-message sentinel tokens is read back into a
    message->page mapping via the text layer, and
  * that mapping is stamped onto clean PDF bytes as a resolvable bookmark tree.

The end-to-end fail->pass (Chromium page.pdf() emits ZERO outline items until
this lands) is proven in the integration audit; check_pdf_outline's own
pass/fail is covered by test_checks_can_fail.test_pdf_outline_can_pass_and_fail.
"""
from __future__ import annotations

import io

import pytest

from app.services import pdf_exporter as PE

pypdf = pytest.importorskip("pypdf")
from pypdf import PdfReader, PdfWriter  # noqa: E402


def _blank_pdf(n_pages: int, *, sentinels=None) -> bytes:
    """Build an ``n_pages`` PDF.  ``sentinels`` maps page-index -> token text
    drawn onto that page (so the text layer carries the token)."""
    sentinels = sentinels or {}
    writer = PdfWriter()
    for i in range(n_pages):
        writer.add_blank_page(width=595, height=842)
    # pypdf cannot easily draw text; instead we fake the probe text layer in the
    # mapping test by monkeypatching extract_text.  Here we only need real page
    # objects, so return the bytes and let the mapping test stub extraction.
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _flatten_outline(reader):
    items = []

    def rec(node, depth=0):
        for it in node:
            if isinstance(it, list):
                rec(it, depth + 1)
            else:
                try:
                    pg = reader.get_destination_page_number(it)
                except Exception:
                    pg = None
                items.append({"title": str(it.title), "page": pg, "depth": depth})

    rec(reader.outline)
    return items


def test_map_sentinels_reads_pages_from_text_layer(monkeypatch):
    """Each sentinel token resolves to the page whose text layer contains it."""
    anchors = [
        {"index": 0, "label": "You", "token": "ZYAOUTLINEANCHOR0X"},
        {"index": 1, "label": "Ziya", "token": "ZYAOUTLINEANCHOR1X"},
        {"index": 2, "label": "You", "token": "ZYAOUTLINEANCHOR2X"},
    ]
    # Stub the page text layer: token0 on page0, token1+token2 on page1.
    page_texts = [
        "intro ZYA OUTLINE ANCHOR0X body",   # spaces to prove space-collapsing
        "ZYAOUTLINEANCHOR1X and ZYAOUTLINEANCHOR2X",
    ]

    class _FakePage:
        def __init__(self, t):
            self._t = t

        def extract_text(self):
            return self._t

    class _FakeReader:
        def __init__(self, *_a, **_k):
            self.pages = [_FakePage(t) for t in page_texts]

    monkeypatch.setattr(PE, "io", PE.io)  # keep io module intact
    import pypdf as _pypdf

    monkeypatch.setattr(_pypdf, "PdfReader", _FakeReader)

    mapping = PE._map_sentinels_to_pages(b"ignored", anchors)
    by_index = {m["index"]: m for m in mapping}
    assert by_index[0]["page"] == 0
    assert by_index[1]["page"] == 1
    assert by_index[2]["page"] == 1
    # ordered by (page, index)
    assert [m["index"] for m in mapping] == [0, 1, 2]


def test_synthesize_outline_stamps_resolvable_bookmarks():
    """A mapping is stamped as a bookmark tree whose destinations resolve."""
    clean = _blank_pdf(3)
    mapping = [
        {"index": 0, "label": "You", "page": 0},
        {"index": 1, "label": "Ziya", "page": 1},
        {"index": 2, "label": "You", "page": 2},
    ]
    out = PE._synthesize_outline(clean, mapping)
    reader = PdfReader(io.BytesIO(out))
    items = _flatten_outline(reader)
    assert len(items) == 3
    resolved = [it for it in items if it["page"] is not None]
    assert len(resolved) == 3
    assert sorted({it["page"] for it in resolved}) == [0, 1, 2]
    # Titles carry role label + 1-based message number.
    assert items[0]["title"] == "You (message 1)"
    assert items[1]["title"] == "Ziya (message 2)"


def test_synthesize_outline_empty_mapping_is_noop():
    """No mapping -> bytes returned unchanged (best-effort, never breaks)."""
    clean = _blank_pdf(2)
    assert PE._synthesize_outline(clean, []) == clean


def test_synthesize_outline_skips_out_of_range_pages():
    """A page index past the document is skipped, not fatal."""
    clean = _blank_pdf(2)
    mapping = [
        {"index": 0, "label": "You", "page": 0},
        {"index": 1, "label": "Ziya", "page": 99},  # out of range
    ]
    out = PE._synthesize_outline(clean, mapping)
    reader = PdfReader(io.BytesIO(out))
    items = _flatten_outline(reader)
    assert len(items) == 1
    assert items[0]["page"] == 0


def test_outline_item_cap_is_bounded():
    """A runaway conversation cannot produce an unbounded bookmark tree."""
    clean = _blank_pdf(1)
    mapping = [
        {"index": i, "label": "You", "page": 0}
        for i in range(PE._OUTLINE_MAX_ITEMS + 50)
    ]
    out = PE._synthesize_outline(clean, mapping)
    reader = PdfReader(io.BytesIO(out))
    items = _flatten_outline(reader)
    assert len(items) == PE._OUTLINE_MAX_ITEMS


def test_inject_js_targets_print_messages_and_is_out_of_flow():
    """The injected sentinel JS is out-of-flow (won't perturb pagination)."""
    js = PE._OUTLINE_SENTINEL_INJECT_JS
    assert ".print-message" in js
    assert "position" in js and "absolute" in js
    assert "data-zya-outline-anchor" in js
    # remover keys off the same attribute
    assert "data-zya-outline-anchor" in PE._OUTLINE_SENTINEL_REMOVE_JS
