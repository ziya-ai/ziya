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


def test_split_sections_wires_linear_chain_preprocess():
    """split_sections must run the generic body pre-processing pass (D-001),
    so the linear-chain re-layout ships through BOTH exporter paths without an
    edit to the non-writable pdf_exporter.  A vertical mermaid chain declared
    ``graph TD`` must come out re-laid left-to-right (``flowchart LR`` for a
    short chain).  Fails against the old split_sections, which never touched
    fence bodies."""
    body = (
        "Intro paragraph.\n\n"
        "```mermaid\n"
        "graph TD\n"
        "  A[Ingress] --> B[Parse]\n"
        "  B --> C[Route]\n"
        "  C --> D[Write]\n"
        "```\n"
    )
    (section,) = split_sections(body)
    assert "graph TD" not in section
    assert "flowchart LR" in section
    # Node labels preserved through the rewrite.
    assert "Ingress" in section and "Write" in section


def test_split_sections_leaves_non_linear_diagram_untouched():
    """A branching graph is NOT a linear chain and must pass through the pass
    byte-for-byte, so split_sections does not mangle real diagrams."""
    body = (
        "```mermaid\n"
        "graph TD\n"
        "  A --> B\n"
        "  A --> C\n"
        "```\n"
    )
    (section,) = split_sections(body)
    assert "graph TD" in section          # untouched
    assert "flowchart LR" not in section


def test_split_sections_plain_prose_is_byte_identical():
    """Prose with no diagram fence must be returned unchanged (the pass is a
    no-op), so wiring the pre-processor into split_sections cannot regress
    ordinary documents."""
    body = "# Heading\n\nSome text with `inline code` and a [link](http://x).\n"
    assert split_sections(body) == [body.strip('\n')]


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


def test_documents_dir_follows_request_project_root(tmp_path, monkeypatch):
    # Observed: server started in $HOME, project open elsewhere. file_write
    # put the IR under <project>/.ziya/documents/, the export route looked
    # under $HOME/.ziya/documents/ and returned "Document not found". The
    # store must resolve against the SAME root file_write uses.
    from app.context import set_project_root, _request_project_root
    home = tmp_path / 'home'
    project = tmp_path / 'project'
    (project / '.ziya' / 'documents').mkdir(parents=True)
    (project / '.ziya' / 'documents' / 'report.md').write_text('# hi\n')
    monkeypatch.setenv('ZIYA_USER_CODEBASE_DIR', str(home))       # startup dir
    token = _request_project_root.set(None)
    try:
        set_project_root(str(project))                            # X-Project-Root
        assert documents_dir(create=False) == project / '.ziya' / 'documents'
        assert load_document('report.md')['body'].strip() == '# hi'
    finally:
        _request_project_root.reset(token)


def test_documents_dir_falls_back_to_startup_root_without_request(tmp_path, monkeypatch):
    # Pair for the above: no header (CLI, or a bare curl) still resolves to
    # the startup root, so nothing that worked before stops working.
    from app.context import _request_project_root
    monkeypatch.setenv('ZIYA_USER_CODEBASE_DIR', str(tmp_path))
    token = _request_project_root.set(None)
    try:
        assert documents_dir(create=False) == tmp_path / '.ziya' / 'documents'
    finally:
        _request_project_root.reset(token)


def test_skill_recipe_sends_project_root_and_no_install_recipe():
    # Seam: the store fix is moot if the model's curl (from the shell, which
    # runs in the project root) omits the header. And the 501 guidance must
    # relay the server's message, not the raw playwright recipe -- on an AL2
    # host that recipe can never succeed.
    import re
    from app.data.built_in_skills import BUILT_IN_SKILLS
    skill = next(s for s in BUILT_IN_SKILLS if s['id'] == 'document_authoring')
    text = skill['prompt']
    assert 'X-Project-Root: $PWD' in text
    assert text.index('X-Project-Root') < text.index('"name": "report.md"')
    assert 'playwright install chromium' not in text
    assert re.search(r'501.*Relay', text, re.S)


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
    # The footer is drawn in the page margin (footer_template), never
    # appended to the body payload.
    assert 'footerHtml' not in call['payload']
    # Footer is ON by default, matching the other export routes.
    assert call['footer_template'], 'default export must carry the per-page footer'


