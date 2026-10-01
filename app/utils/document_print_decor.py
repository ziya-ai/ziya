"""
Document print decoration — the aesthetic layer for authored-document PDFs.

``app/services/pdf_exporter.export_document_pdf`` renders an IR document
(see ``app/utils/document_ir.py``) through the shared ``/print`` route.  That
route paints the chat UI's screen styling, which is right for a transcript
but reads like a screen dump when the artifact is a report.  This module
holds everything that turns the rendered document into a typeset one, and
nothing that the conversation export uses:

* :data:`DOCUMENT_PRINT_CSS` — a stylesheet the driver injects with
  ``page.add_style_tag`` before capture: a real heading scale under the
  title, a title block with a rule, report tables (header rule, hairline
  rows, zebra), centred figures with captions, tighter code blocks.
* :func:`build_document_decorate_js` — a browser-side pass (run via
  ``page.evaluate`` BEFORE the keep-with-next and outline passes, so all
  captures share one pagination) that numbers figures and tables and attaches
  their captions, optionally numbers sections, lets page-tall tables flow
  across pages with a repeating header instead of stranding a blank band, and
  reconciles the title block (subtitle, formatted date, title-page layout).
* :func:`build_document_footer_template` — a running footer that names the
  DOCUMENT (title · author, page N of M) rather than the export tool; the
  tool credit shrinks to a second, lighter line.
* :func:`format_document_date` — locale-independent long date for the title
  block and footer.
* :func:`page_content_box_px` — the printable box for the current margins, so
  the driver can size the viewport to the PDF layout and the in-page
  measurements (figure fit, keep-with-next, table flow) match the pages
  Chromium actually paginates.

Caption conventions the decoration pass recognises in the IR body:

    ```mermaid
    ...
    ```
    Figure: Request path from client to store.     <- caption BELOW a figure

    Table: Measured queue depth by utilization.     <- caption ABOVE (or below)
    | a | b |
    |---|---|

A figure or table with no caption line still receives a numbered label
("Figure 3", "Table 2") when numbering is on, so the prose can refer to it.
Everything here is best-effort and idempotent: a second run is a no-op, and
any failure inside the browser pass is contained per element.
"""
from __future__ import annotations

import datetime as _dt
import html as _html
from typing import Any, Dict, Optional, Tuple

# A4 in millimetres; CSS reference pixel density.
_A4_W_MM = 210.0
_A4_H_MM = 297.0
_MM_TO_PX = 96.0 / 25.4

# Margins ``export_document_pdf`` uses when the front-matter sets none.  The
# 19mm sides give a 60-95 character measure at the 11pt body (R9, d026); 16mm
# sides measured a 95-98 character median.  Independent of the conversation
# export's margins (pdf_exporter.capture_pdf), which are unchanged.
DEFAULT_DOCUMENT_MARGIN: Dict[str, str] = {
    "top": "16mm", "bottom": "18mm", "left": "19mm", "right": "19mm",
}

# Tables taller than this fraction of the printable page height are allowed to
# break across pages (with a repeating header row) instead of being held
# atomic — holding a page-tall table whole forces it onto a fresh page and
# leaves the preceding page mostly blank.
LONG_TABLE_PAGE_FRACTION = 0.45

# Default numbering knobs (front-matter ``numbering:`` overrides).
DEFAULT_NUMBERING: Dict[str, bool] = {
    "sections": False,
    "figures": True,
    "tables": True,
}


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def _css_length_to_px(value: Optional[str]) -> Optional[float]:
    """Convert a CSS length accepted by ``page.pdf()`` (mm/cm/in/px) to px."""
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    for unit, factor in (("mm", _MM_TO_PX), ("cm", 10 * _MM_TO_PX),
                         ("in", 96.0), ("px", 1.0)):
        if v.endswith(unit):
            try:
                return float(v[: -len(unit)]) * factor
            except ValueError:
                return None
    return None


def page_content_box_px(margin: Optional[Dict[str, str]]) -> Tuple[int, int]:
    """Return ``(width_px, height_px)`` of the A4 printable box for ``margin``.

    The /print page lays out at whatever viewport it is given while Chromium
    paginates ``page.pdf()`` at the PAPER width, so any in-page measurement
    (how tall a paragraph is, whether a figure fits) is only accurate when the
    viewport width equals this content width.  Missing or unparseable sides
    fall back to :data:`DEFAULT_DOCUMENT_MARGIN`.
    """
    m = dict(DEFAULT_DOCUMENT_MARGIN)
    if isinstance(margin, dict):
        m.update({k: v for k, v in margin.items() if isinstance(v, str)})
    side = {k: (_css_length_to_px(m.get(k))
                or _css_length_to_px(DEFAULT_DOCUMENT_MARGIN[k]) or 0.0)
            for k in ("top", "bottom", "left", "right")}
    width = _A4_W_MM * _MM_TO_PX - side["left"] - side["right"]
    height = _A4_H_MM * _MM_TO_PX - side["top"] - side["bottom"]
    return int(round(width)), int(round(height))


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

def format_document_date(value: Any = None) -> str:
    """Long, locale-independent date for the title block (``September 19, 2026``).

    Accepts a ``date``/``datetime`` (PyYAML parses an unquoted ``2026-09-19``
    into one), an ISO ``YYYY-MM-DD`` string, or free text (returned verbatim
    so an author can write ``Q3 FY26`` or ``Draft — 19 Sep``).  ``None`` means
    today.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        value = _dt.date.today()
    if isinstance(value, _dt.datetime):
        value = value.date()
    if isinstance(value, _dt.date):
        return f"{value.strftime('%B')} {value.day}, {value.year}"
    text = str(value).strip()
    try:
        parsed = _dt.date.fromisoformat(text[:10])
        if len(text) == 10:
            return format_document_date(parsed)
    except ValueError:
        pass
    return text


# ---------------------------------------------------------------------------
# Numbering knobs
# ---------------------------------------------------------------------------

def normalize_numbering(raw: Any) -> Dict[str, bool]:
    """Normalise a front-matter ``numbering`` value into the three knobs.

    ``true``/``false`` set every knob; a mapping sets individual knobs and
    leaves the rest at :data:`DEFAULT_NUMBERING`.  Anything else yields the
    defaults.
    """
    out = dict(DEFAULT_NUMBERING)
    if isinstance(raw, bool):
        return {k: raw for k in out}
    if isinstance(raw, dict):
        for k in out:
            v = raw.get(k)
            if isinstance(v, bool):
                out[k] = v
    return out


# ---------------------------------------------------------------------------
# Stylesheet (injected via page.add_style_tag; document mode only)
# ---------------------------------------------------------------------------
#
# Scoped to body.ziya-print-document, a class the decoration pass adds, so an
# accidental injection into a transcript render changes nothing.  Print media
# is NOT emulated during capture (see styles/print.css), so these are plain
# rules, and fragmentation properties are honoured by page.pdf() as usual.

DOCUMENT_PRINT_CSS = r"""
/* ── Page & body ─────────────────────────────────────────────────────────── */
body.ziya-print-document #print-render-content {
  padding: 0 !important;
  font-size: 11pt;
  line-height: 1.45;
  color: #1f2328;
}
body.ziya-print-document #print-render-content p {
  margin: 0 0 9px;
}
body.ziya-print-document #print-render-content ul,
body.ziya-print-document #print-render-content ol {
  margin: 0 0 10px;
  padding-left: 1.7em;
}
body.ziya-print-document #print-render-content li {
  margin: 2px 0;
}
body.ziya-print-document #print-render-content li > p {
  margin-bottom: 4px;
}
body.ziya-print-document #print-render-content a {
  color: #0b3d91;
  text-decoration: none;
}
body.ziya-print-document #print-render-content hr {
  border: 0;
  border-top: 1px solid #d0d7de;
  margin: 18px 0;
}
body.ziya-print-document #print-render-content strong {
  font-weight: 650;
}

/* ── Title block ─────────────────────────────────────────────────────────── */
body.ziya-print-document .print-doc-titleblock {
  margin: 0 0 30px !important;
  padding-bottom: 14px;
  border-bottom: 2px solid #1f2328;
  break-after: avoid;
}
body.ziya-print-document .print-doc-titleblock h1 {
  font-size: 26pt !important;
  line-height: 1.15;
  font-weight: 700;
  letter-spacing: -0.02em;
  margin: 0 0 6px !important;
  border: 0 !important;
  padding: 0 !important;
}
body.ziya-print-document .print-doc-subtitle {
  font-size: 13pt;
  line-height: 1.3;
  color: #57606a;
  margin: 0 0 12px;
}
body.ziya-print-document .print-doc-byline {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 18px;
  font-size: 9.5pt !important;
  color: #57606a !important;
}
body.ziya-print-document .print-doc-byline .print-doc-author {
  font-weight: 600;
  color: #1f2328 !important;
  font-size: inherit !important;
}
body.ziya-print-document .print-doc-byline .print-doc-date {
  font-size: inherit !important;
  color: inherit !important;
}
/* layout: titlepage — the title block owns the first page. */
body.ziya-print-document .print-doc-titleblock.print-doc-titlepage {
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-height: 62vh;
  border-bottom: 0;
  padding-top: 18vh;
  break-after: page;
  page-break-after: always;
}
body.ziya-print-document .print-doc-titleblock.print-doc-titlepage h1 {
  font-size: 32pt !important;
  margin-bottom: 12px !important;
}
body.ziya-print-document .print-doc-titleblock.print-doc-titlepage .print-doc-subtitle {
  font-size: 15pt;
  margin-bottom: 28px;
}
body.ziya-print-document .print-doc-titleblock.print-doc-titlepage .print-doc-byline {
  border-top: 1px solid #1f2328;
  padding-top: 12px;
  width: 60%;
}

