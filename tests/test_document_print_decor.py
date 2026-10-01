"""
Tests for the document PDF aesthetic layer (app/utils/document_print_decor.py)
and the IR keys that feed it (document_ir: subtitle / date / numbering /
layout: titlepage).

Three tiers:

  1. Pure helpers — geometry, date formatting, numbering knobs, footer
     template, IR normalisation.  No browser.
  2. Browser-side passes, run for real in headless Chromium against static
     HTML fixtures via ``page.set_content`` (no Ziya server): the decoration
     pass (figure/table numbering + captions, section numbers, long-table
     flow, title-block reconciliation, idempotency) and the exporter's
     keep-with-next pass (stacked headings + table/figure binding).
  3. Capture-level: the finding that Chromium repeats a table's <thead> on
     every page ONLY under print media — asserted on the PDF text layer,
     which is what a reader sees.

Tiers 2-3 skip when Playwright/Chromium is unavailable.
"""
from __future__ import annotations

import asyncio
import datetime
import io

import pytest

from app.utils.document_ir import normalize_meta, parse_document
from app.utils.document_print_decor import (
    DEFAULT_DOCUMENT_MARGIN,
    DEFAULT_NUMBERING,
    DOCUMENT_PRINT_CSS,
    LONG_TABLE_PAGE_FRACTION,
    build_document_decorate_js,
    build_document_footer_template,
    format_document_date,
    normalize_numbering,
    page_content_box_px,
)


# ---------------------------------------------------------------------------
# 1. Pure helpers
# ---------------------------------------------------------------------------

def test_page_content_box_default_margins():
    w, h = page_content_box_px(None)
    # A4 210x297mm minus 19/19 horizontal (d026) and 16/18 vertical at 96dpi.
    assert w == round((210 - 38) * 96 / 25.4)
    assert h == round((297 - 34) * 96 / 25.4)


def test_page_content_box_honours_explicit_margins_and_falls_back_per_side():
    w, h = page_content_box_px({"left": "10mm", "right": "10mm", "top": "bogus"})
    assert w == round(190 * 96 / 25.4)
    # top unparseable -> default top (16mm); bottom missing -> default (18mm)
    assert h == round((297 - 16 - 18) * 96 / 25.4)


def test_page_content_box_accepts_in_cm_px():
    w, _ = page_content_box_px({"left": "1in", "right": "1cm"})
    assert w == round(210 * 96 / 25.4 - 96 - 10 * 96 / 25.4)


@pytest.mark.parametrize("value, expected", [
    (datetime.date(2026, 9, 19), "September 19, 2026"),
    (datetime.datetime(2026, 1, 5, 13, 0), "January 5, 2026"),
    ("2026-09-19", "September 19, 2026"),
    ("Q3 FY26", "Q3 FY26"),
    ("Draft — 19 Sep", "Draft — 19 Sep"),
    ("2026-09-19T10:00:00", "2026-09-19T10:00:00"),  # not a bare date: verbatim
])
def test_format_document_date(value, expected):
    assert format_document_date(value) == expected


def test_format_document_date_none_is_today():
    today = datetime.date.today()
    assert format_document_date(None) == format_document_date(today)
    assert format_document_date("  ") == format_document_date(today)


def test_normalize_numbering():
    assert normalize_numbering(None) == DEFAULT_NUMBERING
    assert normalize_numbering(True) == {"sections": True, "figures": True, "tables": True}
    assert normalize_numbering(False) == {"sections": False, "figures": False, "tables": False}
    assert normalize_numbering({"sections": True, "figures": "yes"}) == {
        "sections": True, "figures": True, "tables": True,
    }
    assert normalize_numbering("garbage") == DEFAULT_NUMBERING


def test_decorate_js_is_constant_and_options_carry_document_text():
    """Document text must travel in the options object, never be spliced
    into code (the same posture as the exporter's other injected JS)."""
    js1, o1 = build_document_decorate_js(title="A <b>", subtitle="S", author="Au",
                                         date="D", layout="titlepage",
                                         numbering={"sections": True},
                                         page_height_px=900)
    js2, o2 = build_document_decorate_js(title="Other")
    assert js1 == js2
    assert "A <b>" not in js1
    assert o1["title"] == "A <b>" and o1["layout"] == "titlepage"
    assert o1["numbering"] == {"sections": True, "figures": True, "tables": True}
    assert o1["pageHeightPx"] == 900
    assert o1["longTableFraction"] == LONG_TABLE_PAGE_FRACTION
    assert o2["layout"] == "plain" and o2["subtitle"] == ""


def test_document_footer_names_the_document_not_the_tool():
    f = build_document_footer_template(
        title="Queue <Depth>", author="dcohn", date="September 19, 2026",
        version="0.9", model="fable", provider="bedrock",
    )
    # Document line, escaped, in the order title · author · date.
    assert "Queue &lt;Depth&gt; · dcohn · September 19, 2026" in f
    # Tool credit demoted to the second line; the transcript tagline is gone.
    assert "Ziya v0.9 · fable (Bedrock)" in f
    assert "orchestration harness" not in f
    # Live page numbers and the inlined mark.
    assert 'class="pageNumber"' in f and 'class="totalPages"' in f
    assert 'src="data:image/png;base64,' in f
    # Footer templates render at font-size 0 unless the template sets one.
    assert "font-size:" in f


def test_document_footer_degrades_without_model_or_author():
    f = build_document_footer_template(title="T", model="unknown", provider="unknown")
    assert ">T</div>" in f
    assert ">Ziya</div>" in f  # bare credit, no version, no model


# --- IR keys ---------------------------------------------------------------

def test_normalize_meta_new_keys():
    meta = normalize_meta({
        "title": "T", "subtitle": " Sub ", "date": datetime.date(2026, 9, 19),
        "layout": "TitlePage", "numbering": {"sections": True, "tables": "no"},
    })
    assert meta["subtitle"] == "Sub"
    assert meta["date"] == "2026-09-19"          # date object -> ISO string
    assert meta["layout"] == "titlepage"
    assert meta["numbering"] == {"sections": True}  # non-bool dropped


def test_normalize_meta_numbering_shorthand_and_free_date():
    assert normalize_meta({"numbering": False})["numbering"] == {
        "sections": False, "figures": False, "tables": False,
    }
    assert normalize_meta({"numbering": "x"})["numbering"] is None
    assert normalize_meta({"date": "Q3 FY26"})["date"] == "Q3 FY26"
    assert normalize_meta({})["date"] is None and normalize_meta({})["subtitle"] is None


def test_parse_document_unquoted_yaml_date_stays_json_safe():
    meta, _ = parse_document("---\ntitle: X\ndate: 2026-09-19\n---\n# h\n")
    assert isinstance(meta["date"], str) and meta["date"] == "2026-09-19"


# ---------------------------------------------------------------------------
# 2/3. Browser-level (skipped without Playwright + Chromium)
# ---------------------------------------------------------------------------

def _chromium_available() -> bool:
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except ImportError:
        return False

    async def probe():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            b = await p.chromium.launch()
            await b.close()
        return True

    try:
        return asyncio.run(probe())
    except Exception:
        return False


browser = pytest.mark.skipif(not _chromium_available(),
                             reason="Playwright/Chromium not available")


def _run_in_page(html: str, steps):
    """Load ``html``, run ``steps(page)`` coroutine, return its result."""
    from playwright.async_api import async_playwright

    async def go():
        async with async_playwright() as p:
            b = await p.chromium.launch()
            page = await b.new_page(viewport={"width": 673, "height": 994})
            try:
                await page.set_content(html)
                await page.add_style_tag(content=DOCUMENT_PRINT_CSS)
                return await steps(page)
            finally:
                await b.close()

    return asyncio.run(go())