def test_export_document_pdf_drives_document_aesthetics(monkeypatch):
    """The SEAM for the aesthetic layer (app/utils/document_print_decor):
    export_document_pdf must render under PRINT media (table headers repeat
    only there), at a viewport matched to the printable box, inject the
    document stylesheet, run the decoration pass with the front-matter
    knobs, format the date once for BOTH title block and footer, and use the
    document footer (title · author · date) instead of the transcript one."""
    import app.services.pdf_exporter as pe
    from app.utils.document_print_decor import (
        DOCUMENT_PRINT_CSS, DEFAULT_DOCUMENT_MARGIN, page_content_box_px,
    )
    fake = _FakeSession()

    async def fake_get_session(port=6969):
        return fake

    monkeypatch.setattr(pe, 'get_render_session', fake_get_session)
    doc = (
        "---\ntitle: Aesthetics\nsubtitle: Sub line\nauthor: dcohn\n"
        "date: 2026-09-19\nlayout: titlepage\nnumbering:\n  sections: true\n"
        "---\n# A\n\nbody\n"
    )
    asyncio.run(pe.export_document_pdf(
        markdown=doc, version='0.9', model='fable', provider='bedrock',
    ))
    call = fake.calls[0]

    assert call['media'] == 'print'
    # No front-matter margin -> the document default, and the viewport is
    # the printable box for exactly that margin.
    assert call['margin'] == DEFAULT_DOCUMENT_MARGIN
    assert tuple(call['viewport']) == page_content_box_px(DEFAULT_DOCUMENT_MARGIN)
    assert call['pre_capture_css'] is DOCUMENT_PRINT_CSS

    (js, opts), = call['pre_capture_js']
    assert callable(getattr(js, 'strip', None)) and '(opts)' in js
    assert opts['numbering'] == {'sections': True, 'figures': True, 'tables': True}
    assert opts['layout'] == 'titlepage'
    assert opts['subtitle'] == 'Sub line' and opts['author'] == 'dcohn'
    assert opts['date'] == 'September 19, 2026'
    assert opts['pageHeightPx'] == page_content_box_px(DEFAULT_DOCUMENT_MARGIN)[1]

    # Payload carries the same DISPLAY date and the new title-block fields.
    assert call['payload']['date'] == 'September 19, 2026'
    assert call['payload']['subtitle'] == 'Sub line'
    assert call['payload']['numbering'] == {'sections': True}

    footer = call['footer_template']
    assert 'Aesthetics · dcohn · September 19, 2026' in footer
    assert 'orchestration harness' not in footer   # transcript tagline gone
    assert 'fable (Bedrock)' in footer and 'v0.9' in footer


def test_export_document_pdf_explicit_margin_sizes_viewport(monkeypatch):
    import app.services.pdf_exporter as pe
    from app.utils.document_print_decor import page_content_box_px
    fake = _FakeSession()

    async def fake_get_session(port=6969):
        return fake

    monkeypatch.setattr(pe, 'get_render_session', fake_get_session)
    asyncio.run(pe.export_document_pdf(
        markdown="---\npage:\n  margin: 10mm\n---\n# A\n", include_footer=False,
    ))
    call = fake.calls[0]
    m = {s: '10mm' for s in ('top', 'bottom', 'left', 'right')}
    assert call['margin'] == m
    assert tuple(call['viewport']) == page_content_box_px(m)
    # The page fits figures against the SAME box it is paginated with.
    w, h = page_content_box_px(m)
    assert call['payload']['page'] == {'contentWidthPx': w, 'contentHeightPx': h}


