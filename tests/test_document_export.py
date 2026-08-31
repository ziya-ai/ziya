"""
Tests for the authored-document IR and its PDF export wiring (phase 1).

Covers, in dependency order:
  1. app/utils/document_ir.py — front-matter parsing/normalization, pagebreak
     splitting, and the store's path-traversal guard.
  2. pdf_exporter.build_document_print_payload — the /print payload contract
     the frontend document mode consumes (kind/sections/layout/author).
  3. pdf_exporter._synthesize_outline — heading-mode NESTED bookmark trees
     (level-carrying mappings) alongside the unchanged flat message mode.
  4. pdf_exporter.export_document_pdf — the SEAM: IR file → payload →
     capture_pdf(outline_mode="headings", metadata, margin) without a browser
     (session mocked).

NOTE: tests 2-4 require the pdf_exporter document-mode diff to be applied;
they fail with ImportError/AttributeError against unpatched code, which is
the intended "test certifies the fix" behavior.
"""
import asyncio
import io
import os

import pytest

from app.utils.document_ir import (
    parse_document,
    normalize_meta,
    split_sections,
    resolve_document_path,
    load_document,
    documents_dir,
)


# ---------------------------------------------------------------------------
# 1. Document IR
# ---------------------------------------------------------------------------

FULL_DOC = """---
ziya-doc: 1
title: "Queue Depth Analysis"
author: "dcohn"
layout: report
page:
  size: A4
  margin: 18mm
---

# Exec Summary

Body text with $\\rho = 0.85$ math.

<!-- ziya:pagebreak -->

# Model

```mermaid
graph TD; a-->b
```
"""


def test_parse_document_full_front_matter():
    meta, body = parse_document(FULL_DOC)
    assert meta['title'] == 'Queue Depth Analysis'
    assert meta['author'] == 'dcohn'
    assert meta['layout'] == 'report'
    assert meta['page']['size'] == 'A4'
    assert meta['page']['margin'] == {
        'top': '18mm', 'bottom': '18mm', 'left': '18mm', 'right': '18mm',
    }
    # Body starts after the front-matter block and keeps the markdown intact.
    assert body.lstrip().startswith('# Exec Summary')
    assert '```mermaid' in body


def test_parse_document_without_front_matter():
    meta, body = parse_document('# Just markdown\n\nhello')
    assert meta['title'] is None
    assert meta['layout'] == 'plain'
    assert body == '# Just markdown\n\nhello'


def test_parse_document_malformed_front_matter_never_raises():
    text = '---\n: : not yaml [\n---\n# Body'
    meta, body = parse_document(text)
    assert body.strip() == '# Body'
    assert meta['layout'] == 'plain'  # defaults survive


def test_normalize_meta_rejects_invalid_values():
    meta = normalize_meta({
        'title': 42,                      # non-string → dropped
        'layout': 'fancy',                # unknown → plain
        'page': {'margin': 'huge'},       # invalid length → dropped
        'unknown_key': 'x',               # never crosses the wire
    })
    assert meta['title'] is None
    assert meta['layout'] == 'plain'
    assert meta['page'] == {}
    assert 'unknown_key' not in meta


def test_normalize_meta_per_side_margins():
    meta = normalize_meta({'page': {'margin': {
        'top': '12mm', 'bottom': '20mm', 'left': 'bogus', 'right': '1in',
    }}})
    assert meta['page']['margin'] == {
        'top': '12mm', 'bottom': '20mm', 'right': '1in',
    }


def test_split_sections_on_pagebreak():
    body = 'one\n\n<!-- ziya:pagebreak -->\n\ntwo\n<!--ziya:pagebreak-->\nthree'
    assert split_sections(body) == ['one', 'two', 'three']


def test_split_sections_no_directive_single_section():
    assert split_sections('just one body') == ['just one body']


def test_split_sections_drops_empty_and_never_returns_empty_list():
    assert split_sections('<!-- ziya:pagebreak -->') == ['']
    assert split_sections('') == ['']


def test_pagebreak_must_be_alone_on_line():
    body = 'text <!-- ziya:pagebreak --> more'  # inline: NOT a directive
    assert split_sections(body) == [body]


def test_resolve_document_path_rejects_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv('ZIYA_USER_CODEBASE_DIR', str(tmp_path))
    with pytest.raises(ValueError):
        resolve_document_path('../../etc/passwd')
    with pytest.raises(ValueError):
        resolve_document_path('/etc/passwd')
    with pytest.raises(ValueError):
        resolve_document_path('')


def test_load_document_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv('ZIYA_USER_CODEBASE_DIR', str(tmp_path))
    store = documents_dir()
    assert store == tmp_path / '.ziya' / 'documents'
    (store / 'r.md').write_text(FULL_DOC, encoding='utf-8')
    doc = load_document('r.md')
    assert doc['meta']['title'] == 'Queue Depth Analysis'
    assert '# Model' in doc['body']


def test_load_document_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setenv('ZIYA_USER_CODEBASE_DIR', str(tmp_path))
    documents_dir()  # ensure store exists but is empty
    with pytest.raises(FileNotFoundError):
        load_document('absent.md')


# ---------------------------------------------------------------------------
# 2. Payload contract
# ---------------------------------------------------------------------------