# Mirrors the /print DOM the decoration pass targets: #print-render-content,
# a report title block, .print-doc-section wrappers, an outer
# .d3-container[data-visualization-type] figure with an inner renderer
# container (as D3Renderer nests them), tables with/without caption lines.
_ROWS = "".join(f"<tr><td>{i}</td><td>v{i}</td></tr>" for i in range(80))
_DOC_HTML = f"""
<body class="ziya-print-mode">
<div id="print-render-root"><div id="print-render-content">
  <header class="print-doc-titleblock">
    <h1>Title</h1>
    <div class="print-doc-author">dcohn</div>
    <div class="print-doc-date">9/19/2026</div>
  </header>
  <div class="print-doc-section"><div>
    <h1>Intro</h1>
    <h2>Scope</h2>
    <p>Short paragraph.</p>
    <div class="d3-container" data-visualization-type="graphviz" style="height:300px;min-height:300px">
      <div class="d3-container graphviz-renderer-container">
        <div class="graphviz-wrapper" style="padding:1em">
          <svg viewBox="0 0 345 98" style="width:1280px;max-width:none;height:auto"><rect width="345" height="98"/></svg>
        </div>
      </div>
    </div>
    <p>Figure: Pipeline <em>stages</em>.</p>
    <h2>Data</h2>
    <p>Table: Small table.</p>
    <table><thead><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table>
    <p>Not a caption.</p>
    <table><thead><tr><th>HDRX</th><th>V</th></tr></thead><tbody>{_ROWS}</tbody></table>
    <h3>Deep</h3>
    <p>Text. See Table 2.</p>
  </div></div>
  <div class="print-doc-section"><div>
    <h1>Part Two</h1>
    <p>More.</p>
  </div></div>
</div></div>
</body>
"""

_DECORATE_STATE_JS = r"""
() => {
  const q = (s) => document.querySelector(s);
  const qa = (s) => Array.from(document.querySelectorAll(s));
  const fig = q('.print-doc-figure');
  const svg = fig && fig.querySelector('svg');
  return {
    bodyClass: document.body.className,
    figures: qa('.print-doc-figure').length,
    figCaption: fig ? fig.querySelector('.print-doc-caption').innerHTML : null,
    figCaptionParagraphGone: !qa('p').some(p => /^Figure:/.test(p.textContent)),
    svgWidth: svg ? svg.getBoundingClientRect().width : null,
    svgNaturalFit: svg ? svg.getAttribute('data-print-natural-fit') : null,
    outerHeight: fig ? fig.querySelector('.d3-container').getBoundingClientRect().height : null,
    tableCaptions: qa('table > caption.print-doc-table-caption').map(c => c.textContent),
    tableCaptionParagraphGone: !qa('p').some(p => /^Table:/.test(p.textContent)),
    notCaptionKept: qa('p').some(p => p.textContent === 'Not a caption.'),
    flowTables: qa('table.print-doc-table-flow').length,
    secnums: qa('.print-doc-secnum').map(s => s.textContent),
    headingText: qa('.print-doc-section h1, .print-doc-section h2, .print-doc-section h3').map(h => h.textContent),
    subtitle: q('.print-doc-subtitle') && q('.print-doc-subtitle').textContent,
    date: q('.print-doc-byline .print-doc-date') && q('.print-doc-byline .print-doc-date').textContent,
    titlepage: !!q('.print-doc-titleblock.print-doc-titlepage'),
    firstHeadingMarginTop: getComputedStyle(q('.print-doc-section h1')).marginTop,
  };
}
"""


@browser
def test_decorate_pass_numbers_captions_flows_and_reconciles_title():
    js, opts = build_document_decorate_js(
        numbering={"sections": True}, title="Title", subtitle="A subtitle",
        author="dcohn", date="September 19, 2026", layout="titlepage",
        page_height_px=994,
    )

    async def steps(page):
        stats = await page.evaluate(js, opts)
        state = await page.evaluate(_DECORATE_STATE_JS)
        # Idempotency: a second run changes nothing.
        stats2 = await page.evaluate(js, opts)
        state2 = await page.evaluate(_DECORATE_STATE_JS)
        return stats, state, stats2, state2

    stats, state, stats2, state2 = _run_in_page(_DOC_HTML, steps)

    assert stats["figures"] == 1 and stats["tables"] == 2
    assert stats["captions"] == 2 and stats["flowTables"] == 1
    assert stats["sections"] == 5  # Intro, Scope, Data, Deep, Part Two

    assert "ziya-print-document" in state["bodyClass"]
    # Figure: wrapped, numbered, caption text carried with its markup, the
    # caption paragraph consumed.
    assert state["figures"] == 1
    assert 'Figure 1</span>' in state["figCaption"]
    assert "Pipeline <em>stages</em>." in state["figCaption"]
    assert state["figCaptionParagraphGone"]
    # Natural-size clamp: graphviz viewBox 345pt -> 460px, not the 1280px the
    # renderer asked for, and not the column width either.
    assert state["svgNaturalFit"] is not None
    assert abs(state["svgWidth"] - 460) < 2
    # The renderer's reserved 300px box collapsed to the drawing.
    assert state["outerHeight"] < 200
    # Tables: caption above (Table 1 with text; Table 2 label only), the
    # caption paragraph consumed, an ordinary paragraph left alone, and the
    # page-tall table released to flow.
    assert state["tableCaptions"] == ["Table 1. Small table.", "Table 2"]
    assert state["tableCaptionParagraphGone"] and state["notCaptionKept"]
    assert state["flowTables"] == 1
    # Section numbers, with the space that the outline labels rely on.
    assert state["secnums"] == ["1 ", "1.1 ", "1.2 ", "1.2.1 ", "2 "]
    assert state["headingText"][0] == "1 Intro"
    assert state["headingText"][3] == "1.2.1 Deep"
    # Title block: subtitle inserted, date replaced with the formatted one,
    # titlepage class applied.
    assert state["subtitle"] == "A subtitle"
    assert state["date"] == "September 19, 2026"
    assert state["titlepage"] is True
    # First heading of a section has no top gap.
    assert state["firstHeadingMarginTop"] == "0px"

    assert stats2["figures"] == 0 and stats2["tables"] == 0 and stats2["sections"] == 0
    assert state2 == state


@browser
def test_decorate_pass_numbering_off_still_honours_explicit_captions():
    js, opts = build_document_decorate_js(numbering=False, title="T", layout="plain")

    async def steps(page):
        await page.evaluate(js, opts)
        return await page.evaluate(_DECORATE_STATE_JS)

    state = _run_in_page(_DOC_HTML, steps)
    assert state["secnums"] == []
    # No numbers; captions present only where the author wrote one.
    assert "Figure 1" not in state["figCaption"]
    assert "Pipeline <em>stages</em>." in state["figCaption"]
    assert state["tableCaptions"] == ["Small table."]


@browser
def test_keep_with_next_chains_headings_and_binds_tables_and_figures():
    """The exporter's shared keep-with-next pass: an h1 directly over an h2
    moves as one unit with the block after them; short tables and figure
    wrappers count as bindable blocks (they did not before)."""
    from app.services.pdf_exporter import _KEEP_WITH_NEXT_JS
    js, opts = build_document_decorate_js(title="T")

    async def steps(page):
        await page.evaluate(js, opts)
        wrapped = await page.evaluate(_KEEP_WITH_NEXT_JS)
        return wrapped, await page.evaluate(r"""() => Array.from(
            document.querySelectorAll('[data-print-keep-with-next]')).map(w => ({
              n: w.getAttribute('data-print-keep-with-next'),
              tags: Array.from(w.children).map(c => c.tagName + (c.className ? '.' + c.className.split(' ')[0] : '')),
            }))""")

    wrapped, groups = _run_in_page(_DOC_HTML, steps)
    tags = [g["tags"] for g in groups]
    assert ["H1", "H2", "P"] in tags, tags            # stacked headings + paragraph
    assert ["H2", "TABLE"] in tags, tags               # heading + short table
    assert ["H3", "P"] in tags and ["H1", "P"] in tags
    assert wrapped == len(groups) == 4
    # The page-tall table is NOT bound (it exceeds MAX_PX and must flow).
    assert not any("TABLE" in g["tags"] and g["n"] == "2" and len(g["tags"]) == 2
                   for g in groups if "HDRX" in str(g))