def test_export_document_pdf_payload_carries_printable_box(monkeypatch):
    """The /print page used to fit oversized figures against a constant
    baked for the TRANSCRIPT export (A4 minus 10mm sides, ~718px) while a
    document paginates at its front-matter margins (19mm sides, ~650px), so
    a figure between the two widths escaped the fit pass and was squeezed by
    CSS instead. The payload must now carry the document's own box, equal to
    the viewport the exporter renders at."""
    import app.services.pdf_exporter as pe
    from app.utils.document_print_decor import DEFAULT_DOCUMENT_MARGIN, page_content_box_px
    fake = _FakeSession()

    async def fake_get_session(port=6969):
        return fake

    monkeypatch.setattr(pe, 'get_render_session', fake_get_session)
    asyncio.run(pe.export_document_pdf(markdown='# A\n\nbody', include_footer=False))
    call = fake.calls[0]

    w, h = page_content_box_px(DEFAULT_DOCUMENT_MARGIN)
    assert call['payload']['page'] == {'contentWidthPx': w, 'contentHeightPx': h}
    assert tuple(call['viewport']) == (w, h)
    # The reported gap: the document column is materially narrower than the
    # transcript constant the page previously assumed.
    assert w < 718 - 60

    # Direct builder contract: absent page_box -> no page block (the page's
    # transcript fallback applies); present -> integer px.
    assert 'page' not in pe.build_document_print_payload({}, ['x'])
    built = pe.build_document_print_payload({}, ['x'], page_box=(650.0, 994.0))
    assert built['page'] == {'contentWidthPx': 650, 'contentHeightPx': 994}


def test_transcript_payload_has_no_page_block_so_page_uses_transcript_defaults():
    """The transcript path paginates at capture_pdf's 10mm defaults, which
    are exactly the page's fallback box, so it sends no page block."""
    from app.services import pdf_exporter as pe
    payload = pe.build_print_payload([{'role': 'human', 'content': 'hi'}])
    assert 'page' not in payload


def test_export_document_pdf_footer_opt_out(monkeypatch):
    """include_footer=False is the only way to drop the footer."""
    import app.services.pdf_exporter as pe
    fake = _FakeSession()

    async def fake_get_session(port=6969):
        return fake

    monkeypatch.setattr(pe, 'get_render_session', fake_get_session)
    asyncio.run(pe.export_document_pdf(
        markdown='# Solo\n\nbody', include_footer=False,
    ))
    assert fake.calls[0]['footer_template'] is None


def test_document_export_request_footer_default_matches_other_routes():
    """POST /api/export/document must default includeFooter the same way the
    conversation/PDF routes do.  A False default here silently dropped the
    footer from every document export that did not name the flag."""
    from app.routes.export_routes import DocumentExportRequest, PdfExportRequest
    doc_req = DocumentExportRequest(name='report.md')
    pdf_req = PdfExportRequest(messages=[])
    assert doc_req.include_footer is True
    assert doc_req.include_footer == pdf_req.include_footer
    # Explicit opt-out via the camelCase alias the skill documents.
    assert DocumentExportRequest(name='r.md', includeFooter=False).include_footer is False


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


# ---------------------------------------------------------------------------
# doc-aesthetics iter_006: d024 /Subject, d014 runt-last-page squeeze
# ---------------------------------------------------------------------------

def _fake_session_factory(monkeypatch, results=None):
    import app.services.pdf_exporter as pe

    class _Seq(_FakeSession):
        async def capture_pdf(self, payload, **kwargs):
            self.calls.append({'payload': payload, **kwargs})
            if results is None:
                return b'%PDF-fake'
            return results[min(len(self.calls), len(results)) - 1]

    fake = _Seq()

    async def fake_get_session(port=6969):
        return fake

    monkeypatch.setattr(pe, 'get_render_session', fake_get_session)
    return pe, fake


def test_export_document_pdf_subject_is_document_label(monkeypatch):
    pe, fake = _fake_session_factory(monkeypatch)
    asyncio.run(pe.export_document_pdf(markdown='# A\n\nbody', include_footer=False))
    subj = fake.calls[0]['metadata']['/Subject']
    assert subj == 'Ziya document export'
    assert 'conversation' not in subj.lower()
    asyncio.run(pe.export_document_pdf(
        markdown='---\ntitle: T\nsubtitle: Sub line\n---\n# A\n\nbody',
        include_footer=False,
    ))
    assert fake.calls[1]['metadata']['/Subject'] == 'Sub line'


def _words_by_bytes(monkeypatch, pe, table):
    monkeypatch.setattr(pe, '_pdf_page_words', lambda b, margin=None: table.get(b))