/* ── Heading scale (body headings sit BELOW the title) ───────────────────── */
body.ziya-print-document .print-doc-section h1 {
  font-size: 17pt !important;
  line-height: 1.25;
  margin: 26px 0 10px !important;
  padding-bottom: 5px;
  border-bottom: 1px solid #d0d7de;
}
body.ziya-print-document .print-doc-section h2 {
  font-size: 13.5pt !important;
  line-height: 1.3;
  margin: 22px 0 8px !important;
}
body.ziya-print-document .print-doc-section h3 {
  font-size: 11.5pt !important;
  line-height: 1.3;
  margin: 16px 0 6px !important;
}
body.ziya-print-document .print-doc-section h4 {
  font-size: 10pt !important;
  margin: 14px 0 4px !important;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #57606a;
}
body.ziya-print-document .print-doc-section h5,
body.ziya-print-document .print-doc-section h6 {
  font-size: 10pt !important;
  margin: 12px 0 4px !important;
}
/* The first heading of a section (or of the document) carries no top gap —
   also when the keep-with-next pass has wrapped it in its binding div. */
body.ziya-print-document .print-doc-section > div > :is(h1, h2, h3):first-child,
body.ziya-print-document .print-doc-section > :is(h1, h2, h3):first-child,
body.ziya-print-document .print-doc-section > div > [data-print-keep-with-next]:first-child > :is(h1, h2, h3):first-child,
body.ziya-print-document .print-doc-section > [data-print-keep-with-next]:first-child > :is(h1, h2, h3):first-child {
  margin-top: 0 !important;
}
body.ziya-print-document .print-doc-secnum {
  display: inline-block;
  min-width: 1.6em;
  margin-right: 0.2em;
  white-space: pre;
  color: #57606a;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

/* ── Figures ─────────────────────────────────────────────────────────────── */
body.ziya-print-document .print-doc-figure {
  margin: 12px 0 18px;
  text-align: center;
  break-inside: avoid;
  page-break-inside: avoid;
}
body.ziya-print-document .print-doc-figure > .d3-container,
body.ziya-print-document .print-doc-figure > p,
body.ziya-print-document .print-doc-figure > figure,
body.ziya-print-document .print-doc-figure-head > .d3-container,
body.ziya-print-document .print-doc-figure-head > figure {
  margin: 0 auto !important;
}
/* Oversize unit: title + drawing share one unbreakable box (the unit itself
   may break, but only before the caption). */
body.ziya-print-document .print-doc-figure > .print-doc-figure-head {
  break-inside: avoid;
  page-break-inside: avoid;
}
/* The chat renderer reserves room around a diagram (wrapper padding, an
   inline min-height sized to the screen layout, a hover toolbar).  None of
   that belongs on paper: collapse the box to the drawing. */
body.ziya-print-document .print-doc-figure .d3-container,
body.ziya-print-document .print-doc-figure .mermaid-container,
body.ziya-print-document .print-doc-figure .mermaid-wrapper,
body.ziya-print-document .print-doc-figure [class$="-wrapper"] {
  /* `height` too: D3Renderer's resize observer re-measures after the box
     collapses and writes `height: Npx` back inline (non-important), so
     the stylesheet — not the decoration pass — must own these. */
  height: auto !important;
  max-height: none !important;
  min-height: 0 !important;
  padding: 0 !important;
  margin: 0 auto !important;
  justify-content: center;
  background: transparent !important;
  border: 0 !important;
  box-shadow: none !important;
}
body.ziya-print-document .print-doc-figure .diagram-actions,
body.ziya-print-document .print-doc-figure .diagram-action-button,
body.ziya-print-document .print-doc-figure button {
  display: none !important;
}
/* Never wider than the column.  Inline `max-width:none` on a renderer's
   <svg> is non-important, so this wins; up-scaling past the drawing's
   natural size is undone by the decoration pass (see _DECORATE_JS_BODY). */
body.ziya-print-document .print-doc-figure svg,
body.ziya-print-document .print-doc-figure img {
  max-width: 100% !important;
  height: auto;
}
body.ziya-print-document .print-doc-caption {
  margin: 8px auto 0;
  max-width: 88%;
  font-size: 9pt;
  line-height: 1.4;
  color: #57606a;
  text-align: center;
}
body.ziya-print-document .print-doc-caption-label {
  font-weight: 650;
  color: #1f2328;
}
/* A multi-line caption (> ~2 lines) reads badly centred at 88%: full
   measure, ragged-right like the body. */
body.ziya-print-document .print-doc-caption.print-doc-caption-long {
  max-width: 100%;
  text-align: left;
}
/* d031: side caption — title + caption beside a tall narrow drawing. */
body.ziya-print-document .print-doc-figure.print-doc-figure-side {
  display: grid;
  column-gap: 14px;
  align-items: start;
  text-align: left;
}
body.ziya-print-document .print-doc-figure-sidepanel > .print-doc-figure-title {
  margin: 0 0 6px !important;
  font-size: 9.5pt;
  line-height: 1.35;
  text-align: left;
  color: #1f2328;
}
body.ziya-print-document .print-doc-figure-sidepanel > .print-doc-caption {
  margin: 0;
  max-width: 100%;
  text-align: left;
}
/* d030: a rotated (landscape) figure unit on its own page. */
body.ziya-print-document .print-doc-figure-landscape {
  position: relative;
  margin: 0 0 9px;
  break-before: page;
  page-break-before: always;
  break-inside: avoid;
  page-break-inside: avoid;
}
body.ziya-print-document .print-doc-figure-landscape > .print-doc-figure {
  margin: 0 !important;
}
/* The author's "Figure N — Title" paragraph, adopted as the unit's first
   child so it always sits on the same page as its drawing. */
body.ziya-print-document .print-doc-figure > .print-doc-figure-title,
body.ziya-print-document .print-doc-figure-head > .print-doc-figure-title {
  margin: 0 0 6px !important;
  font-size: 9.5pt;
  line-height: 1.35;
  text-align: left;
  color: #1f2328;
  break-after: avoid;
  page-break-after: avoid;
}

/* ── Tables (report style: header rule, hairline rows, zebra) ────────────── */
body.ziya-print-document #print-render-content table:not(.diff-table) {
  width: 100%;
  border-collapse: collapse;
  margin: 10px 0 18px;
  font-size: 9.5pt;
  line-height: 1.35;
  border: 0 !important;
  font-variant-numeric: lining-nums tabular-nums;
}
body.ziya-print-document #print-render-content table:not(.diff-table) caption {
  caption-side: top;
  text-align: left;
  padding: 0 0 6px;
  font-size: 9pt;
  color: #57606a;
}
body.ziya-print-document #print-render-content table:not(.diff-table) caption .print-doc-caption-label {
  font-weight: 650;
  color: #1f2328;
}
body.ziya-print-document #print-render-content table:not(.diff-table) th {
  background: transparent !important;
  border: 0 !important;
  border-bottom: 1.5px solid #1f2328 !important;
  padding: 6px 8px !important;
  font-weight: 650;
  color: #1f2328;
  vertical-align: bottom;
}
body.ziya-print-document #print-render-content table:not(.diff-table) td {
  border: 0 !important;
  border-bottom: 1px solid #e3e6ea !important;
  padding: 5px 8px !important;
  vertical-align: top;
}
body.ziya-print-document #print-render-content table:not(.diff-table) tbody tr:nth-child(even) td {
  background: #f6f8fa !important;
}
body.ziya-print-document #print-render-content table:not(.diff-table) tbody tr:last-child td {
  border-bottom: 1.5px solid #1f2328 !important;
}
/* A page-tall table flows across pages with its header repeated on each
   (thead as table-header-group is what Chromium repeats), and rows stay
   whole.  Applied by the decoration pass only to tables that would
   otherwise strand a blank band. */
body.ziya-print-document table.print-doc-table-flow {
  break-inside: auto !important;
  page-break-inside: auto !important;
}
body.ziya-print-document table.print-doc-table-flow thead {
  display: table-header-group;
}
body.ziya-print-document table.print-doc-table-flow tr {
  break-inside: avoid;
  page-break-inside: avoid;
}
/* Column-balanced tables (decoration pass, balanceColumns): widths are fixed
   by a <colgroup> the pass computed to minimise the table's row heights; a
   long token may hyphenate/break rather than overflow its (now fixed) cell. */
body.ziya-print-document #print-render-content table[data-print-colbalance] td,
body.ziya-print-document #print-render-content table[data-print-colbalance] th {
  hyphens: auto;
  -webkit-hyphens: auto;
  overflow-wrap: break-word;
}

/* ── Code, quotes, math ──────────────────────────────────────────────────── */
body.ziya-print-document #print-render-content pre {
  font-size: 8.6pt !important;
  line-height: 1.45;
  margin: 8px 0 14px;
  padding: 10px 12px !important;
  border: 1px solid #e1e4e8 !important;
  border-radius: 4px;
  background: #f8f9fb !important;
}
body.ziya-print-document #print-render-content pre code {
  font-size: inherit !important;
}
body.ziya-print-document #print-render-content :not(pre) > code {
  font-size: 0.92em;
  padding: 0.05em 0.3em;
  background: #f3f4f6;
  border-radius: 3px;
}
body.ziya-print-document #print-render-content blockquote {
  margin: 10px 0 12px;
  padding: 2px 0 2px 14px;
  border-left: 3px solid #c8ccd1;
  border-radius: 0;
  background: transparent !important;
  color: #57606a;
}
/* Reviewer / editorial notes and TODOs (decoration pass tags blockquotes
   whose lead is "Reviewer note…", "Note:", "TODO…"): an amber call-out,
   distinct from a body quotation (R10).  Plain quotes keep the rule above. */