@browser
def test_keep_with_next_binds_heading_to_figure_wrapper():
    from app.services.pdf_exporter import _KEEP_WITH_NEXT_JS
    html = """<body class="ziya-print-mode"><div id="print-render-content">
      <div class="print-doc-section"><div>
        <h2>Diagram</h2>
        <div class="d3-container" data-visualization-type="mermaid">
          <svg viewBox="0 0 100 40" style="width:100px;height:40px"></svg>
        </div>
        <p>Figure: cap.</p>
      </div></div></div></body>"""
    js, opts = build_document_decorate_js(title="T")

    async def steps(page):
        await page.evaluate(js, opts)
        await page.evaluate(_KEEP_WITH_NEXT_JS)
        return await page.evaluate(r"""() => {
          const w = document.querySelector('[data-print-keep-with-next]');
          return w && Array.from(w.children).map(c => c.tagName + '.' + c.className.split(' ')[0]);
        }""")

    assert _run_in_page(html, steps) == ["H2.", "DIV.print-doc-figure"]


@browser
def test_table_header_repeats_only_under_print_media():
    """Capture-level: Chromium repeats <thead> across pages only when the
    page is under print media.  This is WHY export_document_pdf passes
    media="print"; asserted on the PDF text layer."""
    from playwright.async_api import async_playwright
    from pypdf import PdfReader

    rows = "".join(f"<tr><td>{i}</td><td>v{i}</td></tr>" for i in range(120))
    html = (f"<html><body class='ziya-print-mode ziya-print-document'>"
            f"<div id='print-render-content'>"
            f"<table class='print-doc-table-flow'><thead><tr><th>HDRX</th><th>V</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div></body></html>")

    async def go():
        async with async_playwright() as p:
            b = await p.chromium.launch()
            out = {}
            for media in ("screen", "print"):
                page = await b.new_page()
                await page.set_content(html)
                await page.add_style_tag(content=DOCUMENT_PRINT_CSS)
                await page.emulate_media(media=media)
                pdf = await page.pdf(format="A4", print_background=True)
                r = PdfReader(io.BytesIO(pdf))
                out[media] = [(pg.extract_text() or "").count("HDRX") for pg in r.pages]
                await page.close()
            await b.close()
            return out

    counts = asyncio.run(go())
    assert len(counts["print"]) >= 2, "fixture must span pages"
    assert all(c == 1 for c in counts["print"]), counts
    assert counts["screen"][0] == 1 and all(c == 0 for c in counts["screen"][1:]), counts


# ---------------------------------------------------------------------------
# Figure units: authored title above, "*Caption.*" paragraph below, and the
# oversize guard (doc-aesthetics loop iter_001: d002, d012, d001).
# ---------------------------------------------------------------------------

def _fig_unit_html(svg_w=500, svg_h=300, title=True, caption=True,
                   title_text="Figure 1 \u2014 The curve", extra=""):
    t = (f"<p><strong>{title_text}</strong> (artifact <code>x</code>).</p>"
         if title else "")
    c = ("<p><em>Caption.</em> Below the knee every bit offered is delivered; "
         "above it the queue fills and the tail drops, so goodput flattens while "
         "delay keeps growing for every flow that shares the bottleneck, and the "
         "sender only learns about it one round trip later.</p>"
         if caption else "")
    return f"""<body class="ziya-print-mode"><div id="print-render-content">
      <div class="print-doc-section"><div>
        <p>Intro paragraph.</p>
        {t}
        <div class="d3-container" data-visualization-type="vega-lite">
          <svg viewBox="0 0 {svg_w} {svg_h}" style="width:{svg_w}px;height:{svg_h}px"><rect width="{svg_w}" height="{svg_h}"/></svg>
        </div>
        {c}
        {extra}
      </div></div></div></body>"""


_FIG_STATE_JS = r"""
() => {
  const w = document.querySelector('.print-doc-figure');
  const svg = w && w.querySelector('svg');
  const cap = w && w.querySelector('.print-doc-caption');
  const f0 = w && w.firstElementChild;
  const lead = f0 && f0.classList.contains('print-doc-figure-head') ? f0.firstElementChild : f0;
  return {
    // The leading element of the unit (inside the oversize head box, d022).
    firstChildClass: lead ? lead.className : null,
    firstChildText: lead ? lead.textContent.trim() : null,
    titleOutside: Array.from(document.querySelectorAll('p')).some(
        p => !p.closest('.print-doc-figure') && /^Figure 1 \u2014/.test(p.textContent.trim())),
    authored: w && w.getAttribute('data-print-figure-authored'),
    captionText: cap ? cap.textContent.trim() : null,
    captionLabel: !!(w && w.querySelector('.print-doc-caption-label')),
    captionLong: !!(cap && cap.classList.contains('print-doc-caption-long')),
    captionParaLeft: Array.from(document.querySelectorAll('p')).some(
        p => !p.closest('.print-doc-figure') && /^Caption\./.test(p.textContent.trim())),
    oversize: w && w.getAttribute('data-print-figure-oversize'),
    fitReason: svg && svg.getAttribute('data-print-fit-reason'),
    svgH: svg ? svg.getBoundingClientRect().height : null,
    unitH: w ? w.getBoundingClientRect().height : null,
    titleTop: w && w.firstElementChild ? w.firstElementChild.getBoundingClientRect().top : null,
    svgBottom: svg ? svg.getBoundingClientRect().bottom : null,
    wrapBreak: w ? getComputedStyle(w).breakInside : null,
    drawingBreak: w && w.querySelector('.d3-container') ? getComputedStyle(w.querySelector('.d3-container')).breakInside : null,
  };
}
"""


def _decorate_and_read(html):
    js, opts = build_document_decorate_js(title="T", page_height_px=994)

    async def steps(page):
        stats = await page.evaluate(js, opts)
        state = await page.evaluate(_FIG_STATE_JS)
        stats2 = await page.evaluate(js, opts)
        state2 = await page.evaluate(_FIG_STATE_JS)
        return stats, state, stats2, state2

    return _run_in_page(html, steps)


@browser
def test_author_figure_title_adopted_into_wrapper():
    """d002: the author's '**Figure N — …**' paragraph ABOVE a figure moves
    into the wrapper as its first child, so it cannot be stranded at a page
    bottom while the drawing goes to the next page."""
    stats, state, stats2, state2 = _decorate_and_read(_fig_unit_html())
    assert state["firstChildClass"] == "print-doc-figure-title"
    assert state["firstChildText"].startswith("Figure 1 \u2014 The curve")
    assert not state["titleOutside"]
    assert state["authored"] == "1"
    assert stats["titles"] == 1
    # idempotent
    assert stats2["figures"] == 0 and stats2["titles"] == 0 and state2 == state


@browser
def test_caption_lead_paragraph_becomes_caption_without_duplicate_label():
    """d012: '*Caption.* …' below a figure becomes the caption (the lead word
    removed as a DOM node), and no auto 'Figure N' label duplicates the
    authored title's number."""
    stats, state, _s2, _st2 = _decorate_and_read(_fig_unit_html())
    assert state["captionText"].startswith("Below the knee"), state["captionText"]
    assert "Caption." not in state["captionText"]
    assert not state["captionLabel"]
    assert not state["captionParaLeft"]
    assert state["captionLong"]  # > 200 chars -> left-aligned full-measure caption
    assert stats["captions"] == 1


@browser
def test_caption_lead_without_title_keeps_auto_label():
    stats, state, _s2, _st2 = _decorate_and_read(_fig_unit_html(title=False))
    assert state["captionLabel"]
    assert state["captionText"].startswith("Figure 1. Below the knee")


@browser
def test_prose_mentioning_figure_not_adopted():
    prose = ("Figure 3 shows that the knee moves left as more flows share the "
             "bottleneck; " + "the queue grows and drops arrive late. " * 6)
    html = _fig_unit_html(title_text=prose)
    stats, state, _s2, _st2 = _decorate_and_read(html)
    assert state["firstChildClass"] != "print-doc-figure-title"
    assert stats["titles"] == 0
    assert state["captionLabel"]  # no authored title -> auto label stays


