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
    # A4 210x297mm minus 16/16 horizontal and 16/18 vertical at 96dpi.
    assert w == round((210 - 32) * 96 / 25.4)
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
    <p>Text.</p>
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
