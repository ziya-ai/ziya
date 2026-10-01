#!/usr/bin/env python3
"""
Document-aesthetics harness: render ONE authored IR document through the real
document PDF pipeline, rasterise it, and measure it against the print-ready
rubric (R1..R10) used by the doc-aesthetics improvement loop.

Subcommands::

    python3 -m tests.export_fidelity.doc_aesthetics render  --ir DOC.md --out DIR [--port 6969]
    python3 -m tests.export_fidelity.doc_aesthetics metrics --pdf DOC.pdf --ir DOC.md [--out metrics.json]
    python3 -m tests.export_fidelity.doc_aesthetics compare --a A/metrics.json --b B/metrics.json

RENDER PATH.  ``render`` runs OUT OF PROCESS: a fresh interpreter imports
``app.services.pdf_exporter.export_document_pdf`` and the decoration module,
so edits to ``app/utils/document_print_decor.py`` take effect immediately even
though the long-running Ziya server (which only serves the ``/print`` route
here) never reloads.  The session singleton is torn down after the render,
exactly as ``render_harness.render_pdf`` does for conversations.

Metric functions are pure (numpy arrays / word dicts in, numbers out) so the
unit tests in ``test_doc_aesthetics.py`` exercise them on synthetic input with
no browser.  The existing check functions in :mod:`checks` are reused via a
``RenderedDocument`` built by :func:`render_harness.load_pdf`.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import math
import re
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

import logging as _logging
# pdfminer warns once per glyph run about Type3 fonts without a FontBBox
# (Chromium emits those for SVG text); it is noise for this harness.
for _name in ("pdfminer", "pdfminer.pdffont", "pdfplumber"):
    _logging.getLogger(_name).setLevel(_logging.ERROR)

DEFAULT_DPI = 110.0
_PT_PER_MM = 72.0 / 25.4

# Fenced blocks that render as figures (diagrams/charts).
DIAGRAM_LANGS = {
    "mermaid", "vega-lite", "vega", "graphviz", "dot", "d3", "plotly",
    "chart", "drawio", "d2", "packet", "railroad", "wavedrom", "chemfig",
    "joint", "network", "chord", "flamegraph",
}

RAW_MARKUP_PATTERNS = {
    "html_comment": re.compile(r"<!--"),
    "triple_backtick": re.compile(r"```"),
    "private_use": re.compile(r"[\uE000-\uF8FF]"),
    "template_braces": re.compile(r"\{\{"),
}

_PAGE_N_OF_M_RE = re.compile(r"Page\s*(\d+)\s*of\s*(\d+)")


# ---------------------------------------------------------------------------
# Pure raster metrics
# ---------------------------------------------------------------------------

def white_mask(rgb: np.ndarray, tol: int = 12) -> np.ndarray:
    return np.all(rgb.astype(int) >= (255 - tol), axis=2)


def crop_box(rgb: np.ndarray, box: Optional[Tuple[int, int, int, int]]) -> np.ndarray:
    """Crop ``rgb`` to ``box`` = (x0, y0, x1, y1) px; ``None`` = whole array."""
    if box is None:
        return rgb
    x0, y0, x1, y1 = box
    return rgb[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]


def ink_fraction(rgb: np.ndarray, box=None) -> float:
    """Non-white pixels over the (printable) box area."""
    a = crop_box(rgb, box)
    if a.size == 0:
        return 0.0
    return float((~white_mask(a)).mean())


def _white_rows(a: np.ndarray, row_white_frac: float = 0.995) -> np.ndarray:
    return white_mask(a).mean(axis=1) >= row_white_frac


def ink_row_extent(rgb: np.ndarray, box=None) -> Optional[Tuple[int, int]]:
    """(first_ink_row, last_ink_row) inside the box, or None when blank."""
    a = crop_box(rgb, box)
    rows = np.nonzero(~_white_rows(a))[0]
    if rows.size == 0:
        return None
    return int(rows[0]), int(rows[-1])


def ink_height_fraction(rgb: np.ndarray, box=None) -> float:
    """Fraction of the box height from its top to the last ink row ('used')."""
    a = crop_box(rgb, box)
    ext = ink_row_extent(a)
    if ext is None or a.shape[0] == 0:
        return 0.0
    return float(ext[1] + 1) / a.shape[0]


def largest_white_band_fraction(rgb: np.ndarray, box=None, *, interior: bool = False) -> float:
    """Tallest run of consecutive white rows over the box height.

    ``interior=True`` only counts bands strictly BETWEEN the first and last ink
    rows (a band trailing the last paragraph of a page is 'unused page', which
    R1's ink-usage clause measures separately).
    """
    a = crop_box(rgb, box)
    h = a.shape[0]
    if h == 0:
        return 0.0
    white = _white_rows(a)
    if interior:
        ext = ink_row_extent(a)
        if ext is None:
            return 0.0
        white = white[ext[0]:ext[1] + 1]
    best = cur = 0
    for v in white:
        if v:
            cur += 1
            if cur > best:
                best = cur
        else:
            cur = 0
    return float(best) / h


def edge_ink(rgb: np.ndarray, box=None, *, edge: str = "bottom", band_frac: float = 0.02) -> bool:
    """True when the box's top/bottom ``band_frac`` strip contains ink."""
    a = crop_box(rgb, box)
    h = a.shape[0]
    n = max(1, int(round(h * band_frac)))
    strip = a[h - n:] if edge == "bottom" else a[:n]
    return bool((~white_mask(strip)).any())


def figure_bisected(cur_fig_bbox_pts: Optional[Sequence[float]],
                    next_fig_bbox_pts: Optional[Sequence[float]],
                    box_pts: Sequence[float], *, tol_pts: float = 18.0) -> bool:
    """A figure region touching the bottom of the printable box that continues
    at the top of the next page's box.  bboxes are (x0, top, x1, bottom) pts."""
    if not cur_fig_bbox_pts or not next_fig_bbox_pts:
        return False
    _bx0, btop, _bx1, bbot = box_pts
    return (cur_fig_bbox_pts[3] >= bbot - tol_pts
            and next_fig_bbox_pts[1] <= btop + tol_pts)


# ---------------------------------------------------------------------------
# Pure text-layer metrics
# ---------------------------------------------------------------------------

def group_lines(words: List[Dict[str, Any]], *, ytol: float = 2.5) -> List[Dict[str, Any]]:
    """Group pdfplumber words into lines: {top, bottom, x0, x1, size, text, n}."""
    ws = sorted(words, key=lambda w: (round(float(w.get("top", 0)), 1), float(w.get("x0", 0))))
    lines: List[Dict[str, Any]] = []
    for w in ws:
        top = float(w.get("top", 0))
        if lines and abs(lines[-1]["top"] - top) <= ytol:
            ln = lines[-1]
            ln["words"].append(w)
        else:
            lines.append({"top": top, "words": [w]})
    out = []
    for ln in lines:
        wl = sorted(ln["words"], key=lambda w: float(w.get("x0", 0)))
        sizes = [float(w.get("size", 0) or 0) for w in wl]
        out.append({
            "top": min(float(w.get("top", 0)) for w in wl),
            "bottom": max(float(w.get("bottom", 0)) for w in wl),
            "x0": min(float(w.get("x0", 0)) for w in wl),
            "x1": max(float(w.get("x1", 0)) for w in wl),
            "size": max(sizes) if sizes else 0.0,
            "text": " ".join(str(w.get("text", "")) for w in wl),
            "n": len(wl),
        })
    return out


def body_font_size(words: List[Dict[str, Any]]) -> Optional[float]:
    """Modal font size weighted by word count (rounded to 0.1pt)."""
    c = Counter(round(float(w.get("size", 0) or 0), 1) for w in words if w.get("size"))
    if not c:
        return None
    return float(c.most_common(1)[0][0])


def footer_font_min_pt(words: List[Dict[str, Any]], box_pts) -> Optional[float]:
    """Smallest glyph size (pt) among words below the printable box — the
    running footer.  None when the page has no footer text (d015)."""
    sizes = [float(w.get("size", 0) or 0) for w in words if w["top"] > box_pts[3]]
    sizes = [v for v in sizes if v > 0]
    return round(min(sizes), 2) if sizes else None


def heading_orphan(lines: List[Dict[str, Any]], body_size: Optional[float],
                   box_top: float, box_bottom: float, *,
                   zone_frac: float = 0.08, ratio: float = 1.12) -> Optional[str]:
    """Return the text of a heading line stranded in the bottom ``zone_frac`` of
    the box with no body line after it on the page, else None."""
    if not lines or not body_size:
        return None
    zone_top = box_bottom - zone_frac * (box_bottom - box_top)
    inbox = [l for l in lines if l["top"] >= box_top - 1 and l["bottom"] <= box_bottom + 1]
    for i, ln in enumerate(inbox):
        if ln["size"] < body_size * ratio or ln["top"] < zone_top:
            continue
        after = [l for l in inbox[i + 1:] if abs(l["size"] - body_size) <= 0.6]
        if not after:
            return ln["text"][:80]
    return None


# An authored figure TITLE: number, separator, then title text.  A bare
# "Figure 3" (the decorator's auto label under a drawing) is not a title.
_FIG_TITLE_LINE_RE = re.compile(r"^(?:Figure|Fig\.)\s*\d+[a-z]?\s*[:.\u2014\u2013-]\s*\S", re.I)


def figure_title_orphan(lines: List[Dict[str, Any]], figs: Sequence[Sequence[float]],
                        next_figs: Sequence[Sequence[float]], box_pts: Sequence[float],
                        next_box_pts: Optional[Sequence[float]], *, tail_lines: int = 6,
                        next_top_frac: float = 0.40) -> Optional[str]:
    """Return the text of an authored figure title ('Figure N — …') stranded among
    the last ``tail_lines`` text lines of the page (inline code in a title
    splits it into extra baseline-offset lines, hence 6) with no figure below it on the
    page, while the next page opens with a figure (top within ``next_top_frac``
    of the box), else None.  Prose that merely mentions a figure ('Figure 3
    shows …') does not match: the number must be followed by a separator."""
    if not lines or not next_figs or not next_box_pts:
        return None
    inbox = [l for l in lines if l["top"] >= box_pts[1] - 1 and l["bottom"] <= box_pts[3] + 1]
    nb_top, nb_bot = float(next_box_pts[1]), float(next_box_pts[3])
    if not any(float(f[1]) <= nb_top + next_top_frac * (nb_bot - nb_top) for f in next_figs):
        return None
    for ln in inbox[-tail_lines:]:
        if not _FIG_TITLE_LINE_RE.match(ln["text"].strip()):
            continue
        if any(float(f[1]) >= ln["top"] - 1 for f in figs):
            continue
        return ln["text"][:80]
    return None


def raw_markup_hits(text: str) -> Dict[str, int]:
    return {k: len(p.findall(text or "")) for k, p in RAW_MARKUP_PATTERNS.items()}


def cell_wrapped_lines(cell_words: List[Dict[str, Any]], ytol: float = 2.5) -> int:
    return len(group_lines(cell_words, ytol=ytol))


# ---------------------------------------------------------------------------
# IR analysis
# ---------------------------------------------------------------------------

_FENCE_OPEN_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})[ \t]*([^\s`]*)")


def analyze_ir(ir_text: str) -> Dict[str, Any]:
    """Word count (prose only), fenced figure count, pagebreaks, margin, meta."""
    from app.utils.document_ir import parse_document
    meta, body = parse_document(ir_text)
    prose: List[str] = []
    figures = 0
    langs: List[str] = []
    in_fence: Optional[str] = None
    for line in body.split("\n"):
        m = _FENCE_OPEN_RE.match(line)
        if in_fence is None:
            if m:
                in_fence = m.group(1)[0] * 3
                lang = (m.group(2) or "").strip().lower()
                if lang in DIAGRAM_LANGS:
                    figures += 1
                    langs.append(lang)
                continue
            prose.append(line)
        else:
            if line.strip().startswith(in_fence):
                in_fence = None
    prose_text = "\n".join(prose)
    prose_text = re.sub(r"<!--.*?-->", " ", prose_text, flags=re.DOTALL)
    words = len(re.findall(r"[A-Za-z0-9][\w'’.,%/–-]*", prose_text))
    pagebreaks = len(re.findall(r"<!--\s*ziya:pagebreak\s*-->", body))
    return {
        "meta": meta,
        "words": words,
        "figure_count": figures,
        "figure_langs": langs,
        "pagebreaks": pagebreaks,
    }


def target_pages(words: int, figures: int) -> float:
    """R8 target = ceil(words/600) + figures*0.5 + 1 (title)."""
    return math.ceil(words / 600.0) + figures * 0.5 + 1


def _margin_mm(meta: Dict[str, Any]) -> Dict[str, float]:
    from app.utils.document_print_decor import DEFAULT_DOCUMENT_MARGIN
    m = dict(DEFAULT_DOCUMENT_MARGIN)
    m.update(((meta or {}).get("page") or {}).get("margin") or {})
    out = {}
    for side, v in m.items():
        v = str(v).strip().lower()
        num = float(re.sub(r"[a-z]+$", "", v) or 0)
        if v.endswith("cm"):
            num *= 10
        elif v.endswith("in"):
            num *= 25.4
        elif v.endswith("px"):
            num *= 25.4 / 96
        out[side] = num
    return out


# ---------------------------------------------------------------------------
# PDF analysis
# ---------------------------------------------------------------------------

def _figure_clusters(plumb_page, box_pts, table_bboxes, *, gap_pts: float = 36.0):
    """Cluster vector graphics (curves, non-table rects/lines, images) inside the
    printable box into figure bboxes (x0, top, x1, bottom)."""
    bx0, btop, bx1, bbot = box_pts
    objs = []

    def in_table(o):
        cy = (o["top"] + o["bottom"]) / 2
        cx = (o["x0"] + o["x1"]) / 2
        return any(t[0] - 2 <= cx <= t[2] + 2 and t[1] - 2 <= cy <= t[3] + 2
                   for t in table_bboxes)

    for kind in ("curves", "rects", "lines", "images"):
        for o in getattr(plumb_page, kind, []) or []:
            try:
                d = {"x0": float(o["x0"]), "x1": float(o["x1"]),
                     "top": float(o["top"]), "bottom": float(o["bottom"]), "kind": kind}
            except Exception:
                continue
            if d["bottom"] < btop - 2 or d["top"] > bbot + 2:
                continue  # footer/header margin art
            if kind in ("rects", "curves") and _is_whiteish(o.get("non_stroking_color")) \
                    and not o.get("stroke"):
                # page background / code-block fills; unstroked near-white
                # curves are label backgrounds / frame corners (d017: two
                # #f3f4f6 curves hanging off the paper flagged p5 as clipped).
                continue
            if kind == "rects" and (d["x1"] - d["x0"]) >= 0.97 * (bx1 - bx0) and \
                    (d["bottom"] - d["top"]) >= 0.9 * (bbot - btop):
                continue  # full-box background
            if kind != "curves" and in_table(d):
                continue
            # Full-width hairlines (hr, heading rules) are not figures.
            if kind in ("lines", "rects") and (d["bottom"] - d["top"]) < 3 and \
                    (d["x1"] - d["x0"]) > 0.8 * (bx1 - bx0):
                continue
            # Code-block / blockquote backgrounds: one big rect alone; kept but
            # a cluster needs several objects to count.
            objs.append(d)
    objs.sort(key=lambda d: d["top"])
    clusters: List[Dict[str, Any]] = []
    for d in objs:
        if clusters and d["top"] <= clusters[-1]["bottom"] + gap_pts:
            c = clusters[-1]
            c["x0"] = min(c["x0"], d["x0"]); c["x1"] = max(c["x1"], d["x1"])
            c["bottom"] = max(c["bottom"], d["bottom"]); c["n"] += 1
            c["kinds"][d["kind"]] = c["kinds"].get(d["kind"], 0) + 1
        else:
            clusters.append({**d, "n": 1, "kinds": {d["kind"]: 1}})
    figs = []
    for c in clusters:
        h = c["bottom"] - c["top"]
        if c["kinds"].get("images") or (c["n"] >= 40 and h >= 60 and c["kinds"].get("curves", 0) + c["kinds"].get("rects", 0) >= 4):
            figs.append([round(c["x0"], 1), round(c["top"], 1), round(c["x1"], 1),
                         round(c["bottom"], 1), c["n"]])
    return figs


def figure_text_overflow_x(chars, figs, box_pts, tol: float = 3.0) -> bool:
    """True if a glyph inside a figure's vertical span is cut at the paper edge
    (starts left of / ends right of the printable box by more than ``tol``).
    This is what a genuinely clipped chart title/subtitle looks like."""
    bx0, _bt, bx1, _bb = box_pts
    for c in chars or []:
        try:
            cy = (float(c["top"]) + float(c["bottom"])) / 2
            x0, x1 = float(c["x0"]), float(c["x1"])
        except Exception:
            continue
        if not str(c.get("text", "x")).strip():
            continue
        if any(f[1] <= cy <= f[3] for f in figs) and (x0 < bx0 - tol or x1 > bx1 + tol):
            return True
    return False


def figure_overflow_x(figs, chars, box_pts, tol: float = 3.0) -> bool:
    """A figure's drawing (filtered clusters) or its glyphs extend past the
    printable box — i.e. the chart is clipped at the paper edge (R5)."""
    if any(f[0] < box_pts[0] - tol or f[2] > box_pts[2] + tol for f in figs):
        return True
    return figure_text_overflow_x(chars, figs, box_pts, tol)


def pdf_figure_overflow(pdf_bytes: bytes, ir_text: str) -> List[bool]:
    """Per-page ``figure_overflow_x`` straight from the PDF (no rasterising)."""
    import pdfplumber
    mm = _margin_mm(analyze_ir(ir_text)["meta"])
    out: List[bool] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as plumb:
        for pp in plumb.pages:
            W, H = float(pp.width), float(pp.height)
            box = (mm["left"] * _PT_PER_MM, mm["top"] * _PT_PER_MM,
                   W - mm["right"] * _PT_PER_MM, H - mm["bottom"] * _PT_PER_MM)
            figs = _figure_clusters(pp, box, [t[0] for t in _tables(pp, box)])
            out.append(figure_overflow_x(figs, pp.chars, box))
    return out


def _is_whiteish(color) -> bool:
    try:
        vals = list(color) if isinstance(color, (list, tuple)) else [color]
        vals = [float(v) for v in vals]
    except Exception:
        return False
    if len(vals) == 4:  # CMYK
        return all(v <= 0.05 for v in vals)
    return bool(vals) and all(v >= 0.95 for v in vals)


def detect_rule_tables(rects: List[Dict[str, Any]], *, min_cols: int = 2,
                       max_row_gap: float = 260.0) -> List[Dict[str, Any]]:
    """Find report-style tables from their per-cell bottom-border rules.

    The document stylesheet draws every cell's ``border-bottom`` as its own
    thin rect whose x-extent IS the column, so a table is a vertical run of
    rule-rows sharing the same column x-set.  Pure function over rect dicts
    (x0, x1, top, bottom).  Returns [{"bbox", "cols", "rules"}].
    """
    thin = [r for r in rects if (r["bottom"] - r["top"]) <= 2.5 and (r["x1"] - r["x0"]) >= 8]
    rows: Dict[float, List[Tuple[float, float]]] = {}
    for r in thin:
        key = round(r["top"])
        match = next((k for k in rows if abs(k - key) <= 1.5), None)
        rows.setdefault(match if match is not None else key, []).append(
            (round(r["x0"]), round(r["x1"])))
    rule_rows = sorted(
        (k, tuple(sorted(set(v)))) for k, v in rows.items() if len(set(v)) >= min_cols)
    tables: List[Dict[str, Any]] = []
    for top, cols in rule_rows:
        if tables and tables[-1]["cols"] == cols and top - tables[-1]["rules"][-1] <= max_row_gap:
            tables[-1]["rules"].append(top)
        else:
            tables.append({"cols": cols, "rules": [top]})
    out = []
    for t in tables:
        if len(t["rules"]) < 2:
            continue
        x0 = min(c[0] for c in t["cols"]); x1 = max(c[1] for c in t["cols"])
        out.append({"bbox": (x0, t["rules"][0], x1, t["rules"][-1]),
                    "cols": list(t["cols"]), "rules": t["rules"]})
    return out


def _tables(plumb_page, box_pts):
    """Report-table detection from cell rules; returns [(bbox, cells_words)].

    Only BODY cells (between consecutive rules) are returned: header cells sit
    above the first rule with no top bound, and never wrap to >=5 lines in
    practice.  pdfplumber's generic ``find_tables`` is not used: the page's
    full-bleed white background rects make it report the whole page as one
    table.
    """
    rects = []
    for r in plumb_page.rects or []:
        if _is_whiteish(r.get("non_stroking_color")):
            continue
        rects.append({"x0": float(r["x0"]), "x1": float(r["x1"]),
                      "top": float(r["top"]), "bottom": float(r["bottom"])})
    words = plumb_page.extract_words(extra_attrs=["size"]) or []
    out = []
    for t in detect_rule_tables(rects):
        cells_words = []
        for a, b in zip(t["rules"], t["rules"][1:]):
            for (cx0, cx1) in t["cols"]:
                cw = [w for w in words
                      if cx0 - 1 <= (w["x0"] + w["x1"]) / 2 <= cx1 + 1
                      and a < (w["top"] + w["bottom"]) / 2 < b]
                cells_words.append(cw)
        out.append((t["bbox"], cells_words))
    return out


def glyph_size_pt(char: Dict[str, Any]) -> Optional[float]:
    """True point size of one pdfplumber char.

    Upright glyphs report it as ``size``.  For a quarter-turn glyph (rotated
    +/-90 degrees: matrix a,d ~ 0) pdfminer reports the *advance* as ``size``;
    the glyph box spans descent..descent+fontsize across the rotated axis, so
    its horizontal extent ``x1 - x0`` is the font size (d032).  Any other angle
    returns None (not measurable this way)."""
    if char.get("upright", True):
        try:
            return float(char.get("size"))
        except Exception:
            return None
    m = tuple(char.get("matrix") or ())
    if len(m) < 4:
        return None
    a, b, c, d = (abs(float(v)) for v in m[:4])
    big = max(b, c)
    if big <= 0 or a >= 0.05 * big or d >= 0.05 * big:
        return None
    try:
        return float(char["x1"]) - float(char["x0"])
    except Exception:
        return None


def compute_metrics(pdf_bytes: bytes, ir_text: str, *, dpi: float = DEFAULT_DPI,
                    doc=None) -> Dict[str, Any]:
    import pdfplumber
    from tests.export_fidelity import render_harness, checks

    ir = analyze_ir(ir_text)
    meta = ir["meta"]
    mm = _margin_mm(meta)
    if doc is None:
        doc = render_harness.load_pdf(pdf_bytes, dpi=dpi, rev_byteorder=True)
    scale = dpi / 72.0
    n = doc.page_count

    pages: List[Dict[str, Any]] = []
    fig_bboxes: List[List[List[float]]] = []
    all_words: List[Dict[str, Any]] = []
    per_page_words: List[List[Dict[str, Any]]] = []
    box_pts_list = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as plumb:
        for i, pp in enumerate(plumb.pages):
            W, H = float(pp.width), float(pp.height)
            box_pts = (mm["left"] * _PT_PER_MM, mm["top"] * _PT_PER_MM,
                       W - mm["right"] * _PT_PER_MM, H - mm["bottom"] * _PT_PER_MM)
            box_pts_list.append(box_pts)
            words = pp.extract_words(extra_attrs=["size", "fontname"]) or []
            per_page_words.append(words)
            all_words.extend([w for w in words if box_pts[1] - 1 <= w["top"] <= box_pts[3] + 1])
            tables = _tables(pp, box_pts)
            figs = _figure_clusters(pp, box_pts, [t[0] for t in tables])
            fig_bboxes.append(figs)
            rot_chars = []
            for c in pp.chars or []:
                if c.get("upright", True) or not str(c.get("text", "")).strip():
                    continue
                try:
                    rot_chars.append({"x0": float(c["x0"]), "x1": float(c["x1"]),
                                      "top": float(c["top"]), "bottom": float(c["bottom"]),
                                      "size": c.get("size"), "upright": False,
                                      "matrix": tuple(c.get("matrix") or ())})
                except Exception:
                    continue
            pages.append({"_tables": tables, "_box_pts": box_pts, "_W": W, "_H": H,
                          "_rot_chars": rot_chars,
                          "_overflow_x": figure_overflow_x(figs, pp.chars, box_pts)})

    body_size = body_font_size(all_words)
    sizes_seen = sorted({round(float(w.get("size", 0) or 0), 1) for w in all_words}, reverse=True)

    out_pages = []
    for i, p in enumerate(doc.pages):
        box_pts = pages[i]["_box_pts"]
        box_px = tuple(int(round(v * scale)) for v in box_pts)
        words = per_page_words[i]
        inbox = [w for w in words if box_pts[1] - 1 <= w["top"] <= box_pts[3] + 1]
        footer_words = [w for w in words if w["top"] > box_pts[3]]
        header_words = [w for w in words if w["bottom"] < box_pts[1]]
        footer_text = " ".join(w["text"] for w in footer_words)
        m = _PAGE_N_OF_M_RE.search(footer_text)
        lines = group_lines(inbox)
        orphan = heading_orphan(lines, body_size, box_pts[1], box_pts[3])
        narrow = 0
        max_cell_lines = 0
        for _bbox, cells in pages[i]["_tables"]:
            for cw in cells:
                k = cell_wrapped_lines(cw) if cw else 0
                max_cell_lines = max(max_cell_lines, k)
                if k >= 5:
                    narrow += 1
        figs = fig_bboxes[i]
        fig_text_min = None
        for f in figs:
            # Rotated glyphs (axis titles) report their advance as 'size';
            # only upright words give a real point size.
            fw = [w for w in inbox if w.get("upright", True)
                  and f[0] - 2 <= w["x0"] and w["x1"] <= f[2] + 2
                  and f[1] - 2 <= w["top"] and w["bottom"] <= f[3] + 2]
            if fw:
                s = min(float(w.get("size", 99) or 99) for w in fw)
                fig_text_min = s if fig_text_min is None else min(fig_text_min, s)
            # d032: quarter-turn glyphs (rotated axis titles, landscape
            # figures) measured from their box, not their advance.
            for c in pages[i]["_rot_chars"]:
                if not (f[0] - 2 <= c["x0"] and c["x1"] <= f[2] + 2
                        and f[1] - 2 <= c["top"] and c["bottom"] <= f[3] + 2):
                    continue
                s = glyph_size_pt(c)
                if s is not None and s > 0.5:
                    fig_text_min = s if fig_text_min is None else min(fig_text_min, s)
        bisect = False
        if i + 1 < n and figs and fig_bboxes[i + 1]:
            bisect = figure_bisected(figs[-1][:4], fig_bboxes[i + 1][0][:4], box_pts)
        title_orphan = figure_title_orphan(
            lines, figs, fig_bboxes[i + 1] if i + 1 < n else [],
            box_pts, pages[i + 1]["_box_pts"] if i + 1 < n else None)
        # body line lengths (full-measure lines only)
        box_w = box_pts[2] - box_pts[0]
        full_lines = [l for l in lines if body_size and abs(l["size"] - body_size) <= 0.3
                      and (l["x1"] - l["x0"]) >= 0.85 * box_w]
        line_chars = [len(l["text"]) for l in full_lines]
        next_first = None
        if i + 1 < n:
            nl = group_lines([w for w in per_page_words[i + 1]
                              if pages[i + 1]["_box_pts"][1] - 1 <= w["top"] <= pages[i + 1]["_box_pts"][3] + 1])
            if nl:
                next_first = nl[0]
        text = p.text or ""
        out_pages.append({
            "page": i + 1,
            "ink_fraction": round(ink_fraction(p.rgb, box_px), 4),
            "ink_height_fraction": round(ink_height_fraction(p.rgb, box_px), 4),
            "largest_white_band_fraction": round(largest_white_band_fraction(p.rgb, box_px), 4),
            "interior_white_band_fraction": round(largest_white_band_fraction(p.rgb, box_px, interior=True), 4),
            "has_figure": bool(figs),
            "figure_bboxes_pts": figs,
            "figure_min_text_pt": fig_text_min,
            "figure_max_height_frac": round(max(((f[3] - f[1]) / (box_pts[3] - box_pts[1]) for f in figs), default=0.0), 3),
            "figure_bisected": bisect,
            # A chart whose drawing extends past the printable box (a long
            # title/subtitle wider than the plot) is clipped at the paper edge.
            # Drawing objects (near-white unstroked fills excluded) OR glyphs
            # cut at the paper edge inside a figure's vertical span (d017).
            "figure_overflow_x": pages[i]["_overflow_x"],
            "heading_orphan": orphan,
            "figure_title_orphan": title_orphan,
            "table_count": len(pages[i]["_tables"]),
            "table_narrow_cells": narrow,
            "table_max_cell_lines": max_cell_lines,
            "raw_markup_hits": raw_markup_hits(text),
            "footer_present": bool(footer_words),
            "footer_font_min_pt": footer_font_min_pt(words, box_pts),
            "footer_page_n_of_m": [int(m.group(1)), int(m.group(2))] if m else None,
            "header_present": bool(header_words),
            "word_count": len(inbox),
            "body_line_chars_median": (statistics.median(line_chars) if line_chars else None),
            "body_full_lines": len(line_chars),
            "next_page_starts_with_heading": bool(next_first and body_size and next_first["size"] >= body_size * 1.12),
        })

    # Reused checks
    ws = checks.check_whitespace_waste(doc)
    pbs = checks.check_page_break_sanity(doc)
    ffq = checks.check_figure_flow_quality(doc)
    outline = checks.check_pdf_outline(doc)
    fonts = checks.check_font_embedding(doc)
    tq = checks.check_text_quality(doc, expected_phrases=[])
    md = checks.check_document_metadata(doc, expected_title_substr=(meta.get("title") or None))

    words = ir["words"]
    tp = target_pages(words, ir["figure_count"])
    density = n / tp if tp else None

    # Outline vs heading tree (h2/h1 lines in IR)
    ir_heads = re.findall(r"^(#{1,3})\s+(.+?)\s*$", _strip_fences(ir_text), flags=re.MULTILINE)
    outline_titles = outline.measurements.get("titles", [])

    doc_metrics = {
        "page_count": n,
        "words": words,
        "figure_count": ir["figure_count"],
        "figure_langs": ir["figure_langs"],
        "pagebreaks": ir["pagebreaks"],
        "target_pages": tp,
        "density_ratio": round(density, 3) if density else None,
        "body_font_pt": body_size,
        "font_sizes_pt": sizes_seen[:12],
        "margins_mm": mm,
        "outline_ok": outline.passed,
        "outline": outline.measurements,
        "ir_heading_count": len(ir_heads),
        "fonts_embedded": fonts.passed,
        "fonts": {k: fonts.measurements.get(k) for k in ("n_fonts", "n_embedded", "n_non_embedded", "non_embedded_fonts")},
        "metadata": md.measurements,
        "metadata_ok": document_metadata_ok(md.passed, md.measurements, meta),
        "runt_last_page": runt_last_page(out_pages, pagebreaks=ir.get("pagebreaks", 0)),
        "reused_checks": {
            "whitespace_waste": {"passed": ws.passed, "failures": ws.failures},
            "page_break_sanity": {"passed": pbs.passed, "failures": pbs.failures},
            "figure_flow_quality": {"passed": ffq.passed, "failures": ffq.failures,
                                    "lonely_figure_pages": ffq.measurements.get("lonely_figure_pages")},
            "text_quality": {"passed": tq.passed, "failures": tq.failures},
        },
    }
    doc_metrics["rubric"] = build_rubric(out_pages, doc_metrics, meta)
    return {"document": doc_metrics, "pages": out_pages}


def _strip_fences(text: str) -> str:
    return re.sub(r"^```.*?^```", "", text, flags=re.DOTALL | re.MULTILINE)


def document_metadata_ok(check_passed: bool, measurements: Dict[str, Any],
                         meta: Dict[str, Any]) -> bool:
    """Info dict is sane for a DOCUMENT export: the generic metadata check
    passes, a front-matter author is carried through, and /Subject does not
    call the document a conversation (d024)."""
    if not check_passed:
        return False
    if meta.get("author") and measurements.get("author") != meta.get("author"):
        return False
    return "conversation" not in (measurements.get("subject") or "").lower()


RUNT_WORDS = 80
RUNT_INK = 0.15


def runt_last_page(pages: List[Dict[str, Any]], *, pagebreaks: int = 0) -> bool:
    """The document ends on a near-empty page that was not forced by a page
    break (d014): < RUNT_WORDS words and < RUNT_INK of the box height used."""
    if len(pages) < 2:
        return False
    last, prev = pages[-1], pages[-2]
    if pagebreaks > 0 and prev.get("next_page_starts_with_heading"):
        return False
    return (last.get("word_count", 0) < RUNT_WORDS
            and last.get("ink_height_fraction", 1.0) < RUNT_INK)


def build_rubric(pages: List[Dict[str, Any]], d: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    n = len(pages)
    R: Dict[str, Dict[str, Any]] = {}

    # R1 ink usage
    low, bands = [], []
    for p in pages:
        last = p["page"] == n
        forced = d.get("pagebreaks", 0) > 0 and p["next_page_starts_with_heading"]
        if not last and not forced and p["ink_height_fraction"] < 0.40:
            low.append(f"p{p['page']} used={p['ink_height_fraction']:.2f}")
        if p["interior_white_band_fraction"] > 0.25:
            bands.append(f"p{p['page']} band={p['interior_white_band_fraction']:.2f}")
    R["R1"] = {"pass": not low and not bands,
               "evidence": f"under-used pages: {low or 'none'}; interior white bands >25%: {bands or 'none'}"}

    # R2 figures (partial: bisect + min text + height; caption co-location needs eyes)
    bis = [p["page"] for p in pages if p["figure_bisected"]]
    small = [f"p{p['page']} {p['figure_min_text_pt']}pt" for p in pages
             if p["figure_min_text_pt"] is not None and p["figure_min_text_pt"] < 6.5]
    tall = [p["page"] for p in pages if p["figure_max_height_frac"] > 1.0]
    bad = bis or small or tall
    R["R2"] = {"pass": False if bad else None,
               "evidence": f"bisected figures on pages {bis or 'none'}; figure text <6.5pt: {small or 'none'}; "
                           f"taller than box: {tall or 'none'} (caption co-location needs eyes)"}

    # R3 headings
    orph = [f"p{p['page']}: {p['heading_orphan']!r}" for p in pages if p["heading_orphan"]]
    body = d.get("body_font_pt")
    body_ok = body is not None and 10.0 <= body <= 11.0
    R["R3"] = {"pass": (not orph and body_ok),
               "evidence": f"orphan headings: {orph or 'none'}; body={body}pt (want 10-11); sizes seen={d.get('font_sizes_pt')}"}

    # R4 tables (partial)
    narrow = [f"p{p['page']}: {p['table_narrow_cells']} cells (max {p['table_max_cell_lines']} lines)"
              for p in pages if p["table_narrow_cells"]]
    R["R4"] = {"pass": False if narrow else None,
               "evidence": f"cells wrapping >=5 lines: {narrow or 'none'}; header repeat / zebra need eyes"}

    over = [p["page"] for p in pages if p.get("figure_overflow_x")]
    R["R5"] = {"pass": False if over else None,
               "evidence": f"figure drawing extends past printable box (clipped) on pages {over or 'none'}; "
                           f"tick overlap / legend placement need eyes"}

    # R6 raw markup
    hits = {}
    for p in pages:
        for k, v in p["raw_markup_hits"].items():
            if v:
                hits.setdefault(k, []).append(p["page"])
    R["R6"] = {"pass": not hits, "evidence": f"raw markup hits: {hits or 'none'}"}

    # R7 running chrome + outline + metadata
    no_footer = [p["page"] for p in pages if not p["footer_present"]]
    wrong_nm = [p["page"] for p in pages
                if not p["footer_page_n_of_m"] or p["footer_page_n_of_m"] != [p["page"], n]]
    no_header = [p["page"] for p in pages if not p["header_present"]]
    ok7 = not no_footer and not wrong_nm and d["outline_ok"] and d["metadata_ok"]
    md = d.get("metadata", {})
    R["R7"] = {"pass": ok7,
               "evidence": f"pages without footer: {no_footer or 'none'}; wrong 'Page N of M': {wrong_nm or 'none'}; "
                           f"pages without running header: {no_header or 'none'} (footer-only chrome); "
                           f"footer_font_min_pt={min((p.get('footer_font_min_pt') or 99) for p in pages) if pages else None}; "
                           f"outline_ok={d['outline_ok']} items={d['outline'].get('n_items')} vs IR headings={d.get('ir_heading_count')}; "
                           f"title={md.get('title')!r} author={md.get('author')!r} subject={md.get('subject')!r} metadata_ok={d['metadata_ok']}"}

    # R8 density
    ratio = d.get("density_ratio")
    R["R8"] = {"pass": (ratio is not None and ratio <= 1.25),
               "evidence": f"pages={d['page_count']} target={d['target_pages']} (words={d['words']}, figures={d['figure_count']}) ratio={ratio}; runt_last_page={d.get('runt_last_page')}"}

    # R9 line length + margins
    meds = [p["body_line_chars_median"] for p in pages if p["body_line_chars_median"]]
    med = statistics.median(meds) if meds else None
    mm = d.get("margins_mm", {})
    margins_ok = all(14 <= v <= 20 for v in mm.values()) if mm else False
    R["R9"] = {"pass": (med is not None and 60 <= med <= 95 and margins_ok),
               "evidence": f"median full-line chars={med}; margins_mm={mm}"}

    R["R10"] = {"pass": None, "evidence": "reviewer-note styling vs body quotes needs eyes"}
    return R


# ---------------------------------------------------------------------------
# Rendering + images
# ---------------------------------------------------------------------------

def render_ir_pdf(ir_text: str, *, port: int) -> Tuple[bytes, Dict[str, Any]]:
    from app.services.pdf_exporter import export_document_pdf, shutdown_render_session

    async def _run():
        try:
            return await export_document_pdf(markdown=ir_text, server_port=port)
        finally:
            await shutdown_render_session()

    return asyncio.run(_run())


def _font(size: int):
    from PIL import ImageFont
    for cand in ("/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(cand, size)
        except Exception:
            continue
    return ImageFont.load_default()


def write_contact_sheet(page_rgbs: List[np.ndarray], out: Path, *, per_row: int = 4,
                        thumb_w: int = 300) -> None:
    from PIL import Image, ImageDraw
    thumbs = []
    for a in page_rgbs:
        im = Image.fromarray(a)
        h = int(round(im.height * thumb_w / im.width))
        thumbs.append(im.resize((thumb_w, h)))
    if not thumbs:
        return
    th = max(t.height for t in thumbs)
    pad, lab = 12, 26
    rows = math.ceil(len(thumbs) / per_row)
    sheet = Image.new("RGB", (per_row * (thumb_w + pad) + pad, rows * (th + pad + lab) + pad), (200, 204, 210))
    dr = ImageDraw.Draw(sheet)
    f = _font(18)
    for i, t in enumerate(thumbs):
        r, c = divmod(i, per_row)
        x = pad + c * (thumb_w + pad)
        y = pad + r * (th + pad + lab)
        dr.text((x, y), f"p{i + 1}", fill=(20, 20, 20), font=f)
        sheet.paste(t, (x, y + lab))
        dr.rectangle([x - 1, y + lab - 1, x + t.width, y + lab + t.height], outline=(90, 90, 90))
    sheet.save(out)


def cmd_render(args) -> int:
    from PIL import Image
    from tests.export_fidelity import render_harness
    ir_path = Path(args.ir)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ir_text = ir_path.read_text(encoding="utf-8")
    pdf_bytes, meta = render_ir_pdf(ir_text, port=args.port)
    (out / "doc.pdf").write_bytes(pdf_bytes)
    doc = render_harness.load_pdf(pdf_bytes, dpi=args.dpi, rev_byteorder=True)
    for old in out.glob("page_*.png"):
        old.unlink()
    rgbs = []
    for p in doc.pages:
        Image.fromarray(p.rgb).save(out / f"page_{p.index + 1:02d}.png")
        rgbs.append(p.rgb)
    write_contact_sheet(rgbs, out / "contact.png")
    metrics = compute_metrics(pdf_bytes, ir_text, dpi=args.dpi, doc=doc)
    metrics["render"] = {"ir": str(ir_path), "port": args.port, "dpi": args.dpi,
                         "exporter_meta": {k: v for k, v in (meta or {}).items() if k != "size"},
                         "pdf_size": len(pdf_bytes)}
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
    d = metrics["document"]
    print(json.dumps({"pages": d["page_count"], "target": d["target_pages"],
                      "density_ratio": d["density_ratio"],
                      "rubric": {k: v["pass"] for k, v in d["rubric"].items()}}, indent=1))
    return 0


def cmd_metrics(args) -> int:
    pdf_bytes = Path(args.pdf).read_bytes()
    ir_text = Path(args.ir).read_text(encoding="utf-8")
    metrics = compute_metrics(pdf_bytes, ir_text, dpi=args.dpi)
    out = Path(args.out) if args.out else Path(args.pdf).with_name("metrics.json")
    out.write_text(json.dumps(metrics, indent=2, default=str))
    print(str(out))
    return 0


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

_LOWER_IS_BETTER_DOC = ("page_count", "density_ratio")
_LOWER_IS_BETTER_PAGE = ("interior_white_band_fraction", "table_narrow_cells")


def compare_metrics(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    da, db = a["document"], b["document"]
    diff: Dict[str, Any] = {"document": {}, "rubric": {}, "pages": [], "regressions": [], "improvements": []}
    for k in ("page_count", "words", "figure_count", "target_pages", "density_ratio",
              "body_font_pt", "outline_ok", "fonts_embedded", "metadata_ok", "runt_last_page"):
        if da.get(k) != db.get(k):
            diff["document"][k] = {"a": da.get(k), "b": db.get(k)}
    for k in _LOWER_IS_BETTER_DOC:
        va, vb = da.get(k), db.get(k)
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            if vb > va + 1e-9:
                diff["regressions"].append(f"{k}: {va} -> {vb}")
            elif vb < va - 1e-9:
                diff["improvements"].append(f"{k}: {va} -> {vb}")
    for k in ("outline_ok", "fonts_embedded", "metadata_ok"):
        if da.get(k) is True and db.get(k) is False:
            diff["regressions"].append(f"{k}: True -> False")
    ra, rb = da.get("rubric", {}), db.get("rubric", {})
    for rid in sorted(set(ra) | set(rb), key=lambda s: int(s[1:])):
        pa, pb = (ra.get(rid) or {}).get("pass"), (rb.get(rid) or {}).get("pass")
        if pa != pb:
            diff["rubric"][rid] = {"a": pa, "b": pb}
            if pa is not False and pb is False:
                diff["regressions"].append(f"rubric {rid}: {pa} -> False")
            elif pa is False and pb is not False:
                diff["improvements"].append(f"rubric {rid}: False -> {pb}")

    def agg(pages, key):
        return sum(1 for p in pages if p.get(key))

    for key in ("figure_bisected", "heading_orphan", "figure_title_orphan"):
        na, nb = agg(a["pages"], key), agg(b["pages"], key)
        if nb > na:
            diff["regressions"].append(f"{key} pages: {na} -> {nb}")
        elif nb < na:
            diff["improvements"].append(f"{key} pages: {na} -> {nb}")
    ma = sum(sum(p["raw_markup_hits"].values()) for p in a["pages"])
    mb = sum(sum(p["raw_markup_hits"].values()) for p in b["pages"])
    if mb > ma:
        diff["regressions"].append(f"raw_markup_hits: {ma} -> {mb}")
    na = sum(p.get("table_narrow_cells", 0) for p in a["pages"])
    nb = sum(p.get("table_narrow_cells", 0) for p in b["pages"])
    if nb > na:
        diff["regressions"].append(f"table_narrow_cells: {na} -> {nb}")
    elif nb < na:
        diff["improvements"].append(f"table_narrow_cells: {na} -> {nb}")
    wa = max((p["interior_white_band_fraction"] for p in a["pages"]), default=0)
    wb = max((p["interior_white_band_fraction"] for p in b["pages"]), default=0)
    if wb > wa + 0.05:
        diff["regressions"].append(f"max interior_white_band_fraction: {wa} -> {wb}")
    elif wb < wa - 0.05:
        diff["improvements"].append(f"max interior_white_band_fraction: {wa} -> {wb}")
    for i in range(max(len(a["pages"]), len(b["pages"]))):
        pa = a["pages"][i] if i < len(a["pages"]) else None
        pb = b["pages"][i] if i < len(b["pages"]) else None
        row = {"page": i + 1}
        for k in ("ink_fraction", "ink_height_fraction", "interior_white_band_fraction",
                  "has_figure", "figure_bisected", "heading_orphan", "figure_title_orphan",
                  "table_narrow_cells", "word_count"):
            va = pa.get(k) if pa else None
            vb = pb.get(k) if pb else None
            if va != vb:
                row[k] = {"a": va, "b": vb}
        if len(row) > 1:
            diff["pages"].append(row)
    return diff


def cmd_compare(args) -> int:
    a = json.loads(Path(args.a).read_text())
    b = json.loads(Path(args.b).read_text())
    print(json.dumps(compare_metrics(a, b), indent=2, default=str))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="doc_aesthetics")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("--ir", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--port", type=int, default=6969)
    r.add_argument("--dpi", type=float, default=DEFAULT_DPI)
    m = sub.add_parser("metrics")
    m.add_argument("--pdf", required=True)
    m.add_argument("--ir", required=True)
    m.add_argument("--out")
    m.add_argument("--dpi", type=float, default=DEFAULT_DPI)
    c = sub.add_parser("compare")
    c.add_argument("--a", required=True)
    c.add_argument("--b", required=True)
    args = ap.parse_args(argv)
    return {"render": cmd_render, "metrics": cmd_metrics, "compare": cmd_compare}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