@browser
def test_unreferenced_bare_table_label_omitted():
    """d012: a table with no caption line that the prose never refers to gets
    no bare 'Table N' caption; a referenced one keeps its label."""
    html = """<body class="ziya-print-mode"><div id="print-render-content">
      <div class="print-doc-section"><div>
        <p>Plain text.</p>
        <table><thead><tr><th>A</th></tr></thead><tbody><tr><td>1</td></tr></tbody></table>
        <p>As Table 2 shows, it matters.</p>
        <table><thead><tr><th>B</th></tr></thead><tbody><tr><td>2</td></tr></tbody></table>
      </div></div></div></body>"""
    js, opts = build_document_decorate_js(title="T", page_height_px=994)

    async def steps(page):
        stats = await page.evaluate(js, opts)
        caps = await page.evaluate(r"""() => Array.from(document.querySelectorAll('table')).map(
            t => [t.getAttribute('data-print-table'), t.querySelector('caption') ? t.querySelector('caption').textContent : null])""")
        stats2 = await page.evaluate(js, opts)
        return stats, caps, stats2

    stats, caps, stats2 = _run_in_page(html, steps)
    assert caps == [["1", None], ["2", "Table 2"]], caps
    assert stats["tables"] == 2 and stats2["tables"] == 0


@browser
def test_oversize_unit_marked_and_splittable():
    """d001 (L2 part): a figure unit taller than the page is either scaled to
    fit (small shrink) or marked oversize so only the caption may break
    away — the title still leads the drawing."""
    # Slightly too tall with title + caption: scaled, one page.
    _s, st, _s2, _st2 = _decorate_and_read(_fig_unit_html(svg_h=920))
    assert st["oversize"] is None
    assert st["fitReason"] == "unit", st
    assert st["unitH"] <= 994 * 0.97 + 1, st
    # Far too tall: marked oversize, wrapper may break, drawing may not,
    # title still first.
    _s, st, _s2, _st2 = _decorate_and_read(_fig_unit_html(svg_h=1100))
    assert st["oversize"] == "1", st
    assert st["firstChildClass"] == "print-doc-figure-title"
    assert st["wrapBreak"] == "auto" and st["drawingBreak"] == "avoid", st
    # A small figure is untouched by the guard.
    _s, st, _s2, _st2 = _decorate_and_read(_fig_unit_html(svg_h=300))
    assert st["oversize"] is None and st["fitReason"] is None
    assert abs(st["svgH"] - 300) < 1


# ---------------------------------------------------------------------------
# SVG content fit (doc-aesthetics loop iter_003: d004 overflowing chart text
# clipped by the page box; d003 down-scaled drawing with dead margins).
# ---------------------------------------------------------------------------

def _svg_fig_html(inner, viewbox="0 0 400 200", style="width:400px;height:200px",
                  vtype="vega-lite"):
    return f"""<body class="ziya-print-mode"><div id="print-render-content">
      <div class="print-doc-section"><div>
        <p>Intro paragraph.</p>
        <div class="d3-container" data-visualization-type="{vtype}">
          <svg viewBox="{viewbox}" style="{style}">{inner}</svg>
        </div>
        <p>After.</p>
      </div></div></div></body>"""


# Content extent in root user units: union of painted leaves' client rects
# mapped through the inverse screen CTM (what the page would clip).
_SVG_STATE_JS = r"""
() => {
  const svg = document.querySelector('.d3-container svg');
  const vb = svg.getAttribute('viewBox').split(/[\s,]+/).map(Number);
  const inv = svg.getScreenCTM().inverse();
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const el of svg.querySelectorAll('text, rect, path, line, circle')) {
    const r = el.getBoundingClientRect();
    if (!r.width && !r.height) continue;
    const a = new DOMPoint(r.left, r.top).matrixTransform(inv);
    const b = new DOMPoint(r.right, r.bottom).matrixTransform(inv);
    x0 = Math.min(x0, a.x); y0 = Math.min(y0, a.y); x1 = Math.max(x1, b.x); y1 = Math.max(y1, b.y);
  }
  const t = svg.querySelector('text');
  return {
    vb, content: [x0, y0, x1, y1],
    inside: x0 >= vb[0] - 2 && y0 >= vb[1] - 2 && x1 <= vb[0] + vb[2] + 2 && y1 <= vb[1] + vb[3] + 2,
    tspans: t ? t.querySelectorAll('tspan').length : 0,
    fontSize: t ? parseFloat(getComputedStyle(t).fontSize) : null,
    wrap: t ? t.getAttribute('data-print-text-wrap') : null,
    textFit: t ? t.getAttribute('data-print-text-fit') : null,
    fitReason: svg.getAttribute('data-print-fit-reason'),
    pxPerUser: svg.getScreenCTM().a,
    width: svg.getBoundingClientRect().width,
    colW: document.querySelector('.print-doc-section > div').getBoundingClientRect().width,
  };
}
"""


def _fit_and_read(html):
    js, opts = build_document_decorate_js(title="T", page_height_px=994)

    async def steps(page):
        before = await page.evaluate(_SVG_STATE_JS)
        stats = await page.evaluate(js, opts)
        after = await page.evaluate(_SVG_STATE_JS)
        stats2 = await page.evaluate(js, opts)
        after2 = await page.evaluate(_SVG_STATE_JS)
        return before, stats, after, stats2, after2

    return _run_in_page(html, steps)


_LONG_SUBTITLE = ("When peers load the spot in the busy morning hours the heavy "
                  "terminal is squeezed far below its occupancy weighted share")


@browser
def test_overflowing_svg_title_line_is_wrapped_not_clipped():
    """d004: a vega title/subtitle line wider than the viewBox is wrapped at a
    word boundary (font size kept) instead of spilling past the drawing where
    the page box clips it."""
    inner = (
        '<g class="mark-group role-title"><g transform="translate(200,20)">'
        '<g class="mark-text role-title-subtitle">'
        '<text text-anchor="middle" transform="translate(0,10)" font-size="10px">'
        f'<tspan>{_LONG_SUBTITLE}</tspan></text></g></g></g>'
        '<rect y="60" width="400" height="140" fill="#ccc"/>'
    )
    before, stats, after, stats2, after2 = _fit_and_read(_svg_fig_html(inner))
    assert not before["inside"], before  # fixture really overflows
    assert after["inside"], after
    assert after["tspans"] == 2, after
    assert abs(after["fontSize"] - 10) < 0.01, after
    assert after["wrap"] == "1", after
    assert stats["textWrapped"] == 1, stats
    assert after2 == after  # idempotent


@browser
def test_overflowing_text_mark_shrinks_to_floor_then_viewbox_expands():
    """d004 (text marks): shrink toward the fit but never below the 6.5pt
    floor (8.67 rendered px); any residual is brought inside the drawing by
    growing the viewBox — clipped text is never acceptable."""
    inner = ('<rect width="400" height="200" fill="#eee"/>'
             '<text x="300" y="100" font-size="12px">'
             'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa</text>')
    before, stats, after, _s2, after2 = _fit_and_read(_svg_fig_html(inner))
    assert not before["inside"], before
    assert after["inside"], after
    assert after["fontSize"] * after["pxPerUser"] >= 8.6, after
    assert after["textFit"] is not None, after
    assert after["fitReason"] == "content", after
    assert stats["textFitted"] == 1 and stats["contentFit"] == 1, stats
    assert after2 == after


@browser
def test_svg_inside_viewbox_untouched():
    inner = ('<rect width="400" height="200" fill="#eee"/>'
             '<text x="20" y="100" font-size="12px">short label</text>')
    before, stats, after, _s2, _a2 = _fit_and_read(_svg_fig_html(inner))
    assert after["fitReason"] is None and after["wrap"] is None and after["textFit"] is None
    assert abs(after["width"] - before["width"]) < 0.5
    assert after["vb"] == before["vb"]
    assert stats["contentFit"] == 0 and stats["textWrapped"] == 0 and stats["textFitted"] == 0