def test_runt_last_page_triggers_squeeze_recapture(monkeypatch):
    from app.utils.document_print_decor import DOCUMENT_PRINT_CSS, DOCUMENT_SQUEEZE_CSS_STEPS
    pe, fake = _fake_session_factory(monkeypatch, [b'A', b'B'])
    _words_by_bytes(monkeypatch, pe, {b'A': [500] * 15 + [47], b'B': [500] * 15})
    pdf, meta = asyncio.run(pe.export_document_pdf(markdown='# A\n\nbody', include_footer=False))
    assert len(fake.calls) == 2
    assert fake.calls[1]['pre_capture_css'] == DOCUMENT_PRINT_CSS + DOCUMENT_SQUEEZE_CSS_STEPS[0]
    assert pdf == b'B'
    assert meta['squeeze_step'] == 1
    assert meta['pages_before_squeeze'] == 16


def test_squeeze_rejected_when_page_count_not_reduced(monkeypatch):
    pe, fake = _fake_session_factory(monkeypatch, [b'A', b'B', b'C', b'D'])
    _words_by_bytes(monkeypatch, pe, {b'A': [500] * 15 + [47],
                                     b'B': [500] * 15 + [30], b'C': [500] * 15 + [20],
                                     b'D': [500] * 15 + [25]})
    pdf, meta = asyncio.run(pe.export_document_pdf(markdown='# A\n\nbody', include_footer=False))
    assert len(fake.calls) == 4
    assert pdf == b'A'
    assert meta['squeeze_step'] == 0


def test_squeeze_step3_accepted_when_it_drops_a_page(monkeypatch):
    from app.utils.document_print_decor import DOCUMENT_PRINT_CSS, DOCUMENT_SQUEEZE_CSS_STEPS
    pe, fake = _fake_session_factory(monkeypatch, [b'A', b'B', b'C', b'D'])
    _words_by_bytes(monkeypatch, pe, {b'A': [500] * 15 + [47],
                                     b'B': [500] * 15 + [30], b'C': [500] * 15 + [20],
                                     b'D': [500] * 15})
    pdf, meta = asyncio.run(pe.export_document_pdf(markdown='# A\n\nbody', include_footer=False))
    assert len(fake.calls) == 4
    assert fake.calls[3]['pre_capture_css'] == DOCUMENT_PRINT_CSS + DOCUMENT_SQUEEZE_CSS_STEPS[2]
    assert pdf == b'D'
    assert meta['squeeze_step'] == 3
    assert meta['pages_before_squeeze'] == 16


def test_squeeze_rejects_candidate_that_lost_content(monkeypatch):
    # d028: a recapture where the page crashed (frontend error screen, one
    # page of ~30 words) has "fewer pages" but has lost the document; it
    # must be rejected, and a later genuine candidate may still win.
    pe, fake = _fake_session_factory(monkeypatch, [b'A', b'B', b'C', b'D'])
    _words_by_bytes(monkeypatch, pe, {b'A': [500] * 15 + [47],
                                     b'B': [30], b'C': [500] * 15 + [20],
                                     b'D': [500] * 15})
    pdf, meta = asyncio.run(pe.export_document_pdf(markdown='# A\n\nbody', include_footer=False))
    assert pdf == b'D'
    assert meta['squeeze_step'] == 3


def test_squeeze_keeps_first_capture_when_every_candidate_lost_content(monkeypatch):
    pe, fake = _fake_session_factory(monkeypatch, [b'A', b'B', b'C', b'D'])
    _words_by_bytes(monkeypatch, pe, {b'A': [500] * 15 + [47],
                                     b'B': [30], b'C': [30], b'D': [30]})
    pdf, meta = asyncio.run(pe.export_document_pdf(markdown='# A\n\nbody', include_footer=False))
    assert pdf == b'A'
    assert meta['squeeze_step'] == 0


def test_no_squeeze_when_last_page_full(monkeypatch):
    pe, fake = _fake_session_factory(monkeypatch, [b'A'])
    _words_by_bytes(monkeypatch, pe, {b'A': [500] * 15 + [300]})
    pdf, meta = asyncio.run(pe.export_document_pdf(markdown='# A\n\nbody', include_footer=False))
    assert len(fake.calls) == 1 and pdf == b'A'
    assert meta['squeeze_step'] == 0