def test_build_document_print_payload_shape():
    from app.services.pdf_exporter import build_document_print_payload
    meta, body = parse_document(FULL_DOC)
    sections = split_sections(body)
    payload = build_document_print_payload(meta, sections, title='Override')
    assert payload['kind'] == 'document'
    assert payload['title'] == 'Override'
    assert payload['author'] == 'dcohn'
    assert payload['layout'] == 'report'
    assert len(payload['sections']) == 2
    assert 'messages' not in payload  # document payloads carry no transcript
    assert payload['options']['includeFooter'] is False
    assert 'footerHtml' not in payload


def test_build_document_print_payload_footer():
    from app.services.pdf_exporter import build_document_print_payload
    payload = build_document_print_payload({}, ['body'], footer_html='<div/>')
    assert payload['options']['includeFooter'] is True
    assert payload['footerHtml'] == '<div/>'


# ---------------------------------------------------------------------------
# 3. Nested outline synthesis (heading mode) vs flat (message mode)
# ---------------------------------------------------------------------------

def _blank_pdf(n_pages: int) -> bytes:
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(n_pages):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_synthesize_outline_nested_by_level():
    from pypdf import PdfReader
    from app.services.pdf_exporter import _synthesize_outline
    mapping = [
        {'index': 0, 'label': 'Intro', 'page': 0, 'level': 1},
        {'index': 1, 'label': 'Background', 'page': 0, 'level': 2},
        {'index': 2, 'label': 'Model', 'page': 1, 'level': 1},
        {'index': 3, 'label': 'Results', 'page': 2, 'level': 2},
    ]
    out = _synthesize_outline(_blank_pdf(3), mapping)
    reader = PdfReader(io.BytesIO(out))
    ol = reader.outline
    # Top level: Intro, [children], Model, [children]
    titles = [o.title for o in ol if not isinstance(o, list)]
    assert titles == ['Intro', 'Model']
    nested = [o for o in ol if isinstance(o, list)]
    assert len(nested) == 2
    assert nested[0][0].title == 'Background'
    assert nested[1][0].title == 'Results'


def test_synthesize_outline_message_mode_unchanged():
    from pypdf import PdfReader
    from app.services.pdf_exporter import _synthesize_outline
    mapping = [
        {'index': 0, 'label': 'You', 'page': 0},
        {'index': 1, 'label': 'Ziya', 'page': 1},
    ]
    out = _synthesize_outline(_blank_pdf(2), mapping)
    reader = PdfReader(io.BytesIO(out))
    titles = [o.title for o in reader.outline if not isinstance(o, list)]
    assert titles == ['You (message 1)', 'Ziya (message 2)']


# ---------------------------------------------------------------------------
# 4. export_document_pdf seam (mocked session — no browser required)
# ---------------------------------------------------------------------------

class _FakeSession:
    def __init__(self):
        self.calls = []

    async def capture_pdf(self, payload, **kwargs):
        self.calls.append({'payload': payload, **kwargs})
        return b'%PDF-fake'


def test_export_document_pdf_seam(tmp_path, monkeypatch):
    import app.services.pdf_exporter as pe

    monkeypatch.setenv('ZIYA_USER_CODEBASE_DIR', str(tmp_path))
    store = documents_dir()
    (store / 'report.md').write_text(FULL_DOC, encoding='utf-8')

    fake = _FakeSession()

    async def fake_get_session(port=6969):
        return fake

    monkeypatch.setattr(pe, 'get_render_session', fake_get_session)

    pdf, meta = asyncio.run(pe.export_document_pdf(
        name='report.md', model='claude', provider='bedrock',
    ))
    assert pdf == b'%PDF-fake'
    assert meta['section_count'] == 2
    assert meta['title'] == 'Queue Depth Analysis'

    call = fake.calls[0]
    # Heading-tree outline, not the message outline.
    assert call['outline_mode'] == 'headings'
    # Front-matter margin flows through to page.pdf().
    assert call['margin']['top'] == '18mm'
    # Front-matter author OVERRIDES the model/provider default.
    assert call['metadata']['/Author'] == 'dcohn'
    assert call['metadata']['/Title'] == 'Queue Depth Analysis'
    # Payload is document-shaped.
    assert call['payload']['kind'] == 'document'
    assert call['payload']['layout'] == 'report'
    assert len(call['payload']['sections']) == 2
    # No footer unless requested (work product, not transcript).
    assert 'footerHtml' not in call['payload']


def test_export_document_pdf_requires_source():
    from app.services.pdf_exporter import export_document_pdf
    with pytest.raises(ValueError):
        asyncio.run(export_document_pdf())


def test_export_document_pdf_inline_markdown(monkeypatch):
    import app.services.pdf_exporter as pe
    fake = _FakeSession()

    async def fake_get_session(port=6969):
        return fake

    monkeypatch.setattr(pe, 'get_render_session', fake_get_session)
    pdf, meta = asyncio.run(pe.export_document_pdf(
        markdown='# Solo\n\nbody', title='Inline Doc',
    ))
    assert pdf == b'%PDF-fake'
    assert meta['section_count'] == 1
    assert fake.calls[0]['payload']['title'] == 'Inline Doc'
    assert fake.calls[0]['payload']['layout'] == 'plain'