@browser
def test_downscaled_svg_dead_margins_trimmed():
    """d003: a DOWN-scaled drawing (mermaid timeline, viewBox 1790 wide shown
    at 600px) with dead margins gets its viewBox trimmed to the content, so
    the same box shows the content larger."""
    inner = '<rect x="150" y="-10" width="1690" height="700" fill="#ddd"/>'
    before, stats, after, _s2, after2 = _fit_and_read(_svg_fig_html(
        inner, viewbox="100 -60 1790 800",
        style="max-width:none !important;width:600px !important;height:268px !important",
        vtype="mermaid"))
    assert after["fitReason"] == "trim", after
    assert after["inside"], after
    assert after["pxPerUser"] / before["pxPerUser"] >= 1.04, (before, after)
    assert after["width"] <= after["colW"] + 0.5
    assert after2 == after


@browser
def test_downscaled_svg_widened_to_column():
    """d003: a renderer inline width narrower than the column on a drawing
    whose natural width exceeds the column is widened to the column."""
    inner = '<rect x="0" y="0" width="1790" height="800" fill="#ddd"/>'
    before, stats, after, _s2, _a2 = _fit_and_read(_svg_fig_html(
        inner, viewbox="0 0 1790 800",
        style="max-width:none !important;width:600px !important;height:268px !important",
        vtype="mermaid"))
    assert abs(before["width"] - 600) < 0.5
    assert after["fitReason"] == "widen", after
    assert abs(after["width"] - after["colW"]) < 1, after


@browser
def test_height_limited_svg_not_widened():
    """A tall drawing held narrow by its height (Fig 3) must not be widened
    into a page-overflowing figure."""
    inner = '<rect x="0" y="0" width="476" height="1794" fill="#ddd"/>'
    before, _stats, after, _s2, _a2 = _fit_and_read(_svg_fig_html(
        inner, viewbox="0 0 476 1794",
        style="width:239px !important;height:900px !important", vtype="mermaid"))
    assert after["fitReason"] not in ("widen", "trim"), after
    assert abs(after["width"] - before["width"]) < 0.5


@browser
def test_clipped_mark_does_not_grow_viewbox():
    """A plot mark that extends past the viewBox but is clipped to the plot
    area (vega clip: true — Fig 4's dashed model line) paints nothing
    outside, so it must not grow the viewBox (it would open a blank band)."""
    inner = ('<defs><clipPath id="c1"><rect x="0" y="0" width="400" height="200"/></clipPath></defs>'
             '<g clip-path="url(#c1)"><rect width="400" height="200" fill="#eee"/>'
             '<path d="M0,-136L300,150" stroke="green" stroke-width="3" fill="none"/></g>')
    before, stats, after, _s2, _a2 = _fit_and_read(_svg_fig_html(inner))
    assert after["fitReason"] is None, after
    assert after["vb"] == before["vb"]
    assert stats["contentFit"] == 0, stats


# ---------------------------------------------------------------------------
# Table column balancing (doc-aesthetics loop iter_004: d007 — Chromium's
# auto layout shares width by max-content, so one long cell in a sparse
# column starves a column that wraps on every row).
# ---------------------------------------------------------------------------

def _table_html(ncols=5, colgroup=""):
    labels = ["A. Host NIC ring buffer (per-queue descriptors, 4,096 entries)",
              "B. Switch egress buffer (shared across all ports, dynamic threshold)",
              "C. TXPA shared FPGA pool (64 MiB per Merlin, shared by 1,024 queues)",
              "D. Shaper token bucket (per-flow burst allowance, refilled at line rate)",
              "E. Receiver socket buffer (autotuned, bounded by rmem_max)"]
    col2 = ["Descriptor exhaustion under bursts from many flows at once",
            "Tail drop when the dynamic threshold is exceeded for the port",
            "Pool exhaustion when many queues burst together in the same window",
            "Tokens exhausted, packets held back until the bucket refills again",
            "Window closes and the sender stalls waiting for the reader"]
    last = ["yes", "partial",
            "Counter exists but is sampled only every five minutes by the fleet "
            "collector and is aggregated across all queues on the device so it "
            "cannot attribute drops to a flow; the per-queue view needs a new "
            "export path from the FPGA, a schema change in the metrics pipeline "
            "and a dashboard, none of which is funded this half, so for now the "
            "only signal is the aggregate drop rate, which hides which tenant "
            "is being starved and which one is causing it",
            "no", "yes"]
    head = ["Drop point", "Failure mode", "Scope", "Rate", "Counter today"][:ncols]
    rows = []
    for i in range(5):
        cells = [f"<strong>{labels[i]}</strong>", col2[i], "per queue", f"{i * 7 + 3}%", last[i]][:ncols]
        rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    return f"""<body class="ziya-print-mode"><div id="print-render-content">
      <div class="print-doc-section"><div>
        <p>Intro.</p>
        <table style="border-collapse: collapse; width: 100%; margin-bottom: 1em;">{colgroup}<thead><tr>{''.join(f'<th>{h}</th>' for h in head)}</tr></thead>
        <tbody>{''.join(rows)}</tbody></table>
      </div></div></div></body>"""


_TABLE_STATE_JS = r"""
() => {
  const t = document.querySelector('#print-render-content table');
  const linesOf = (el) => {
    const r = document.createRange(); r.selectNodeContents(el);
    const tops = new Set();
    for (const q of r.getClientRects()) if (q.width > 0.5) tops.add(Math.round(q.top));
    return tops.size;
  };
  const cells = Array.from(t.querySelectorAll('td'));
  return {
    balanced: t.getAttribute('data-print-colbalance'),
    cols: t.querySelectorAll(':scope > colgroup > col').length,
    maxLines: Math.max(...cells.map(linesOf)),
    lines: cells.map(linesOf),
    height: t.getBoundingClientRect().height,
    width: t.getBoundingClientRect().width,
    colW: t.parentElement.getBoundingClientRect().width,
  };
}
"""


def _table_decorate(html, **overrides):
    js, opts = build_document_decorate_js(title="T", page_height_px=994)
    opts.update(overrides)

    async def steps(page):
        stats = await page.evaluate(js, opts)
        state = await page.evaluate(_TABLE_STATE_JS)
        stats2 = await page.evaluate(js, opts)
        state2 = await page.evaluate(_TABLE_STATE_JS)
        return stats, state, stats2, state2

    return _run_in_page(html, steps)


@browser
def test_table_columns_balanced_to_reduce_wrapping():
    """d007: column widths are chosen to minimise the table's row heights, so a
    column wrapping on every row gets the width a sparse column was hoarding."""
    _s0, off, _s0b, _off2 = _table_decorate(_table_html(), colBalance=False)
    stats, on, stats2, on2 = _table_decorate(_table_html())
    assert off["balanced"] is None
    assert on["balanced"] == "1", (stats, on)
    assert on["cols"] == 5
    assert stats.get("colBalanced") == 1
    # R4's defect is a cell wrapping >= 5 lines; the pass must cut those and
    # make the table shorter overall (one inherently long cell may grow).
    narrow = lambda st: sum(1 for k in st["lines"] if k >= 5)  # noqa: E731
    assert narrow(on) < narrow(off), (off["lines"], on["lines"])
    assert on["height"] < off["height"] - 10
    assert abs(on["width"] - on["colW"]) < 1.5  # still full text width
    # idempotent
    assert on2 == on and not stats2.get("colBalanced")


@browser
def test_table_with_authored_widths_not_rebalanced():
    cg = "<colgroup>" + '<col style="width:40%">' + "<col>" * 4 + "</colgroup>"
    stats, st, _s2, _st2 = _table_decorate(_table_html(colgroup=cg))
    assert st["balanced"] is None
    assert not stats.get("colBalanced")


@browser
def test_two_column_table_not_rebalanced():
    stats, st, _s2, _st2 = _table_decorate(_table_html(ncols=2))
    assert st["balanced"] is None and st["cols"] == 0


# ---------------------------------------------------------------------------
# Reviewer / editorial notes (doc-aesthetics loop iter_004: d008 — R10).
# ---------------------------------------------------------------------------