def test_no_squeeze_when_last_section_is_short_after_pagebreak(monkeypatch):
    pe, fake = _fake_session_factory(monkeypatch, [b'A'])
    _words_by_bytes(monkeypatch, pe, {b'A': [500] * 5 + [35]})
    short = ' '.join(['word'] * 30)
    doc = '# A\n\n' + ' '.join(['x'] * 2000) + '\n\n<!-- ziya:pagebreak -->\n\n# End\n\n' + short + '\n'
    pdf, meta = asyncio.run(pe.export_document_pdf(markdown=doc, include_footer=False))
    assert len(fake.calls) == 1 and pdf == b'A'


def test_unparseable_pdf_never_squeezes(monkeypatch):
    from app.utils.document_print_decor import DOCUMENT_PRINT_CSS
    pe, fake = _fake_session_factory(monkeypatch)
    pdf, meta = asyncio.run(pe.export_document_pdf(markdown='# A\n\nbody', include_footer=False))
    assert len(fake.calls) == 1 and pdf == b'%PDF-fake'
    assert fake.calls[0]['pre_capture_css'] is DOCUMENT_PRINT_CSS


# ---------------------------------------------------------------------------
# doc-aesthetics iter_008: d027 figure float recapture
# ---------------------------------------------------------------------------

def _gaps_by_bytes(monkeypatch, pe, table):
    monkeypatch.setattr(pe, '_pdf_figure_gaps', lambda b, margin=None: table.get(b))


def _float_opts(call):
    js, opts = call['pre_capture_js'][0]
    return opts.get('floatFigures')


def test_figure_gap_triggers_float_recapture(monkeypatch):
    from app.utils.document_print_decor import page_content_box_px
    pe, fake = _fake_session_factory(monkeypatch, [b'A', b'B'])
    _words_by_bytes(monkeypatch, pe, {b'A': [500] * 15, b'B': [500] * 15})
    _gaps_by_bytes(monkeypatch, pe, {b'A': [(6, 0.26)], b'B': []})
    pdf, meta = asyncio.run(pe.export_document_pdf(markdown='# A\n\nbody', include_footer=False))
    assert len(fake.calls) == 2
    content_h = page_content_box_px(None)[1]
    assert _float_opts(fake.calls[1]) == {"6": round(0.26 * content_h)}
    assert _float_opts(fake.calls[0]) == {}
    assert pdf == b'B'
    assert meta['float_applied'] is True and meta['float_figures'] == [6]


def test_float_recapture_rejected_when_pages_grow_or_words_lost(monkeypatch):
    for cand_words in ([500] * 16, [30]):
        pe, fake = _fake_session_factory(monkeypatch, [b'A', b'B'])
        _words_by_bytes(monkeypatch, pe, {b'A': [500] * 15, b'B': cand_words})
        _gaps_by_bytes(monkeypatch, pe, {b'A': [(6, 0.26)], b'B': []})
        pdf, meta = asyncio.run(pe.export_document_pdf(markdown='# A\n\nbody', include_footer=False))
        assert len(fake.calls) == 2
        assert pdf == b'A', cand_words
        assert meta['float_applied'] is False and meta['float_figures'] == [6]


def test_float_rejected_when_gap_did_not_shrink(monkeypatch):
    pe, fake = _fake_session_factory(monkeypatch, [b'A', b'B'])
    _words_by_bytes(monkeypatch, pe, {b'A': [500] * 15, b'B': [500] * 15})
    _gaps_by_bytes(monkeypatch, pe, {b'A': [(6, 0.26)], b'B': [(6, 0.26)]})
    pdf, meta = asyncio.run(pe.export_document_pdf(markdown='# A\n\nbody', include_footer=False))
    assert pdf == b'A' and meta['float_applied'] is False


def test_float_uses_squeeze_css_when_squeeze_won(monkeypatch):
    from app.utils.document_print_decor import DOCUMENT_PRINT_CSS, DOCUMENT_SQUEEZE_CSS_STEPS
    pe, fake = _fake_session_factory(monkeypatch, [b'A', b'B', b'C', b'D', b'E'])
    _words_by_bytes(monkeypatch, pe, {b'A': [500] * 15 + [47],
                                     b'B': [500] * 15 + [30], b'C': [500] * 15 + [20],
                                     b'D': [500] * 15, b'E': [500] * 15})
    _gaps_by_bytes(monkeypatch, pe, {b'D': [(6, 0.26)], b'E': []})
    pdf, meta = asyncio.run(pe.export_document_pdf(markdown='# A\n\nbody', include_footer=False))
    assert len(fake.calls) == 5
    assert fake.calls[4]['pre_capture_css'] == DOCUMENT_PRINT_CSS + DOCUMENT_SQUEEZE_CSS_STEPS[2]
    assert pdf == b'E'
    assert meta['squeeze_step'] == 3 and meta['float_applied'] is True


