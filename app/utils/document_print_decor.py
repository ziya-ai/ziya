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

# Margins ``export_document_pdf`` uses when the front-matter sets none (mirrors
# the conversation export's defaults in pdf_exporter.capture_pdf).
DEFAULT_DOCUMENT_MARGIN: Dict[str, str] = {
    "top": "16mm", "bottom": "18mm", "left": "16mm", "right": "16mm",
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
  font-size: 10.5pt;
  line-height: 1.5;
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
body.ziya-print-document .print-doc-figure > figure {
  margin: 0 auto !important;
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
body.ziya-print-document #print-render-content .math-display,
body.ziya-print-document #print-render-content .katex-display {
  margin: 10px 0 14px;
}
"""


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

  // ── 2. Figures: wrap + number + caption ──────────────────────────────
  const CAPTION_FIG = /^\s*(?:figure|fig\.?)\s*[:.\u2014-]\s*/i;
  const CAPTION_TBL = /^\s*table\s*[:.\u2014-]\s*/i;
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
      const captionHtml = takeCaption(fig.nextElementSibling, CAPTION_FIG);
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
      const wrap = document.createElement('div');
      wrap.className = 'print-doc-figure';
      wrap.setAttribute('data-print-figure', String(figN));
      parent.insertBefore(wrap, fig);
      wrap.appendChild(fig);
      if (numbering.figures || captionHtml) {
        const cap = document.createElement('div');
        cap.className = 'print-doc-caption';
        let inner = '';
        if (numbering.figures) {
          inner += '<span class="print-doc-caption-label">Figure ' + figN + '</span>';
          if (captionHtml) inner += '<span class="print-doc-caption-sep">. </span>';
        }
        if (captionHtml) inner += '<span class="print-doc-caption-text">' + captionHtml + '</span>';
        cap.innerHTML = inner;
        wrap.appendChild(cap);
        if (captionHtml) stats.captions += 1;
      }
      stats.figures += 1;
    } catch (e) { /* skip this figure */ }
  }

  // ── 3. Tables: caption + number + long-table flow ────────────────────
  const tables = Array.from(root.querySelectorAll('table'));
  let tblN = 0;
  for (const t of tables) {
    try {
      if (t.classList.contains('diff-table')) continue;
      if (t.closest('.diff-view, .diff-container, .print-footer')) continue;
      if (t.querySelector(':scope > caption.print-doc-table-caption')) continue;
      // Caption may sit in the paragraph directly above or directly below.
      let captionHtml = takeCaption(t.previousElementSibling, CAPTION_TBL);
      if (captionHtml === null) captionHtml = takeCaption(t.nextElementSibling, CAPTION_TBL);
      tblN += 1;
      t.setAttribute('data-print-table', String(tblN));
      if (numbering.tables || captionHtml) {
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
      const h = t.getBoundingClientRect().height;
      if (h > pageH * longFrac) {
        t.classList.add('print-doc-table-flow');
        stats.flowTables += 1;
      }
      stats.tables += 1;
    } catch (e) { /* skip this table */ }
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
        '<div style="width:100%;font-size:7px;'
        "font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;"
        'color:#57606a;padding:0 16mm;box-sizing:border-box;">'
        '<div style="border-top:0.5px solid #c8ccd1;padding-top:4px;'
        'display:flex;align-items:center;">'
        f'<img src="{get_logo_data_uri()}" '
        'style="height:14px;width:auto;margin-right:7px;opacity:0.85;"/>'
        '<div style="flex:1;min-width:0;line-height:1.5;overflow:hidden;">'
        f'<div style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{doc_line}</div>'
        f'<div style="color:#a8aeb8;font-size:6.2px;">{credit_line}</div>'
        "</div>"
        '<div style="flex:none;margin-left:12px;font-variant-numeric:tabular-nums;">'
        'Page <span class="pageNumber"></span> of '
        '<span class="totalPages"></span>'
        "</div></div></div>"
    )