# The /print markdown renderer puts an inline style on every blockquote
# (seen in the iter_004 DOM); the note styling must beat it.
_BQ_INLINE = ("border-left: 4px solid rgb(107, 114, 128); color: rgb(75, 85, 99); "
              "background: rgba(0, 0, 0, 0.03); margin: 0.6em 0px; padding: 0.4em 0.9em; "
              "border-radius: 3px;")
_NOTE_HTML = f"""<body class="ziya-print-mode"><div id="print-render-content">
  <div class="print-doc-section"><div>
    <blockquote id="n1" style="{_BQ_INLINE}"><p><strong>Reviewer note 1 (G #3, remove before sending):</strong> tighten.</p></blockquote>
    <blockquote id="q1" style="{_BQ_INLINE}"><p><strong>Terms used here (plain language).</strong> A queue is a line.</p></blockquote>
    <blockquote id="q2" style="{_BQ_INLINE}"><p>As the note says, the knee moves left.</p></blockquote>
    <blockquote id="n2" style="{_BQ_INLINE}"><p>TODO: add the fleet counter.</p></blockquote>
  </div></div></div></body>"""

_NOTE_STATE_JS = r"""
() => {
  const r = {};
  for (const id of ['n1', 'q1', 'q2', 'n2']) {
    const b = document.getElementById(id);
    r[id] = { note: b.classList.contains('print-doc-note'),
              kind: b.getAttribute('data-print-note'),
              bg: getComputedStyle(b).backgroundColor,
              border: getComputedStyle(b).borderLeftColor };
  }
  return r;
}
"""


def _note_decorate():
    js, opts = build_document_decorate_js(title="T", page_height_px=994)

    async def steps(page):
        stats = await page.evaluate(js, opts)
        state = await page.evaluate(_NOTE_STATE_JS)
        stats2 = await page.evaluate(js, opts)
        state2 = await page.evaluate(_NOTE_STATE_JS)
        return stats, state, stats2, state2

    return _run_in_page(_NOTE_HTML, steps)


@browser
def test_reviewer_note_blockquote_styled_as_note():
    stats, st, stats2, st2 = _note_decorate()
    assert st["n1"]["note"] and st["n1"]["kind"] == "reviewer"
    assert st["n2"]["note"] and st["n2"]["kind"] == "todo"
    assert not st["q1"]["note"]
    assert stats.get("notes") == 2
    assert st["n1"]["bg"] == "rgb(255, 248, 225)"
    assert st["q1"]["bg"] in ("rgba(0, 0, 0, 0)", "transparent")
    assert st["n1"]["border"] != st["q1"]["border"]
    # idempotent
    assert st2 == st and not stats2.get("notes")


@browser
def test_prose_quote_mentioning_note_not_styled():
    _stats, st, _s2, _st2 = _note_decorate()
    assert not st["q2"]["note"]
    assert st["q2"]["bg"] in ("rgba(0, 0, 0, 0)", "transparent")


@browser
def test_oversize_title_and_drawing_bound_in_unbreakable_head():
    """d022 (found in iter_004): on an oversize (splittable) unit the title's
    break-after: avoid alone did not hold in the real document — Chromium
    split the 2-line title across p4/p5 when title + drawing sat at the page
    limit.  Title and drawing must share one unbreakable box; only the
    caption may move on."""
    js, opts = build_document_decorate_js(title="T", page_height_px=994)

    async def steps(page):
        await page.evaluate(js, opts)
        r = await page.evaluate("""() => {
          const w = document.querySelector('.print-doc-figure');
          const h = w.querySelector(':scope > .print-doc-figure-head');
          return { oversize: w.getAttribute('data-print-figure-oversize'),
                   head: !!h,
                   headBreak: h ? getComputedStyle(h).breakInside : null,
                   headKids: h ? Array.from(h.children).map(c => c.className.trim()) : [],
                   titleMargin: h && h.firstElementChild ? getComputedStyle(h.firstElementChild).marginBottom : null,
                   capOutside: !!w.querySelector(':scope > .print-doc-caption') };
        }""")
        await page.evaluate(js, opts)  # idempotent: no second head
        n = await page.evaluate("() => document.querySelectorAll('.print-doc-figure-head').length")
        return r, n

    r, n = _run_in_page(_fig_unit_html(svg_h=1100), steps)
    assert r["oversize"] == "1"
    assert r["head"] and r["headBreak"] == "avoid", r
    assert r["headKids"][0] == "print-doc-figure-title" and r["headKids"][1].startswith("d3-container"), r
    assert r["titleMargin"] == "6px"  # title styling still applies inside the head
    assert r["capOutside"]
    assert n == 1


# ---------------------------------------------------------------------------
# iter_005: d009 body measure (R9) and d015 footer legibility (R7)
# ---------------------------------------------------------------------------

def _content_rule(css: str) -> str:
    import re
    m = re.search(r"body\.ziya-print-document #print-render-content \{([^}]*)\}", css)
    assert m, "no #print-render-content rule"
    return m.group(1)


def test_body_text_is_11pt_with_145_leading():
    """d009: 10.5pt on the author's 504pt column set ~98 chars/line (R9 wants
    60-95); the margins are the author's, so the body size is the lever."""
    rule = _content_rule(DOCUMENT_PRINT_CSS)
    assert "font-size: 11pt;" in rule, rule
    assert "line-height: 1.45;" in rule, rule


def test_decorate_js_has_measure_guard():
    js, _ = build_document_decorate_js(title="T")
    assert "fitMeasure" in js
    assert "MEASURE_MAX = 94" in js
    assert "BODY_PT_CAP = 11;" in js  # R3: body 10-11pt


_LOREM = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do "
          "eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim "
          "ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut "
          "aliquip ex ea commodo consequat. Duis aute irure dolor in "
          "reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla "
          "pariatur. Excepteur sint occaecat cupidatat non proident, sunt in "
          "culpa qui officia deserunt mollit anim id est laborum. ")


def _measure_html(width_px: int, body_pt: float = 0) -> str:
    paras = "".join(f"<p>{_LOREM * 2}</p>" for _ in range(8))
    size = f"font-size:{body_pt}pt;" if body_pt else ""
    return (f'<body class="ziya-print-mode"><div id="print-render-root">'
            f'<div id="print-render-content" style="width:{width_px}px;{size}'
            f"font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif\">"
            f'<h1>H</h1>{paras}</div></div></body>')


def _decorate_stats(html: str):
    js, opts = build_document_decorate_js(title="T", page_height_px=994)

    async def steps(page):
        s = await page.evaluate(js, opts)
        s2 = await page.evaluate(js, opts)  # idempotent: never grows twice
        fs = await page.evaluate(
            "() => getComputedStyle(document.getElementById('print-render-content')).fontSize")
        return s, s2, fs

    return _run_in_page(html, steps)


@browser
def test_measure_guard_raises_small_body_on_wide_column():
    """A body set below the 11pt default (10.5pt inline) on a 600px column is
    > 94 chars/line: the guard steps it up, capped at 11pt (R3 ceiling)."""
    s, s2, fs = _decorate_stats(_measure_html(600, body_pt=10.5))
    assert s["bodyPt"] > 10.5 and s["bodyPt"] <= 11.0, s
    assert s["measureChars"] is not None and s["measureChars"] <= 94, s
    assert s2["bodyPt"] == s["bodyPt"], (s, s2)
    assert abs(float(fs[:-2]) - s["bodyPt"] * 96 / 72) < 0.2, fs


@browser
def test_measure_guard_never_exceeds_11pt():
    s, _s2, _fs = _decorate_stats(_measure_html(780))
    assert s["bodyPt"] == 11.0, s
    assert s["measureChars"] > 94, s  # reported, not "fixed" past R3


@browser
def test_measure_guard_leaves_normal_column_alone():
    s, _s2, fs = _decorate_stats(_measure_html(600))
    assert s["bodyPt"] == 11.0, s
    assert s["measureChars"] <= 94, s
    assert fs == "14.6667px", fs