def test_no_float_without_figure_gap(monkeypatch):
    pe, fake = _fake_session_factory(monkeypatch, [b'A'])
    _words_by_bytes(monkeypatch, pe, {b'A': [500] * 15})
    _gaps_by_bytes(monkeypatch, pe, {b'A': []})
    pdf, meta = asyncio.run(pe.export_document_pdf(markdown='# A\n\nbody', include_footer=False))
    assert len(fake.calls) == 1 and pdf == b'A'
    assert meta['float_applied'] is False and meta['float_figures'] == []


def _two_page_pdf(p1_lines: int, p2_first: str) -> bytes:
    """A4 PDF: page 1 has ``p1_lines`` text lines from the top margin, page 2
    starts with ``p2_first``.  Built with fpdf2 (core Helvetica)."""
    from fpdf import FPDF
    pdf = FPDF(unit="pt", format="A4")
    pdf.set_auto_page_break(False)
    pdf.set_font("Helvetica", size=11)
    top = 16 / 25.4 * 72
    for page_lines, first in ((p1_lines, None), (3, p2_first)):
        pdf.add_page()
        y = top + 2
        for i in range(page_lines):
            pdf.set_xy(54, y)
            pdf.cell(0, 14, first if (i == 0 and first) else f"Body line {i} of the text")
            y += 16
    return bytes(pdf.output())


def test_pdf_figure_gaps_detects_gap_before_figure_page():
    import app.services.pdf_exporter as pe
    margin = {"top": "16mm", "bottom": "16mm", "left": "16mm", "right": "16mm"}
    # ~0.40 of the printable height used on page 1 (746pt box, 16pt lines).
    blob = _two_page_pdf(19, "Figure 2 - Burst allowance")
    gaps = pe._pdf_figure_gaps(blob, margin)
    assert gaps and gaps[0][0] == 2, gaps
    assert 0.5 < gaps[0][1] < 0.65, gaps
    # Next page not a figure: no gap reported.
    assert pe._pdf_figure_gaps(_two_page_pdf(19, "Plain prose starts here"), margin) == []
    # Small gap: page 1 nearly full.
    assert pe._pdf_figure_gaps(_two_page_pdf(44, "Figure 2 - X"), margin) == []
    assert pe._pdf_figure_gaps(b'%PDF-fake', margin) is None


def test_float_accepted_when_unfloatable_figure_gap_remains(monkeypatch):
    # iter_008 real render: figure 5 is followed by a heading (nothing can
    # float), figure 6's gap closes.  The unchanged figure-5 gap must not
    # veto the figure-6 win.
    pe, fake = _fake_session_factory(monkeypatch, [b'A', b'B'])
    _words_by_bytes(monkeypatch, pe, {b'A': [500] * 15, b'B': [500] * 15})
    _gaps_by_bytes(monkeypatch, pe, {b'A': [(5, 0.32), (6, 0.26)], b'B': [(5, 0.32)]})
    pdf, meta = asyncio.run(pe.export_document_pdf(markdown='# A\n\nbody', include_footer=False))
    assert pdf == b'B' and meta['float_applied'] is True


def test_float_rejected_when_gap_only_moves_to_next_figure(monkeypatch):
    # iter_008 real render: floating Phase 4 above figure 6 left figures 6
    # and 7 back to back, so the gap reappeared (larger) before figure 7.
    pe, fake = _fake_session_factory(monkeypatch, [b'A', b'B'])
    _words_by_bytes(monkeypatch, pe, {b'A': [500] * 15, b'B': [500] * 15})
    _gaps_by_bytes(monkeypatch, pe, {b'A': [(5, 0.3194), (6, 0.2606)],
                                    b'B': [(5, 0.3194), (7, 0.3184)]})
    pdf, meta = asyncio.run(pe.export_document_pdf(markdown='# A\n\nbody', include_footer=False))
    assert pdf == b'A' and meta['float_applied'] is False