body.ziya-print-document #print-render-content blockquote.print-doc-note {
  /* !important throughout: the /print markdown renderer sets an inline
     style (grey 4px bar, grey text, padding) on every blockquote. */
  background: #fff8e1 !important;
  border-left: 3px solid #d4a017 !important;
  border-radius: 0 3px 3px 0 !important;
  color: #3d3a2f !important;
  padding: 6px 10px 6px 12px !important;
  font-size: 9pt;
  break-inside: avoid;
  page-break-inside: avoid;
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}
body.ziya-print-document #print-render-content blockquote.print-doc-note > :first-child {
  margin-top: 0;
}
body.ziya-print-document #print-render-content blockquote.print-doc-note > :last-child {
  margin-bottom: 0;
}
body.ziya-print-document #print-render-content blockquote.print-doc-note > :first-child > strong:first-child,
body.ziya-print-document #print-render-content blockquote.print-doc-note > :first-child > b:first-child {
  color: #7a5b00;
}
body.ziya-print-document #print-render-content .math-display,
body.ziya-print-document #print-render-content .katex-display {
  margin: 10px 0 14px;
}
"""


# Runt-last-page squeeze (doc-aesthetics d014).  When the first capture ends
# on a near-empty page, export_document_pdf recaptures with one of these
# appended to DOCUMENT_PRINT_CSS and keeps the result only if it has FEWER
# pages.  Step 1 trims vertical spacing (~25 %); step 2 adds a slightly
# tighter leading on top.  Neither step touches font-size: the 11pt body is
# the R3 ceiling the measure guard relies on, and leading never drops below
# 1.40.  Every rule is scoped to body.ziya-print-document.
_SQUEEZE_SPACING_CSS = r"""
body.ziya-print-document #print-render-content p { margin-bottom: 6px; }
body.ziya-print-document #print-render-content ul,
body.ziya-print-document #print-render-content ol { margin-bottom: 7px; }
body.ziya-print-document #print-render-content li { margin: 1px 0; }
body.ziya-print-document #print-render-content li > p { margin-bottom: 2px; }
body.ziya-print-document .print-doc-figure { margin: 9px 0 13px; }
body.ziya-print-document .print-doc-caption { margin-top: 6px; }
"""
_SQUEEZE_LEADING_CSS = r"""
body.ziya-print-document #print-render-content { line-height: 1.40; }
"""
# Step 3 (d014, iter_007): spacing trim plus "let medium tables flow".  A
# table below LONG_TABLE_PAGE_FRACTION is otherwise held atomic, and together
# with its keep-with-next heading it is pushed whole onto the next page,
# stranding a blank band and spilling the tail onto a runt page.  In this
# step any table with >= 4 body rows may break between rows with its header
# repeated; the first two rows and the last row are bound to their
# neighbours so a break never strands header + 1 row or a lone row.  No
# leading change (it measured no gain at the tail).  CSS only; comments in
# the rules below avoid commas so selector parsing in tests stays simple.
_TBL4 = (
    "body.ziya-print-document #print-render-content "
    "table[data-print-table]:has(> tbody > tr:nth-child(4))"
)
_SQUEEZE_TABLE_FLOW_CSS = (
    "\n/* d014 step 3: a table of 4+ body rows may break between rows */\n"
    + _TBL4 + " {\n  break-inside: auto !important;\n"
    "  page-break-inside: auto !important;\n}\n"
    + _TBL4 + " > thead {\n  display: table-header-group;\n}\n"
    + _TBL4 + " tr {\n  break-inside: avoid;\n  page-break-inside: avoid;\n}\n"
    "/* never strand header + 1 row at a page bottom nor 1 row on the continuation */\n"
    + _TBL4 + " > tbody > tr:nth-child(-n+2),\n"
    + _TBL4 + " > tbody > tr:last-child {\n"
    "  break-before: avoid;\n  page-break-before: avoid;\n}\n"
)
DOCUMENT_SQUEEZE_CSS_STEPS: Tuple[str, ...] = (
    _SQUEEZE_SPACING_CSS,
    _SQUEEZE_SPACING_CSS + _SQUEEZE_LEADING_CSS,
    _SQUEEZE_SPACING_CSS + _SQUEEZE_TABLE_FLOW_CSS,
)


# ---------------------------------------------------------------------------
# Browser-side decoration pass
# ---------------------------------------------------------------------------
#
# A plain JS string; its only input is the JSON options object the driver
# passes, so no untrusted document text is ever interpolated into code.

_DECORATE_JS_BODY = r"""
(opts) => {
  const root = document.getElementById('print-render-content')
             || document.getElementById('print-render-root');
  const stats = { sections: 0, figures: 0, tables: 0, flowTables: 0,
                  captions: 0, titleblock: false };
  if (!root) return stats;
  document.body.classList.add('ziya-print-document');
  const numbering = opts.numbering || {};
  const pageH = Number(opts.pageHeightPx) || 1016;
  const longFrac = Number(opts.longTableFraction) || 0.45;
  const esc = (s) => String(s).replace(/[<>&]/g,
      (c) => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));

  // ── 0. Title block reconciliation (idempotent) ───────────────────────
  // The React page renders title/author/date; this fills in what an older
  // bundle lacks (subtitle, formatted date, title-page layout) so the
  // driver-side aesthetics do not depend on a frontend rebuild.
  try {
    let tb = root.querySelector('.print-doc-titleblock');
    const wantsBlock = opts.layout === 'report' || opts.layout === 'titlepage';
    if (!tb && wantsBlock && (opts.title || opts.author)) {
      tb = document.createElement('header');
      tb.className = 'print-doc-titleblock';
      if (opts.title) {
        const h = document.createElement('h1');
        h.textContent = opts.title;
        tb.appendChild(h);
      }
      root.insertBefore(tb, root.firstChild);
    }
    if (tb) {
      stats.titleblock = true;
      if (opts.layout === 'titlepage') tb.classList.add('print-doc-titlepage');
      let sub = tb.querySelector('.print-doc-subtitle');
      if (opts.subtitle && !sub) {
        sub = document.createElement('div');
        sub.className = 'print-doc-subtitle';
        sub.textContent = opts.subtitle;
        const h1 = tb.querySelector('h1');
        if (h1 && h1.nextSibling) tb.insertBefore(sub, h1.nextSibling);
        else tb.appendChild(sub);
      }
      let by = tb.querySelector('.print-doc-byline');
      if (!by) {
        by = document.createElement('div');
        by.className = 'print-doc-byline';
        const author = tb.querySelector('.print-doc-author');
        const date = tb.querySelector('.print-doc-date');
        if (author) by.appendChild(author);
        if (date) by.appendChild(date);
        tb.appendChild(by);
      }
      if (opts.author && !by.querySelector('.print-doc-author')) {
        const a = document.createElement('span');
        a.className = 'print-doc-author';
        a.textContent = opts.author;
        by.insertBefore(a, by.firstChild);
      }
      const dateEl = by.querySelector('.print-doc-date');
      if (opts.date) {
        if (dateEl) dateEl.textContent = opts.date;
        else {
          const d = document.createElement('span');
          d.className = 'print-doc-date';
          d.textContent = opts.date;
          by.appendChild(d);
        }
      }
    }
  } catch (e) { /* title block is cosmetic; never abort the pass */ }

  // ── 1. Section numbering (before outline sentinels read heading text) ─
  if (numbering.sections) {
    const counters = [0, 0, 0];
    const heads = Array.from(root.querySelectorAll('.print-doc-section h1, .print-doc-section h2, .print-doc-section h3'));
    for (const h of heads) {
      if (h.querySelector('.print-doc-secnum')) continue;
      const level = parseInt(h.tagName.slice(1), 10) - 1;
      counters[level] += 1;
      for (let i = level + 1; i < counters.length; i++) counters[i] = 0;
      const label = counters.slice(0, level + 1).join('.');
      const span = document.createElement('span');
      span.className = 'print-doc-secnum';
      // Trailing space so textContent (which feeds the PDF outline labels)
      // reads "1.2 Request path", not "1.2Request path".
      span.textContent = label + ' ';
      h.insertBefore(span, h.firstChild);
      h.setAttribute('data-print-secnum', label);
      stats.sections += 1;
    }
  }

  // ── 1b. fitMeasure: keep body lines <= 94 characters (d009, R9) ──────
  // The column width is the author's (front-matter margins), so the body
  // size is the only lever.  Sample up to 40 prose paragraphs (not in
  // quotes/tables/figures/lists/title block) of >= 160 characters; each
  // paragraph's measure = its column width / its mean glyph advance (the
  // text set on one unwrapped line in a hidden probe span that inherits the
  // paragraph's font), less half a mean word for the break at a word
  // boundary.  This is continuous in the font size, unlike a line count.  If the median exceeds MEASURE_MAX, step the content size up by
  // 0.25pt, re-measuring each step, to at most BODY_PT_CAP = 11pt — the
  // R3 ceiling for body text (an 11.5pt cap was tried in iter_005 and put
  // body = h3 = 11.5pt, failing R3).  So it lifts a body set smaller than
  // the 11pt default (an inline/renderer size) and otherwise only reports
  // the measure; a column too wide at 11pt is an L1 margin choice.  Never lowers
  // the size, so a re-run is a no-op.  Headings, tables, captions and code
  // set their own sizes and are unaffected.  The probe span is removed.
  const MEASURE_MAX = 94, BODY_PT_CAP = 11;
  const fitMeasure = () => {
    const skip = 'blockquote, table, figure, .print-doc-figure, li, pre, '
               + '.print-doc-titleblock, .d3-container';
    const paras = [];
    for (const p of root.querySelectorAll('p')) {
      if (paras.length >= 40) break;
      if (p.closest(skip)) continue;
      const txt = p.textContent.replace(/\s+/g, ' ').trim();
      if (txt.length < 160 || p.clientWidth < 100) continue;
      paras.push([p, txt]);
    }
    const measure = () => {
      const per = [];
      for (const [p, txt] of paras) {
        const probe = document.createElement('span');
        probe.setAttribute('data-print-measure-probe', '');
        probe.style.cssText = 'position:absolute;visibility:hidden;'
                            + 'white-space:nowrap;left:0;top:0;';
        probe.textContent = txt;
        p.appendChild(probe);
        const w = probe.getBoundingClientRect().width;
        probe.remove();
        const cs = getComputedStyle(p);
        const col = p.clientWidth - (parseFloat(cs.paddingLeft) || 0)
                                  - (parseFloat(cs.paddingRight) || 0);
        // A wrapped line ends at a word boundary, losing on average half a
        // word (+ its space) against the unwrapped estimate.
        const words = txt.split(' ').length;
        const halfWord = 0.5 * (txt.length / Math.max(1, words));
        if (w > 0 && col > 0) per.push(col / (w / txt.length) - halfWord);
      }
      if (!per.length) return null;
      per.sort((x, y) => x - y);
      const m = per.length >> 1;
      return per.length % 2 ? per[m] : (per[m - 1] + per[m]) / 2;
    };
    let pt = parseFloat(getComputedStyle(root).fontSize) * 0.75;
    let chars = measure();
    while (chars !== null && chars > MEASURE_MAX && pt + 0.25 <= BODY_PT_CAP + 1e-6) {
      pt = Math.round((pt + 0.25) * 100) / 100;
      root.style.setProperty('font-size', pt + 'pt', 'important');
      const next = measure();
      if (next === null || next >= chars) { chars = next; break; }
      chars = next;
    }
    stats.bodyPt = Math.round(pt * 100) / 100;
    stats.measureChars = chars === null ? null : Math.round(chars * 10) / 10;
  };
  fitMeasure();

  // ── 2. Figures: wrap + number + caption ──────────────────────────────
  const CAPTION_FIG = /^\s*(?:figure|fig\.?)\s*[:.\u2014-]\s*/i;
  const CAPTION_TBL = /^\s*table\s*[:.\u2014-]\s*/i;
  // An authored figure TITLE above the drawing: "Figure 3 — …", "Fig. 2: …".
  // The number must be followed by a separator (or end), so prose such as
  // "Figure 3 shows that …" is never adopted.
  const FIG_TITLE = /^\s*(?:figure|fig\.?)\s*(\d+[a-z]?)\s*(?:[:.\u2014\u2013-]|$)/i;
  // "*Caption.* …" / "Caption: …" below the drawing.
  const CAPTION_LEAD = /^\s*caption\s*[:.]\s*/i;
  const CAPTION_LEAD_WORD = /^\s*caption\s*[:.]?\s*$/i;
  const takeCaption = (el, re) => {
    if (!el || el.tagName !== 'P') return null;
    const text = (el.textContent || '').trim();
    if (!re.test(text)) return null;
    // Strip the prefix from the markup; the prefix is plain text at the
    // start of the paragraph so a leading-text replace is safe.
    const html = el.innerHTML.replace(re, '');
    el.remove();
    return html;
  };
  // A caption paragraph led by the word "Caption." — usually emphasised
  // (<em>Caption.</em> …), so the lead is removed as a DOM NODE, not by a
  // regex over innerHTML (which starts with the tag).
  const takeLeadCaption = (el) => {
    if (!el || el.tagName !== 'P') return null;
    if (!CAPTION_LEAD.test(el.textContent || '')) return null;
    const first = el.firstChild;
    if (first && first.nodeType === 1 && /^(EM|STRONG|B|I)$/.test(first.tagName)
        && CAPTION_LEAD_WORD.test(first.textContent || '')) {
      first.remove();
    } else if (first && first.nodeType === 3 && CAPTION_LEAD.test(first.nodeValue || '')) {
      first.nodeValue = first.nodeValue.replace(CAPTION_LEAD, '');
    } else {
      return null;
    }
    const lead = el.firstChild;
    if (lead && lead.nodeType === 3) lead.nodeValue = lead.nodeValue.replace(/^\s+/, '');
    const html = el.innerHTML;
    el.remove();
    return html;
  };
  const takeTitle = (el) => {
    if (!el || el.tagName !== 'P') return null;
    const text = (el.textContent || '').trim();
    if (text.length > 400 || el.querySelector('img, svg')) return null;
    const m = FIG_TITLE.exec(text);
    if (!m) return null;
    el.remove();
    el.classList.add('print-doc-figure-title');
    return { el, num: m[1] };
  };
  // The drawing's scalable element: the largest <svg>/<img> that is not
  // renderer chrome.
  const drawingOf = (fig) => {
    let best = null, area = 0;
    for (const el of Array.from(fig.querySelectorAll('svg, img'))) {
      if (el.closest('.diagram-actions, button')) continue;
      if (el.parentElement && el.parentElement.closest('svg')) continue;
      const r = el.getBoundingClientRect();
      if (r.width * r.height > area) { area = r.width * r.height; best = el; }
    }
    return best;
  };
  const scaleDrawing = (el, s, reason) => {
    const r = el.getBoundingClientRect();
    el.style.setProperty('width', Math.round(r.width * s) + 'px', 'important');
    el.style.setProperty('height', Math.round(r.height * s) + 'px', 'important');
    el.style.setProperty('max-width', '100%', 'important');
    el.setAttribute('data-print-fit-reason', reason);
    el.setAttribute('data-print-unit-scale', String(Math.round(s * 1000) / 1000));
  };
  const UNIT_FRAC = 0.97, MIN_UNIT_SCALE = 0.90;
  // ── Fit SVG content inside its drawing (d004 / d003) ──────────────────
  // Chart text wider than the viewBox spills past the <svg> box (vega sets
  // overflow: visible) and is then cut by the PAGE box; a down-scaled
  // drawing with dead margins prints its text needlessly small.  Per svg,
  // in root user units:
  //   1. title/subtitle lines that overflow horizontally are wrapped at word
  //      boundaries (font kept) and the title block moves up by the added
  //      lines; other overflowing text marks shrink, never below 6.5pt;
  //   2. any residual overflow grows the viewBox to the content (no clip);
  //   3. a DOWN-scaled drawing (renderer shrank it below its natural size)
  //      with > 4 % dead margin gets its viewBox trimmed to the content, and
  //      is widened to the column when its natural width exceeds the
  //      rendered one — but never taller than max(its box, 45 % of a page),
  //      so a height-limited drawing is left to the oversize guard.
  // Contained per svg; marks what it changed; untouched svgs get nothing.
  stats.contentFit = 0; stats.textWrapped = 0; stats.textFitted = 0;
  const FLOOR_PX = 8.67;  // 6.5pt at print size, in CSS px
  const LEAF_SEL = 'text, rect, path, line, circle, ellipse, polygon, polyline, image, foreignObject, use';
  // A leaf inside a clip-path group only paints within that clip (vega
  // clips plot marks to the plot area): intersect with the clip region.
  const clipRectOf = (svg, el, cache) => {
    const g = el.closest('[clip-path]');
    if (!g || !svg.contains(g)) return null;
    if (cache.has(g)) return cache.get(g);
    let out = null;
    try {
      const m = /url\(\s*["']?#([^"')]+)["']?\s*\)/.exec(g.getAttribute('clip-path') || '');
      const cp = m && svg.ownerDocument.getElementById(m[1]);
      if (cp && (cp.getAttribute('clipPathUnits') || 'userSpaceOnUse') === 'userSpaceOnUse') {
        const toUser = svg.getScreenCTM().inverse().multiply(g.getScreenCTM());
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        for (const c of cp.children) {
          const b = c.getBBox();
          for (const [px, py] of [[b.x, b.y], [b.x + b.width, b.y], [b.x, b.y + b.height], [b.x + b.width, b.y + b.height]]) {
            const q = new DOMPoint(px, py).matrixTransform(toUser);
            x0 = Math.min(x0, q.x); y0 = Math.min(y0, q.y); x1 = Math.max(x1, q.x); y1 = Math.max(y1, q.y);
          }
        }
        if (x1 > x0) out = { x0, y0, x1, y1 };
      }
    } catch (e) { out = null; }
    cache.set(g, out);
    return out;
  };
  const userRect = (svg, el, cache) => {
    const inv = svg.getScreenCTM().inverse();
    const r = el.getBoundingClientRect();
    const a = new DOMPoint(r.left, r.top).matrixTransform(inv);
    const b = new DOMPoint(r.right, r.bottom).matrixTransform(inv);
    const u = { x0: Math.min(a.x, b.x), x1: Math.max(a.x, b.x), y0: Math.min(a.y, b.y), y1: Math.max(a.y, b.y) };
    const c = cache ? clipRectOf(svg, el, cache) : null;
    if (c) {
      u.x0 = Math.max(u.x0, c.x0); u.y0 = Math.max(u.y0, c.y0);
      u.x1 = Math.min(u.x1, c.x1); u.y1 = Math.min(u.y1, c.y1);
    }
    return u;
  };
  const contentBox = (svg, cache) => {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity, n = 0;
    for (const el of svg.querySelectorAll(LEAF_SEL)) {
      if (++n > 20000) return null;
      if (el.closest('defs, clipPath, mask, marker, pattern, symbol')) continue;
      if (el.parentElement && el.parentElement.closest('text')) continue;
      const r = el.getBoundingClientRect();
      if (!(r.width > 0) && !(r.height > 0)) continue;
      const u = userRect(svg, el, cache);
      if (u.x1 < u.x0 || u.y1 < u.y0) continue;  // fully clipped away
      x0 = Math.min(x0, u.x0); y0 = Math.min(y0, u.y0);
      x1 = Math.max(x1, u.x1); y1 = Math.max(y1, u.y1);
    }
    return x1 > x0 ? { x0, y0, x1, y1 } : null;
  };
  const readVb = (svg) => {
    const vb = (svg.getAttribute('viewBox') || '').trim().split(/[\s,]+/).map(Number);
    return (vb.length === 4 && vb.every(isFinite) && vb[2] > 0 && vb[3] > 0) ? vb : null;
  };
  const setSvgBox = (svg, vb, w) => {
    svg.setAttribute('viewBox', vb.map(v => Math.round(v * 100) / 100).join(' '));
    svg.style.setProperty('width', Math.round(w) + 'px', 'important');
    svg.style.setProperty('height', Math.round(w * vb[3] / vb[2]) + 'px', 'important');
    svg.style.setProperty('max-width', '100%', 'important');
    svg.style.setProperty('max-height', 'none', 'important');
  };
  // Split one overflowing title line (a <tspan>, or the bare <text>) into
  // n word-balanced lines.  Returns the number of lines ADDED.
  const wrapLine = (text, line, n, lh) => {
    const words = (line.textContent || '').trim().split(/\s+/);
    if (words.length < 2) return 0;
    n = Math.min(n, words.length, 4);
    const total = words.join(' ').length, parts = [];
    let cur = [];
    for (const w of words) {
      cur.push(w);
      if (parts.length < n - 1 && cur.join(' ').length >= total / n) { parts.push(cur.join(' ')); cur = []; }
    }
    if (cur.length) parts.push(cur.join(' '));
    if (parts.length < 2) return 0;
    const ns = 'http://www.w3.org/2000/svg';
    let first = line, x = line.getAttribute('x');
    if (line === text) {  // bare text node: move its text into tspans
      x = text.getAttribute('x') || '0';
      first = document.createElementNS(ns, 'tspan');
      first.setAttribute('x', x);
      while (text.firstChild) text.removeChild(text.firstChild);
      text.appendChild(first);
    }
    first.textContent = parts[0];
    let after = first;
    for (const p of parts.slice(1)) {
      const t = document.createElementNS(ns, 'tspan');
      t.setAttribute('x', x == null ? '0' : x);
      t.setAttribute('dy', String(lh));
      t.textContent = p;
      after.parentNode.insertBefore(t, after.nextSibling);
      after = t;
    }
    return parts.length - 1;
  };
  const fitSvgContent = (svg, colW) => {
    if (svg.getAttribute('data-print-content-fit')) return;
    const vb = readVb(svg);
    if (!vb) return;
    const rect0 = svg.getBoundingClientRect();
    if (!(rect0.width > 0)) return;
    const pxPerUser = rect0.width / vb[2];
    const L = vb[0], R = vb[0] + vb[2], T = vb[1], B = vb[1] + vb[3];
    let changed = false;
    const clipCache = new Map();
    // 1. Overflowing text.
    for (const t of Array.from(svg.querySelectorAll('text'))) {
      try {
        if (t.closest('defs, clipPath, mask, marker, pattern, symbol')) continue;
        const u = userRect(svg, t, clipCache);
        if (!(u.x1 > u.x0) || (u.x0 >= L - 2 && u.x1 <= R + 2)) continue;
        const titleG = t.closest('g.role-title-text, g.role-title-subtitle, g.role-title');
        const fs = parseFloat(getComputedStyle(t).fontSize) || 12;
        if (titleG) {
          const lines = t.querySelectorAll('tspan').length ? Array.from(t.querySelectorAll('tspan')) : [t];
          let added = 0, lh = 0;
          for (const ln of lines) {
            const lu = userRect(svg, ln, clipCache);
            const w = lu.x1 - lu.x0;
            if (lu.x0 >= L - 2 && lu.x1 <= R + 2) continue;
            const dy = parseFloat(ln.getAttribute('dy'));
            const h = dy > 0 && !/em$/.test(ln.getAttribute('dy') || '') ? dy : fs * 1.2;
            const k = wrapLine(t, ln, Math.ceil(w / Math.max(1, vb[2] - 16)), h);
            if (k) { added += k; lh = Math.max(lh, h); }
          }
          if (added) {
            // Grow upward, away from the plot: move the whole title block
            // (title + subtitle) up by the added lines.
            const block = t.closest('g.role-title') || t;
            const tr = block.getAttribute('transform') || '';
            block.setAttribute('transform', ('translate(0,' + (-added * lh) + ') ' + tr).trim());
            t.setAttribute('data-print-text-wrap', '1');
            t.setAttribute('data-print-text-lines-added', String(added));
            stats.textWrapped += 1; changed = true;
            continue;
          }
        }
        // Text mark: shrink toward the fit, never below the floor.
        const w = u.x1 - u.x0, cx = (u.x0 + u.x1) / 2;
        const anchor = getComputedStyle(t).textAnchor || t.getAttribute('text-anchor') || 'start';
        let allowed;
        if (anchor === 'end') allowed = u.x1 - L;
        else if (anchor === 'middle') allowed = 2 * Math.min(cx - L, R - cx);
        else allowed = R - u.x0;
        const s = allowed / w;
        if (!(s > 0) || s >= 1) continue;
        const m = svg.getCTM().inverse().multiply(t.getCTM());
        const localScale = Math.hypot(m.a, m.b) || 1;
        const floorLocal = FLOOR_PX / pxPerUser / localScale;
        const nfs = Math.max(fs * s, floorLocal);
        if (nfs >= fs - 0.01) continue;
        t.style.setProperty('font-size', (Math.round(nfs * 100) / 100) + 'px', 'important');
        t.setAttribute('data-print-text-fit', String(Math.round(nfs / fs * 1000) / 1000));
        stats.textFitted += 1; changed = true;
      } catch (e) { /* leave this text as rendered */ }
    }
    // 2. Residual overflow: grow the viewBox to the content.
    const cb = contentBox(svg, new Map());
    if (cb && (cb.x0 < L - 2 || cb.x1 > R + 2 || cb.y0 < T - 2 || cb.y1 > B + 2)) {
      const nv = [Math.min(L, cb.x0 - 2), Math.min(T, cb.y0 - 2), 0, 0];
      nv[2] = Math.max(R, cb.x1 + 2) - nv[0];
      nv[3] = Math.max(B, cb.y1 + 2) - nv[1];
      setSvgBox(svg, nv, Math.min(nv[2] * pxPerUser, colW));
      svg.setAttribute('data-print-fit-reason', 'content');
      svg.setAttribute('data-print-content-fit', 'content');
      stats.contentFit += 1;
      return;
    }
    if (changed) { svg.setAttribute('data-print-content-fit', 'text'); return; }
    // 3. Down-scaled drawing: trim dead margins, widen to the column.
    if (!cb) return;
    const isPoints = !!svg.closest('[class*="graphviz"]');
    const naturalW = vb[2] * (isPoints ? 96 / 72 : 1);
    if (!(rect0.width < naturalW * 0.98)) return;
    let nv = vb, reason = 'widen';
    const slackX = (cb.x0 - L) + (R - cb.x1), slackY = (cb.y0 - T) + (B - cb.y1);
    if (slackX > 0.04 * vb[2] || slackY > 0.04 * vb[3]) {
      const x0 = Math.max(L, cb.x0 - 8), y0 = Math.max(T, cb.y0 - 8);
      nv = [x0, y0, Math.min(R, cb.x1 + 8) - x0, Math.min(B, cb.y1 + 8) - y0];
      reason = 'trim';
    }
    const natNew = nv[2] * (naturalW / vb[2]);
    const maxW = Math.min(colW, Math.max(rect0.width, natNew));
    const maxH = Math.max(rect0.height, pageH * 0.45);
    const k2 = Math.min(maxW / nv[2], maxH / nv[3]);
    if (!(k2 > pxPerUser * 1.02)) return;
    setSvgBox(svg, nv, nv[2] * k2);
    svg.setAttribute('data-print-fit-reason', reason);
    svg.setAttribute('data-print-content-fit', reason);
    svg.setAttribute('data-print-content-scale', String(Math.round(k2 / pxPerUser * 1000) / 1000));
    stats.contentFit += 1;
  };
  stats.titles = 0;
  stats.oversize = 0;
  stats.sideCaption = 0; stats.landscape = 0;
  // Smallest on-screen text size (px) inside a drawing: <text> via its
  // screen CTM, foreignObject labels via their computed font-size scaled by
  // the foreignObject's CTM.  null when the drawing has no text.
  const minTextPxOf = (drawing) => {
    let min = null;
    const take = (v) => { if (v > 0 && (min === null || v < min)) min = v; };
    if (!drawing || drawing.tagName.toLowerCase() !== 'svg') return null;
    for (const t of Array.from(drawing.querySelectorAll('text'))) {
      if (t.closest('defs, clipPath, mask, marker, pattern, symbol')) continue;
      if (!(t.textContent || '').trim()) continue;
      const m = t.getScreenCTM();
      if (!m) continue;
      take((parseFloat(getComputedStyle(t).fontSize) || 0) * Math.hypot(m.a, m.b));
    }
    for (const fo of Array.from(drawing.querySelectorAll('foreignObject'))) {
      const m = fo.getScreenCTM();
      if (!m) continue;
      const k = Math.hypot(m.a, m.b);
      for (const el of Array.from(fo.querySelectorAll('*'))) {
        const own = Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim());
        if (own) take((parseFloat(getComputedStyle(el).fontSize) || 0) * k);
      }
    }
    return min;
  };
  // Inline-style snapshot/restore for a revertable layout attempt.
  const snapStyle = (els) => els.map(el => [el, el.getAttribute('style'),
    el.getAttribute('data-print-fit-reason'), el.getAttribute('data-print-unit-scale')]);
  const restoreStyle = (snap) => {
    for (const [el, st, fr, us] of snap) {
      if (st === null) el.removeAttribute('style'); else el.setAttribute('style', st);
      if (fr === null) el.removeAttribute('data-print-fit-reason'); else el.setAttribute('data-print-fit-reason', fr);
      if (us === null) el.removeAttribute('data-print-unit-scale'); else el.setAttribute('data-print-unit-scale', us);
    }
  };
  // ── d031: side caption for a tall, narrow, height-limited drawing ─────
  // A narrow drawing leaves over half the column empty beside it; putting
  // the title + caption there takes them out of the height budget, so the
  // unit fits one page and the drawing may grow (never past its natural
  // size).  Reverted when the unit still does not fit.  Returns true if kept.
  const SIDE_GAP = 14, SIDE_MIN_PANEL = 200;
  const trySideCaption = (wrap, fig, drawing, title, cap, limit0) => {
    if (opts.sideCaption === false || !(title || cap)) return false;
    if (drawing.tagName.toLowerCase() !== 'svg') return false;
    // The side unit only has to fit the page with its own top margin (the
    // bottom margin is truncated at a page break); the drawing is the height
    // budget here, so every px of it is text size.  1px under for rounding.
    const mt = parseFloat(getComputedStyle(wrap).marginTop) || 0;
    const limit = Math.max(limit0, pageH - mt - 4) - 1;
    const colW = wrap.getBoundingClientRect().width;
    const dr = drawing.getBoundingClientRect();
    if (!(colW > 0) || !(dr.width > 0) || dr.width > 0.55 * colW) return false;
    const vb = readVb(drawing);
    const isPoints = !!drawing.closest('[class*="graphviz"]');
    const naturalH = vb ? vb[3] * (isPoints ? 96 / 72 : 1) : dr.height;
    const figExtra = Math.max(0, fig.getBoundingClientRect().height - dr.height);
    const s = Math.min((limit - figExtra) / dr.height,
                       (colW - SIDE_MIN_PANEL - SIDE_GAP) / dr.width,
                       naturalH / dr.height);
    if (!(s > 0)) return false;
    const snap = snapStyle([wrap, fig, drawing]);
    const titleNext = title ? title.el.nextSibling : null;
    const panel = document.createElement('div');
    panel.className = 'print-doc-figure-sidepanel';
    if (title) panel.appendChild(title.el);
    if (cap) panel.appendChild(cap);
    const newW = Math.round(dr.width * s);
    scaleDrawing(drawing, s, 'side');
    drawing.style.setProperty('max-width', 'none', 'important');
    fig.style.setProperty('width', newW + 'px', 'important');
    fig.style.setProperty('margin', '0', 'important');
    wrap.appendChild(panel);
    wrap.classList.add('print-doc-figure-side');
    wrap.style.setProperty('grid-template-columns', newW + 'px minmax(0, 1fr)', 'important');
    const uh2 = wrap.getBoundingClientRect().height;
    if (uh2 <= limit + 1) {
      wrap.setAttribute('data-print-figure-side', '1');
      stats.sideCaption += 1;
      return true;
    }
    // Revert: the author's order (title, drawing, caption) and styles.
    wrap.classList.remove('print-doc-figure-side');
    if (title) wrap.insertBefore(title.el, titleNext && titleNext.parentNode === wrap ? titleNext : fig);
    if (cap) wrap.appendChild(cap);
    panel.remove();
    restoreStyle(snap);
    return false;
  };
  // ── d030: landscape (rotated) unit for a wide, width-limited drawing ──
  // Text printing below 6.5pt only because the column is narrow gains the
  // page height (≈1.4x) when the unit is turned a quarter counter-clockwise
  // onto its own page (LaTeX sidewaysfigure).  Only when that gain lifts
  // the smallest text to the floor; title and caption rotate with it.
  const LAND_MIN_GAIN = 1.2;
  const tryLandscape = (wrap, fig, drawing, limit) => {
    if (opts.landscapeFigures === false) return false;
    if (!drawing || drawing.tagName.toLowerCase() !== 'svg') return false;
    if (wrap.hasAttribute('data-print-figure-oversize') || wrap.hasAttribute('data-print-figure-side')) return false;
    const colW = wrap.getBoundingClientRect().width;
    const dr = drawing.getBoundingClientRect();
    if (!(colW > 0) || !(dr.width > 0) || dr.width < 0.95 * colW) return false;
    const minPx = minTextPxOf(drawing);
    if (minPx === null || minPx >= FLOOR_PX) return false;
    const L = Math.floor(limit);
    let g = L / dr.width;
    if (g < LAND_MIN_GAIN || minPx * g < FLOOR_PX) return false;
    const parent = wrap.parentElement;
    if (!parent) return false;
    const snap = snapStyle([wrap, drawing]);
    const box = document.createElement('div');
    box.className = 'print-doc-figure-landscape';
    parent.insertBefore(box, wrap);
    box.appendChild(wrap);
    wrap.style.setProperty('width', L + 'px', 'important');
    wrap.style.setProperty('position', 'absolute', 'important');
    wrap.style.setProperty('top', '0', 'important');
    wrap.style.setProperty('left', '0', 'important');
    wrap.style.setProperty('margin', '0', 'important');
    const undo = () => { parent.insertBefore(wrap, box); box.remove(); restoreStyle(snap); return false; };
    scaleDrawing(drawing, g, 'landscape');
    let h = wrap.getBoundingClientRect().height;
    if (h > colW) {
      // Title + caption laid out at length L take the rest of the width.
      const other = h - drawing.getBoundingClientRect().height;
      const g2 = (colW - other) / dr.height;
      if (!(g2 >= LAND_MIN_GAIN) || minPx * Math.min(g, g2) < FLOOR_PX) return undo();
      g = Math.min(g, g2);
      restoreStyle(snap.filter(x => x[0] === drawing));
      scaleDrawing(drawing, g, 'landscape');
      h = wrap.getBoundingClientRect().height;
      if (h > colW + 0.5) return undo();
    }
    const mp = minTextPxOf(drawing);
    if (mp !== null && mp < FLOOR_PX - 0.05) return undo();
    wrap.style.setProperty('transform-origin', 'top left', 'important');
    wrap.style.setProperty('transform', 'translateY(' + L + 'px) rotate(-90deg)', 'important');
    box.style.setProperty('width', colW + 'px', 'important');
    box.style.setProperty('height', L + 'px', 'important');
    box.setAttribute('data-print-figure-landscape', '1');
    box.setAttribute('data-print-landscape-gain', String(Math.round(g * 1000) / 1000));
    stats.landscape += 1;
    return true;
  };
  const figSel = '.d3-container[data-visualization-type], figure, p > img';
  const figs = Array.from(root.querySelectorAll(figSel));
  let figN = 0;
  for (let fig of figs) {
    try {
      if (fig.tagName === 'IMG') {
        const p = fig.parentElement;
        // Only a paragraph that is JUST the image (optionally whitespace).
        if (!p || (p.textContent || '').trim() !== '' || p.querySelectorAll('img').length !== 1) continue;
        fig = p;
      }
      if (fig.closest('.print-doc-figure')) continue;
      if (fig.closest('.print-doc-titleblock, .print-footer')) continue;
      // Nested renderer containers: only the outermost is the figure unit.
      if (fig.parentElement && fig.parentElement.closest(figSel)) continue;
      const parent = fig.parentElement;
      if (!parent) continue;
      figN += 1;
      // Collapse the renderer's reserved box to the drawing.  Wrappers carry
      // inline `height`/`min-height` sized to the SCREEN layout and, for
      // mermaid, `padding … !important`; only an inline important
      // declaration outranks those, so the stylesheet alone cannot do it.
      for (const box of [fig, ...Array.from(fig.querySelectorAll('div'))]) {
        const cls = (box.className || '').toString();
        const isBox = box === fig || /d3-container|-wrapper|-container/.test(cls);
        if (!isBox || /diagram-actions/.test(cls)) continue;
        box.style.setProperty('height', 'auto', 'important');
        box.style.setProperty('min-height', '0', 'important');
        box.style.setProperty('padding', '0', 'important');
        box.style.setProperty('margin', '0 auto', 'important');
      }
      // Undo screen-oriented UP-scaling: a renderer that stretches its <svg>
      // to the column (graphviz writes width:1280px) blows a small graph up
      // to poster glyph sizes.  Clamp to the drawing's natural size — the
      // viewBox, in px, or in points (x4/3) for graphviz — never larger than
      // the column.  Down-scaling (fitOversizedFigures) is left alone.
      for (const svg of Array.from(fig.querySelectorAll('svg'))) {
        try {
          if (svg.closest('.diagram-actions') || svg.getAttribute('data-print-natural-fit')) continue;
          const vb = (svg.getAttribute('viewBox') || '').trim().split(/[\s,]+/).map(Number);
          if (vb.length !== 4 || !(vb[2] > 0) || !(vb[3] > 0)) continue;
          const isPoints = !!svg.closest('[class*="graphviz"]');
          const unit = isPoints ? 96 / 72 : 1;
          const naturalW = vb[2] * unit;
          const rect = svg.getBoundingClientRect();
          const colW = parent.getBoundingClientRect().width || naturalW;
          const targetW = Math.min(naturalW, colW);
          if (rect.width > targetW * 1.05) {
            svg.style.setProperty('width', Math.round(targetW) + 'px', 'important');
            svg.style.setProperty('height', Math.round(targetW * vb[3] / vb[2]) + 'px', 'important');
            svg.style.setProperty('max-width', '100%', 'important');
            svg.setAttribute('data-print-natural-fit', String(targetW / rect.width));
          }
        } catch (e) { /* leave this svg as rendered */ }
      }
      // Fit overflowing content / trim dead margins (see fitSvgContent).
      for (const svg of Array.from(fig.querySelectorAll('svg'))) {
        try {
          if (svg.closest('.diagram-actions, button')) continue;
          if (svg.parentElement && svg.parentElement.closest('svg')) continue;
          fitSvgContent(svg, parent.getBoundingClientRect().width || 673);
        } catch (e) { /* leave this svg as rendered */ }
      }
      // Caption below / authored title above are detached only now, right
      // before the wrapper takes them, so a failure earlier in this figure
      // cannot drop the author's text.
      let captionHtml = takeCaption(fig.nextElementSibling, CAPTION_FIG);
      if (captionHtml === null) captionHtml = takeLeadCaption(fig.nextElementSibling);
      const title = takeTitle(fig.previousElementSibling);
      const wrap = document.createElement('div');
      wrap.className = 'print-doc-figure';
      wrap.setAttribute('data-print-figure', String(figN));
      parent.insertBefore(wrap, fig);
      // The authored title leads the unit (break-after: avoid), so it can
      // no longer be stranded at a page bottom apart from its drawing.
      if (title) {
        wrap.appendChild(title.el);
        wrap.setAttribute('data-print-figure-authored', title.num);
        stats.titles += 1;
      }
      wrap.appendChild(fig);
      // An authored "Figure N — …" title already carries the number: an auto
      // "Figure N" label under the drawing would only repeat it.
      const wantLabel = numbering.figures && !title;
      let cap = null;
      if (wantLabel || captionHtml) {
        cap = document.createElement('div');
        cap.className = 'print-doc-caption';
        let inner = '';
        if (wantLabel) {
          inner += '<span class="print-doc-caption-label">Figure ' + figN + '</span>';
          if (captionHtml) inner += '<span class="print-doc-caption-sep">. </span>';
        }
        if (captionHtml) inner += '<span class="print-doc-caption-text">' + captionHtml + '</span>';
        cap.innerHTML = inner;
        wrap.appendChild(cap);
        // A multi-line caption reads badly centred: full measure, ragged.
        if ((cap.textContent || '').length > 200) cap.classList.add('print-doc-caption-long');
        if (captionHtml) stats.captions += 1;
      }
      // Oversize guard: a unit (title + drawing + caption) taller than the
      // page cannot honour break-inside: avoid — Chromium would then break
      // it anywhere, possibly between title and drawing.  A small shrink
      // keeps it on one page; otherwise the unit may break, but only after
      // the drawing (the caption moves on), never between title and drawing.
      try {
        const limit = pageH * UNIT_FRAC;
        const drawing = drawingOf(fig);
        const uh = wrap.getBoundingClientRect().height;
        if (drawing && uh > limit && trySideCaption(wrap, fig, drawing, title, cap, limit)) {
          /* d031: fits one page with the caption beside the drawing */
        } else if (drawing && uh > limit) {
          const dh = drawing.getBoundingClientRect().height;
          const sUnit = dh > 0 ? (dh - (uh - limit)) / dh : 0;
          if (sUnit >= MIN_UNIT_SCALE) {
            scaleDrawing(drawing, sUnit, 'unit');
          } else {
            wrap.setAttribute('data-print-figure-oversize', '1');
            wrap.style.setProperty('break-inside', 'auto', 'important');
            wrap.style.setProperty('page-break-inside', 'auto', 'important');
            fig.style.setProperty('break-inside', 'avoid', 'important');
            fig.style.setProperty('page-break-inside', 'avoid', 'important');
            stats.oversize += 1;
            // Title + drawing must still fit one page for "title stays with
            // the drawing" to hold; shrink a little if they do not.
            const top = (title ? title.el : fig).getBoundingClientRect().top;
            const th = fig.getBoundingClientRect().bottom - top;
            if (th > limit) {
              const sTd = dh > 0 ? (dh - (th - limit)) / dh : 0;
              if (sTd >= MIN_UNIT_SCALE) scaleDrawing(drawing, sTd, 'title');
            }
            // The title's break-after: avoid alone is not enough: with title
            // + drawing at the page limit Chromium split the 2-line title
            // across pages (d022, iter_004 p4/p5).  Bind title and drawing
            // in one unbreakable box; only the caption may move on.
            if (title && title.el.parentElement === wrap && fig.parentElement === wrap) {
              const head = document.createElement('div');
              head.className = 'print-doc-figure-head';
              wrap.insertBefore(head, title.el);
              head.appendChild(title.el);
              head.appendChild(fig);
            }
          }
        }
      } catch (e) { /* guard is best-effort */ }
      try { tryLandscape(wrap, fig, drawingOf(fig), pageH * UNIT_FRAC); }
      catch (e) { /* stays upright */ }
      stats.figures += 1;
    } catch (e) { /* skip this figure */ }
  }

  // ── 3. Tables: caption + number + column balance + long-table flow ───
  // balanceColumns: Chromium's auto layout shares width by max-content, so
  // one long cell in a sparse column starves a column that wraps on every
  // row (d007).  Estimate each cell's one-line width and its widest
  // unbreakable word, then hand out width in 4px steps to whichever column
  // most reduces the estimated table height (sum over rows of the row's
  // tallest cell, in lines).  Fixed via <colgroup>; reverted if the real
  // table got taller.  Contained per table; never touches authored widths.
  stats.colBalanced = 0; stats.colbalanceReverted = 0;
  let measureCtx = null;
  const balanceColumns = (t) => {
    if (opts.colBalance === false) return;
    if (t.hasAttribute('data-print-colbalance')) return;
    if (t.querySelector(':scope > colgroup, :scope > col')) return;
    // Table-level width:100% is the markdown renderer's inline default, not
    // an authored column layout; only per-column widths / fixed layout are.
    if (t.style.tableLayout === 'fixed') return;
    const rows = Array.from(t.rows);
    if (!rows.length) return;
    const ncol = rows[0].cells.length;
    if (ncol < 3) return;
    const bodyRows = t.tBodies.length
        ? Array.from(t.tBodies).reduce((a, b) => a + b.rows.length, 0) : rows.length - 1;
    if (bodyRows < 2) return;
    for (const r of rows) {
      if (r.cells.length !== ncol) return;
      for (const c of r.cells) {
        if (c.colSpan > 1 || c.rowSpan > 1) return;
        if (c.getAttribute('width') || c.style.width) return;
      }
    }
    if (!measureCtx) measureCtx = document.createElement('canvas').getContext('2d');
    const ctx = measureCtx;
    const fontOf = (el) => {
      const cs = getComputedStyle(el);
      return cs.fontStyle + ' ' + cs.fontWeight + ' ' + cs.fontSize + ' ' + cs.fontFamily;
    };
    // Per cell: the words with their measured widths (greedy line-breaking
    // below reproduces the browser's wrap far better than width/one-line).
    const words = [], minWord = new Array(ncol).fill(0), maxOne = new Array(ncol).fill(0),
          pad = new Array(ncol).fill(0);
    for (const r of rows) {
      const line = [];
      Array.from(r.cells).forEach((cell, ci) => {
        const cs = getComputedStyle(cell);
        pad[ci] = Math.max(pad[ci], (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0)
                                    + (parseFloat(cs.borderLeftWidth) || 0) + (parseFloat(cs.borderRightWidth) || 0));
        const ws = [];  // [wordWidth, spaceWidthAfter]
        let one = 0;
        const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
        let n;
        while ((n = walker.nextNode())) {
          const txt = n.nodeValue.replace(/\s+/g, ' ');
          if (!txt.trim()) { if (ws.length) ws[ws.length - 1][1] = Math.max(ws[ws.length - 1][1], 3); continue; }
          ctx.font = fontOf(n.parentElement);
          const sp = ctx.measureText(' ').width;
          one += ctx.measureText(txt).width;
          const toks = txt.split(' ');
          toks.forEach((tok, k) => {
            if (!tok) { if (ws.length) ws[ws.length - 1][1] = sp; return; }
            const tw = ctx.measureText(tok).width;
            minWord[ci] = Math.max(minWord[ci], tw);
            // A token glued to the previous text node (no space) extends that word.
            if (k === 0 && ws.length && ws[ws.length - 1][1] === 0) ws[ws.length - 1][0] += tw;
            else ws.push([tw, 0]);
            if (k < toks.length - 1) ws[ws.length - 1][1] = sp;
          });
        }
        maxOne[ci] = Math.max(maxOne[ci], one);
        line.push(ws);
      });
      words.push(line);
    }
    const tw = t.getBoundingClientRect().width;
    const W = tw - pad.reduce((a, b) => a + b, 0);
    if (!(W > 0)) return;
    const w = minWord.map((m, c) => Math.max(Math.ceil(m) + 1, Math.min(Math.round(0.10 * W), Math.ceil(maxOne[c]) + 1)));
    let rest = W - w.reduce((a, b) => a + b, 0);
    if (rest < 0) return;  // unbreakable words already overfill: leave auto layout
    const oneW = words.map((row) => row.map((ws) => ws.reduce((a, q) => a + q[0] + q[1], 0)));
    const memo = new Map();
    const lines = (r, c, wc) => {
      const key = r * 64 + c + ':' + wc;
      let v = memo.get(key);
      if (v !== undefined) return v;
      const ws = words[r][c];
      let n = 1, x = 0;
      const lim = wc - 0.5;  // subpixel slack
      for (let i = 0; i < ws.length; i++) {
        const need = ws[i][0];
        if (x > 0 && x + need > lim) { n += 1; x = 0; }
        x += need + ws[i][1];
      }
      memo.set(key, n);
      return n;
    };
    const cost = (ws) => {
      let H = 0, Hc = 0;
      for (let r = 0; r < words.length; r++) {
        let m = 0, mc = 0;
        for (let c = 0; c < ncol; c++) {
          m = Math.max(m, lines(r, c, ws[c]));
          mc = Math.max(mc, oneW[r][c] / ws[c]);
        }
        H += m; Hc += mc;
      }
      return [H, Hc];
    };
    const STEP = 4;
    // Phase 1 — row moves.  Lowering row r by one line may need SEVERAL
    // columns widened at once (a single 4px step on one column then shows no
    // gain, and a pure per-column greedy wanders off to a sparse column's
    // single tall cell).  So each candidate move is "make row r one line
    // shorter": the width every over-tall cell in that row needs, costed and
    // evaluated on the whole table; take the best gain per pixel, repeat.
    const reqW = (r, c, L, from) => {
      let x = from;
      while (lines(r, c, x) > L && x - from <= rest) x += STEP;
      return x;
    };
    for (let guard = 0; guard < 400; guard++) {
      const H0 = cost(w)[0];
      let best = null;
      for (let r = 0; r < words.length; r++) {
        let L = 0;
        for (let c = 0; c < ncol; c++) L = Math.max(L, lines(r, c, w[c]));
        if (L <= 1) continue;
        const nw = w.slice();
        let extra = 0;
        for (let c = 0; c < ncol; c++) {
          if (lines(r, c, w[c]) > L - 1) { nw[c] = reqW(r, c, L - 1, w[c]); extra += nw[c] - w[c]; }
        }
        if (extra <= 0 || extra > rest) continue;
        const gain = H0 - cost(nw)[0];
        if (gain <= 0) continue;
        const ratio = gain / extra;
        if (!best || ratio > best.ratio) best = { nw, extra, ratio };
      }
      if (!best) break;
      for (let c = 0; c < ncol; c++) w[c] = best.nw[c];
      rest -= best.extra;
    }
    // Phase 2 — hand out what is left in 4px steps (line count first, then
    // the continuous row-height estimate as tie-break).
    while (rest >= STEP) {
      let best = -1, bestCost = null;
      for (let c = 0; c < ncol; c++) {
        w[c] += STEP;
        const k = cost(w);
        w[c] -= STEP;
        if (bestCost === null || k[0] < bestCost[0] - 1e-9
            || (k[0] === bestCost[0] && k[1] < bestCost[1] - 1e-9)) { best = c; bestCost = k; }
      }
      w[best] += STEP; rest -= STEP;
    }
    w[w.indexOf(Math.max(...w))] += rest;  // leftover < STEP
    // (A pairwise-transfer hill-climb after this was tried and found no
    // better widths on the real Table 1 — the estimate is already at a local
    // optimum — so it is not run.)
    const h0 = t.getBoundingClientRect().height;
    const cg = document.createElement('colgroup');
    cg.setAttribute('data-print-colbalance', '1');
    for (let c = 0; c < ncol; c++) {
      const col = document.createElement('col');
      col.style.width = ((w[c] + pad[c]) / tw * 100).toFixed(3) + '%';
      cg.appendChild(col);
    }
    const cap = t.querySelector(':scope > caption');
    t.insertBefore(cg, cap ? cap.nextSibling : t.firstChild);
    const addedLang = !t.closest('[lang]');
    if (addedLang) t.setAttribute('lang', 'en');
    const prevLayout = t.style.tableLayout, prevWidth = t.style.width;
    t.style.tableLayout = 'fixed';
    t.style.width = '100%';
    t.setAttribute('data-print-colbalance', '1');
    const h1 = t.getBoundingClientRect().height;
    if (h1 > h0 + 0.5) {
      cg.remove();
      if (addedLang) t.removeAttribute('lang');
      t.style.tableLayout = prevLayout; t.style.width = prevWidth;
      t.removeAttribute('data-print-colbalance');
      stats.colbalanceReverted += 1;
      return;
    }
    stats.colBalanced += 1;
  };

  const tables = Array.from(root.querySelectorAll('table'));
  let tblN = 0;
  // Read once, before any caption is inserted: a bare "Table N" label is
  // only worth printing when the prose refers to "Table N".
  const bodyText = root.textContent || '';
  const referenced = (n) => new RegExp('\\bTable\\s+' + n + '\\b').test(bodyText);
  for (const t of tables) {
    try {
      if (t.classList.contains('diff-table')) continue;
      if (t.closest('.diff-view, .diff-container, .print-footer')) continue;
      // Already decorated (with or without a caption) — idempotent.
      if (t.hasAttribute('data-print-table')) continue;
      if (t.querySelector(':scope > caption.print-doc-table-caption')) continue;
      // Caption may sit in the paragraph directly above or directly below.
      let captionHtml = takeCaption(t.previousElementSibling, CAPTION_TBL);
      if (captionHtml === null) captionHtml = takeCaption(t.nextElementSibling, CAPTION_TBL);
      tblN += 1;
      t.setAttribute('data-print-table', String(tblN));
      if (captionHtml || (numbering.tables && referenced(tblN))) {
        const cap = document.createElement('caption');
        cap.className = 'print-doc-table-caption';
        let inner = '';
        if (numbering.tables) {
          inner += '<span class="print-doc-caption-label">Table ' + tblN + '</span>';
          if (captionHtml) inner += '<span class="print-doc-caption-sep">. </span>';
        }
        if (captionHtml) inner += '<span class="print-doc-caption-text">' + captionHtml + '</span>';
        cap.innerHTML = inner;
        t.insertBefore(cap, t.firstChild);
        if (captionHtml) stats.captions += 1;
      }
      try { balanceColumns(t); } catch (e) { /* layout stays auto */ }
      const h = t.getBoundingClientRect().height;
      if (h > pageH * longFrac) {
        t.classList.add('print-doc-table-flow');
        stats.flowTables += 1;
      }
      stats.tables += 1;
    } catch (e) { /* skip this table */ }
  }

  // ── 4. Reviewer / editorial notes (R10) ──────────────────────────────
  // A blockquote whose lead (its first bold run, else its first 40 chars)
  // reads "Reviewer note…", "Note:", "Editorial comment…", "TODO…" is a
  // note, not a quotation: tag it so the stylesheet gives it an amber
  // call-out.  Anchored at the lead, so prose quoting "the note says…" stays
  // a quote.  Idempotent (class checked); contained per blockquote.
  stats.notes = 0;
  const NOTE_LEAD = /^\s*(reviewer|editor(?:ial)?|author(?:'s)?|internal|review)?\s*(note|comment)s?\b|^\s*(todo|fixme|draft)\b/i;
  for (const bq of Array.from(root.querySelectorAll('blockquote'))) {
    try {
      if (bq.classList.contains('print-doc-note')) continue;
      if (bq.closest('.print-footer')) continue;
      const first = bq.firstElementChild || bq;
      const full = (first.textContent || '').trim();
      let lead = full.slice(0, 40);
      const b = first.querySelector(':scope > strong:first-child, :scope > b:first-child');
      if (b && full.startsWith((b.textContent || '').trim())) lead = (b.textContent || '').trim();
      const m = NOTE_LEAD.exec(lead);
      if (!m) continue;
      const kind = m[3] ? 'todo' : (m[1] ? 'reviewer' : 'note');
      bq.classList.add('print-doc-note');
      bq.setAttribute('data-print-note', kind);
      stats.notes += 1;
    } catch (e) { /* leave this quote as is */ }
  }

  // ── 5. Figure float (d027) ───────────────────────────────────────────
  // The driver measured the first capture's PDF: the page before figure N
  // ends with a gap of G px because the unbreakable figure unit moved to
  // the next page.  Move the WHOLE prose blocks that follow the unit (same
  // section: never across a heading, figure, table or rule; at most 4; P /
  // UL / OL / DL / note blockquotes only) ahead of it while they fit in
  // 95% of G — the LaTeX [tp] float.  Figure order (and numbering) is
  // unchanged.  Idempotent: a unit that already floated is skipped;
  // contained per figure.
  stats.floated = 0;
  const floats = (opts.floatFigures && typeof opts.floatFigures === 'object') ? opts.floatFigures : {};
  const FLOAT_MAX_BLOCKS = 4;
  const FLOAT_TAGS = /^(P|UL|OL|DL)$/;
  const FIG_TITLE_RE = /^\s*Figure\s+\d+\s*[\u2014\u2013-]/;
  for (const [n, gapRaw] of Object.entries(floats)) {
    try {
      const gapPx = Number(gapRaw) || 0;
      if (gapPx <= 0) continue;
      const wrap = root.querySelector('.print-doc-figure[data-print-figure="' + String(n).replace(/[^0-9]/g, '') + '"]');
      if (!wrap || !wrap.parentElement) continue;
      if (wrap.hasAttribute('data-print-figure-oversize')) continue;
      if (wrap.closest('[data-print-figure-landscape]')) continue;
      if (wrap.hasAttribute('data-print-float-blocks')) continue;
      const moved = [];
      let used = 0;
      let el = wrap.nextElementSibling;
      while (el && moved.length < FLOAT_MAX_BLOCKS) {
        const isNote = el.tagName === 'BLOCKQUOTE' && el.classList.contains('print-doc-note');
        if (!FLOAT_TAGS.test(el.tagName) && !isNote) break;
        if (el.matches('.print-doc-figure, .print-doc-titleblock')) break;
        if (el.querySelector('h1,h2,h3,h4,h5,h6,.print-doc-figure,table,svg,img,.d3-container,pre')) break;
        if (FIG_TITLE_RE.test(el.textContent || '')) break;
        const cs = getComputedStyle(el);
        const h = el.getBoundingClientRect().height
                + (parseFloat(cs.marginTop) || 0) + (parseFloat(cs.marginBottom) || 0);
        if (used + h > gapPx * 0.95) break;   // whole blocks only
        moved.push(el);
        used += h;
        el = el.nextElementSibling;
      }
      for (const b of moved) {
        wrap.parentElement.insertBefore(b, wrap);
        b.setAttribute('data-print-floated', String(n));
      }
      if (moved.length) {
        wrap.setAttribute('data-print-float-blocks', String(moved.length));
        stats.floated += 1;
      }
    } catch (e) { /* leave this figure where it is */ }
  }

  root.setAttribute('data-print-doc-decorated', JSON.stringify(stats));
  return stats;
}
"""


def build_document_decorate_js(
    *,
    numbering: Optional[Dict[str, bool]] = None,
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    author: Optional[str] = None,
    date: Optional[str] = None,
    layout: str = "plain",
    page_height_px: int = 1016,
) -> Tuple[str, Dict[str, Any]]:
    """Return ``(js_function_source, options)`` for ``page.evaluate(js, options)``.

    The function source is constant; every document-specific value travels in
    the options object so Playwright serialises it (no string interpolation of
    document text into code).
    """
    opts: Dict[str, Any] = {
        "numbering": normalize_numbering(numbering),
        "title": title or "",
        "subtitle": subtitle or "",
        "author": author or "",
        "date": date or "",
        "layout": layout or "plain",
        "pageHeightPx": int(page_height_px),
        "longTableFraction": LONG_TABLE_PAGE_FRACTION,
        # d027: {figure number: gap px} set by the driver's float recapture.
        "floatFigures": {},
        # d030 / d031: automatic landscape and side-caption figure layouts.
        "landscapeFigures": True,
        "sideCaption": True,
    }
    return _DECORATE_JS_BODY, opts


# ---------------------------------------------------------------------------
# Running footer for documents
# ---------------------------------------------------------------------------

def build_document_footer_template(
    *,
    title: str,
    author: str = "",
    date: str = "",
    version: str = "",
    model: str = "",
    provider: str = "",
) -> str:
    """Chromium ``footer_template`` for an authored document.

    Line 1 names the document: ``Title · Author · Date`` in the body colour.
    Line 2 is the tool credit in a lighter tone: ``Ziya vX · model (Provider)``.
    Page numbers sit on the right.  The Ziya mark is the same inlined PNG the
    conversation footer uses.  All dynamic text is HTML-escaped; the template
    sets its own font-size because Chromium renders footer templates at 0.
    Footer templates are not scaled: px print as px*0.75 pt, so 9.4px/8.8px
    give 7.05pt/6.6pt — above the 6.5pt legibility floor (d015).
    """
    from app.utils.export_logo import get_logo_data_uri
    # Lazy: pdf_exporter imports this module lazily too, so neither side
    # imports the other at module load.
    from app.services.pdf_exporter import _provider_display_name

    doc_parts = [p for p in (title.strip(), (author or "").strip(),
                             (date or "").strip()) if p]
    doc_line = " · ".join(_html.escape(p) for p in doc_parts) or "Document"

    credit = ["Ziya"]
    if (version or "").strip():
        credit[0] = f"Ziya v{version.strip()}"
    model_s = (model or "").strip()
    if model_s and model_s.lower() not in ("unknown", "test-model"):
        prov = _provider_display_name(provider)
        credit.append(f"{model_s} ({prov})" if prov else model_s)
    credit_line = " · ".join(_html.escape(p) for p in credit)

    return (
        '<div style="width:100%;font-size:9.4px;'
        "font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;"
        'color:#57606a;padding:0 16mm;box-sizing:border-box;">'
        '<div style="border-top:0.5px solid #c8ccd1;padding-top:3px;'
        'display:flex;align-items:center;">'
        f'<img src="{get_logo_data_uri()}" '
        'style="height:16px;width:auto;margin-right:7px;opacity:0.85;"/>'
        '<div style="flex:1;min-width:0;line-height:1.5;overflow:hidden;">'
        f'<div style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{doc_line}</div>'
        f'<div style="color:#a8aeb8;font-size:8.8px;">{credit_line}</div>'
        "</div>"
        '<div style="flex:none;margin-left:12px;font-variant-numeric:tabular-nums;">'
        'Page <span class="pageNumber"></span> of '
        '<span class="totalPages"></span>'
        "</div></div></div>"
    )