def test_document_footer_text_at_least_6_5pt():
    """d015: Chromium does not scale footer templates, so 7px/6.2px printed at
    5.25pt/4.65pt — below the 6.5pt legibility floor used for figures (R2)."""
    import re
    html = build_document_footer_template(title="T", author="A", date="D", version="1")
    px = [float(v) for v in re.findall(r"font-size:([\d.]+)px", html)]
    assert px, html
    assert min(px) * 0.75 >= 6.5, px
    assert "pageNumber" in html and "totalPages" in html


# ---------------------------------------------------------------------------
# d014 (doc-aesthetics iter_006): squeeze steps for a runt last page
# ---------------------------------------------------------------------------

def test_squeeze_steps_never_change_font_size_and_keep_leading_floor():
    import re as _re
    from app.utils.document_print_decor import DOCUMENT_SQUEEZE_CSS_STEPS
    assert len(DOCUMENT_SQUEEZE_CSS_STEPS) == 3
    for step in DOCUMENT_SQUEEZE_CSS_STEPS:
        assert step.strip()
        # R3: the body stays at 11pt — a squeeze may never touch font-size.
        assert "font-size" not in step
        for lh in _re.findall(r"line-height:\s*([0-9.]+)", step):
            assert float(lh) >= 1.38, lh
        # Every rule is scoped to the document render, never global.
        for sel in _re.findall(r"([^{}]+)\{", step):
            for part in sel.split(","):
                part = part.strip()
                if part and not part.startswith("/*"):
                    assert part.startswith("body.ziya-print-document"), part
    # Step 2 is step 1 plus tighter leading.
    assert DOCUMENT_SQUEEZE_CSS_STEPS[1].startswith(DOCUMENT_SQUEEZE_CSS_STEPS[0])
    assert "line-height" in DOCUMENT_SQUEEZE_CSS_STEPS[1]
    # Step 1 is spacing only; leading changes only in step 2.
    assert "line-height" not in DOCUMENT_SQUEEZE_CSS_STEPS[0]


# ---------------------------------------------------------------------------
# doc-aesthetics iter_007: d014 squeeze step 3, d026 default side margins
# ---------------------------------------------------------------------------

def test_squeeze_step3_lets_medium_tables_flow_with_repeating_header():
    import re as _re
    from app.utils.document_print_decor import (
        DOCUMENT_SQUEEZE_CSS_STEPS, _SQUEEZE_SPACING_CSS,
    )
    assert len(DOCUMENT_SQUEEZE_CSS_STEPS) == 3
    step3 = DOCUMENT_SQUEEZE_CSS_STEPS[2]
    assert step3.startswith(_SQUEEZE_SPACING_CSS)
    # Step 3 is spacing + table flow; it deliberately omits the leading change.
    assert "line-height" not in step3
    assert "font-size" not in step3
    for needle in (":has(> tbody > tr:nth-child(4))", "break-inside: auto",
                   "table-header-group", "break-before: avoid"):
        assert needle in step3, needle
    for sel in _re.findall(r"([^{}]+)\{", _re.sub(r"/\*.*?\*/", "", step3, flags=_re.S)):
        for part in sel.split(","):
            part = part.strip()
            if part:
                assert part.startswith("body.ziya-print-document"), part


def test_default_document_margin_sides_give_r9_measure():
    from app.utils.document_print_decor import DEFAULT_DOCUMENT_MARGIN
    assert DEFAULT_DOCUMENT_MARGIN["left"] == DEFAULT_DOCUMENT_MARGIN["right"] == "19mm"
    assert DEFAULT_DOCUMENT_MARGIN["top"] == "16mm"
    assert DEFAULT_DOCUMENT_MARGIN["bottom"] == "18mm"
    assert page_content_box_px(None)[0] == round((210 - 38) * 96 / 25.4)


# ---------------------------------------------------------------------------
# doc-aesthetics iter_008: d027 figure float (pass 5)
# ---------------------------------------------------------------------------

def _float_html(after: str) -> str:
    """h2, intro, a figure unit (title / drawing / caption), then ``after``."""
    return f"""<body class="ziya-print-mode"><div id="print-render-content">
      <div class="print-doc-section"><div>
        <h2 id="s1">Section one</h2>
        <p>Intro paragraph.</p>
        <p><strong>Figure 1 \u2014 T</strong> (artifact <code>x</code>).</p>
        <div class="d3-container" data-visualization-type="vega-lite">
          <svg viewBox="0 0 600 300" style="width:600px;height:300px"><rect width="600" height="300"/></svg>
        </div>
        <p><em>Caption.</em> What the drawing shows.</p>
        {after}
      </div></div></div></body>"""


_FLOAT_PARAS = "".join(
    f'<p class="fp" id="fp{i}" style="height:60px;margin:0;overflow:hidden">Para {i}.</p>'
    for i in range(1, 4)
) + '<h2 id="s2">Section two</h2>'


_FLOAT_STATE_JS = r"""
() => {
  const w = document.querySelector('.print-doc-figure[data-print-figure="1"]');
  const before = (el) => !!(el && w && (el.compareDocumentPosition(w) & Node.DOCUMENT_POSITION_FOLLOWING));
  const ids = ['fp1', 'fp2', 'fp3', 's1', 's2'];
  const out = {};
  for (const id of ids) out[id] = before(document.getElementById(id));
  out.blocks = w ? w.getAttribute('data-print-float-blocks') : null;
  out.floatedAttr = document.getElementById('fp1') ? document.getElementById('fp1').getAttribute('data-print-floated') : null;
  return out;
}
"""


def _float_run(html, float_figures, oversize=False):
    js, opts = build_document_decorate_js(title="T", page_height_px=994)
    opts = {**opts, "floatFigures": float_figures}

    async def steps(page):
        if oversize:
            # Mark the unit oversize after the figure pass would wrap it:
            # run once without floats, flag it, then run the float pass.
            await page.evaluate(js, {**opts, "floatFigures": {}})
            await page.evaluate("""() => document.querySelector('.print-doc-figure')
                .setAttribute('data-print-figure-oversize', '1')""")
            await page.evaluate("() => document.getElementById('print-render-content').removeAttribute('data-print-doc-decorated')")
        stats = await page.evaluate(js, opts)
        st = await page.evaluate(_FLOAT_STATE_JS)
        again = await page.evaluate(js, opts)   # idempotent: nothing moves twice
        st2 = await page.evaluate(_FLOAT_STATE_JS)
        return stats, st, again, st2

    return _run_in_page(html, steps)


def test_decorate_opts_always_carry_float_figures():
    _js, opts = build_document_decorate_js(title="T")
    assert opts["floatFigures"] == {}


@browser
def test_float_figure_moves_following_prose_into_gap():
    """d027: a trailing gap of ~140px before a page that starts with Figure 1
    takes the following same-section paragraphs (whole, 60px each) ahead of
    the figure unit: two fit (120 <= 0.95*140), the third does not."""
    stats, st, again, st2 = _float_run(_float_html(_FLOAT_PARAS), {"1": 140})
    assert stats["floated"] == 1, stats
    assert st["fp1"] and st["fp2"] and not st["fp3"], st
    assert st["s1"] and not st["s2"], st          # headings stay in place
    assert st["blocks"] == "2" and st["floatedAttr"] == "1"
    assert again["floated"] == 0 and st2 == st      # idempotent


@browser
def test_float_never_crosses_heading():
    """d018 shape: a figure followed directly by a heading moves nothing."""
    after = '<h2 id="s2">Next</h2>' + "".join(
        f'<p class="fp" id="fp{i}" style="height:60px;margin:0">P{i}</p>' for i in range(1, 4))
    stats, st, _a, _s = _float_run(_float_html(after), {"1": 400})
    assert stats["floated"] == 0, stats
    assert not st["fp1"] and not st["s2"], st


@browser
def test_float_skips_oversize_unit():
    stats, st, _a, _s = _float_run(_float_html(_FLOAT_PARAS), {"1": 140}, oversize=True)
    assert stats["floated"] == 0, stats
    assert not st["fp1"], st


# ---------------------------------------------------------------------------
# iter_009: d031 side caption for a tall narrow oversize figure (d020);
#           d030 landscape rotation of a wide small-text figure (d021).
# ---------------------------------------------------------------------------

_SIDE_CAP = ("Only point A reaches the shaper; every later stage sees the "
             "already-clamped rate, so the drop happens where the queue is, "
             "not where the policy is written. ")


def _side_html(vb_w=476, vb_h=1794, w=250, h=943, caption=_SIDE_CAP * 6):
    """Mirrors Fig 3: a renderer-downscaled top-to-bottom flowchart."""
    return f"""<body class="ziya-print-mode"><div id="print-render-content">
      <div class="print-doc-section"><div>
        <p>Intro paragraph.</p>
        <p><strong>Figure 3 \u2014 Where the clamp lands in the path</strong> (artifact <code>fig3</code>).</p>
        <div class="d3-container" data-visualization-type="mermaid">
          <svg viewBox="0 0 {vb_w} {vb_h}" style="width:{w}px;height:{h}px"><rect width="{vb_w}" height="{vb_h}"/></svg>
        </div>
        <p><em>Caption.</em> {caption}</p>
        <p>After.</p>
      </div></div></div></body>"""


_SIDE_STATE_JS = r"""
() => {
  const w = document.querySelector('.print-doc-figure');
  const svg = w && w.querySelector('svg');
  const panel = w && w.querySelector('.print-doc-figure-sidepanel');
  return {
    side: w && w.getAttribute('data-print-figure-side'),
    oversize: w && w.getAttribute('data-print-figure-oversize'),
    unitH: w ? w.getBoundingClientRect().height : null,
    panelTitle: !!(panel && panel.querySelector('.print-doc-figure-title')),
    panelCaption: !!(panel && panel.querySelector('.print-doc-caption')),
    fitReason: svg && svg.getAttribute('data-print-fit-reason'),
    scale: svg ? Number(svg.getAttribute('data-print-unit-scale')) : null,
    svgH: svg ? svg.getBoundingClientRect().height : null,
    svgRight: svg ? svg.getBoundingClientRect().right : null,
    panelLeft: panel ? panel.getBoundingClientRect().left : null,
    titleInWrap: !!(w && w.querySelector('.print-doc-figure-title')),
    captionInWrap: !!(w && w.querySelector('.print-doc-caption')),
  };
}
"""


def _side_run(html, **extra_opts):
    js, opts = build_document_decorate_js(title="T", page_height_px=994)
    opts = {**opts, **extra_opts}

    async def steps(page):
        stats = await page.evaluate(js, opts)
        st = await page.evaluate(_SIDE_STATE_JS)
        stats2 = await page.evaluate(js, opts)
        st2 = await page.evaluate(_SIDE_STATE_JS)
        return stats, st, stats2, st2

    return _run_in_page(html, steps)


@browser
def test_tall_narrow_oversize_figure_gets_side_caption():
    """d031: title + caption move beside a narrow height-limited drawing, the
    unit fits one page (not oversize) and the drawing grows (never above its
    natural size)."""
    stats, st, stats2, st2 = _side_run(_side_html())
    assert st["side"] == "1", st
    assert st["oversize"] is None, st
    # fits the page together with its 12px top margin (bottom margin is
    # truncated at the break)
    assert st["unitH"] <= 994 - 12, st
    assert st["panelTitle"] and st["panelCaption"], st
    assert st["fitReason"] == "side" and st["scale"] >= 1.0, st
    assert st["panelLeft"] >= st["svgRight"], st      # panel beside, not below
    assert stats["sideCaption"] == 1
    assert stats2["sideCaption"] == 0 and st2 == st   # idempotent


@browser
def test_side_caption_not_used_for_wide_oversize_figure():
    stats, st, _s2, _st2 = _side_run(_fig_unit_html(svg_w=500, svg_h=1100))
    assert st["side"] is None and st["oversize"] == "1", st


@browser
def test_side_caption_reverts_when_panel_too_tall():
    stats, st, _s2, _st2 = _side_run(_side_html(caption=_SIDE_CAP * 60))
    assert st["side"] is None, st
    assert st["oversize"] == "1", st
    assert st["titleInWrap"] and st["captionInWrap"], st
    assert st["fitReason"] != "side", st


@browser
def test_side_caption_opt_out():
    stats, st, _s2, _st2 = _side_run(_side_html(), sideCaption=False)
    assert st["side"] is None and st["oversize"] == "1", st


def _wide_html(font=18):
    labels = "".join(
        f'<text x="{40 + i * 240}" y="{120 + (i % 3) * 200}" font-size="{font}">Label {i}</text>'
        for i in range(7))
    return f"""<body class="ziya-print-mode"><div id="print-render-content">
      <div class="print-doc-section"><div>
        <p>Intro paragraph.</p>
        <p><strong>Figure 7 \u2014 Path to GA</strong> (artifact <code>fig7</code>).</p>
        <div class="d3-container" data-visualization-type="mermaid">
          <svg viewBox="0 0 1790 800" style="width:1790px;height:800px"><rect width="1790" height="800" fill="#eee"/>{labels}</svg>
        </div>
        <p><em>Caption.</em> Seven phases; each box is one milestone.</p>
        <p>After.</p>
      </div></div></div></body>"""


_LAND_STATE_JS = r"""
() => {
  const L = document.querySelector('.print-doc-figure-landscape');
  const w = document.querySelector('.print-doc-figure');
  const svg = w && w.querySelector('svg');
  let minPx = Infinity;
  for (const t of (svg ? svg.querySelectorAll('text') : [])) {
    const m = t.getScreenCTM();
    minPx = Math.min(minPx, parseFloat(getComputedStyle(t).fontSize) * Math.hypot(m.a, m.b));
  }
  return {
    landscape: L && L.getAttribute('data-print-figure-landscape'),
    outerH: L ? L.getBoundingClientRect().height : null,
    outerW: L ? L.getBoundingClientRect().width : null,
    transform: w ? (w.style.transform || '') : '',
    minPx,
    titleInside: !!(L && L.querySelector('.print-doc-figure-title')),
    captionInside: !!(L && L.querySelector('.print-doc-caption')),
    gain: L && Number(L.getAttribute('data-print-landscape-gain')),
    colW: document.querySelector('.print-doc-section > div').getBoundingClientRect().width,
  };
}
"""


def _land_run(html, **extra_opts):
    js, opts = build_document_decorate_js(title="T", page_height_px=994)
    opts = {**opts, **extra_opts}

    async def steps(page):
        stats = await page.evaluate(js, opts)
        st = await page.evaluate(_LAND_STATE_JS)
        stats2 = await page.evaluate(js, opts)
        st2 = await page.evaluate(_LAND_STATE_JS)
        return stats, st, stats2, st2

    return _run_in_page(html, steps)


@browser
def test_wide_small_text_figure_rotated_to_landscape():
    """d030: a width-limited drawing whose text prints < 6.5pt is rotated onto
    its own page when the page height brings it to >= 6.5pt; title and caption
    rotate with it."""
    stats, st, stats2, st2 = _land_run(_wide_html(18))
    assert st["landscape"] == "1", st
    assert st["outerH"] <= 994 * 0.97 + 1, st
    assert st["outerW"] <= st["colW"] + 1, st
    assert "rotate(-90deg)" in st["transform"], st
    assert st["minPx"] >= 8.67, st
    assert st["titleInside"] and st["captionInside"], st
    assert stats["landscape"] == 1
    assert stats2["landscape"] == 0 and st2 == st      # idempotent


@browser
def test_wide_figure_with_legible_text_not_rotated():
    stats, st, _s2, _st2 = _land_run(_wide_html(26))
    assert st["landscape"] is None, st


@browser
def test_rotation_skipped_when_gain_insufficient():
    stats, st, _s2, _st2 = _land_run(_wide_html(4))
    assert st["landscape"] is None, st


@browser
def test_landscape_opt_out():
    stats, st, _s2, _st2 = _land_run(_wide_html(18), landscapeFigures=False)
    assert st["landscape"] is None, st


def test_decorate_opts_carry_landscape_and_side_flags():
    _js, opts = build_document_decorate_js(title="T")
    assert opts["landscapeFigures"] is True
    assert opts["sideCaption"] is True
