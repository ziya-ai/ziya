"""
SHARED fidelity analyzers.

Each analyzer is an independent, individually-callable function that inspects a
:class:`~tests.export_fidelity.render_harness.RenderedDocument` (or, for the
format-neutral ones, plain extracted text) and returns a :class:`CheckResult`
carrying MEASURED NUMBERS, not prose.  A green audit is a table of numbers you
can diff across runs; a red one points at the page and the metric that failed.

Two families:

* RASTER checks — depend on the pixels.  Only meaningful for a backend that
  produced an image (PDF today).  They read ``doc.pages[*].rgb``.
    - colorfulness            : fraction of non-greyscale pixels per page.
    - background_whiteness    : fraction of the page that is white-backed
                                (catches dark-mode leak / blackspace, defect 6).
    - image_presence          : each expected figure -> a contiguous non-white
                                region of plausible size (defect 2).
    - expected_color_presence : diff-add green, diff-remove red, highlight hue
                                actually appear (defects 1/3/4).
    - whitespace_waste        : largest fully-white vertical band + ink coverage
                                per page (defects 5/7).
    - page_break_sanity       : no code/table/figure bisected by a boundary; no
                                heading orphaned near a page bottom; no page
                                >90% empty except the last (defect 7).

* FORMAT-NEUTRAL checks — depend only on extracted text, NO raster assumptions.
  Cards II (HTML) and III (Markdown) reuse these unchanged.
    - text_extractability     : the fixture's marker strings are recoverable.
    - content_completeness    : every UNIQUE marker appears exactly once
                                (catches a silently dropped message or a
                                duplicated one).

Every function is pure w.r.t. its input and side-effect free, so the runner can
compose them in any order and a test can call one in isolation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from tests.export_fidelity import fixture
from tests.export_fidelity.render_harness import RenderedDocument, RenderedPage


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    passed: bool
    measurements: Dict[str, Any] = field(default_factory=dict)
    failures: List[str] = field(default_factory=list)   # human-readable, numeric
    format_neutral: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Pixel primitives
# ---------------------------------------------------------------------------

def _greyscale_mask(rgb: np.ndarray, chroma_tol: int = 12) -> np.ndarray:
    """Boolean mask of pixels that are (near-)greyscale: max-min channel spread
    within ``chroma_tol``.  A colored pixel has a larger spread."""
    a = rgb.astype(int)
    spread = a.max(axis=2) - a.min(axis=2)
    return spread <= chroma_tol


def _white_mask(rgb: np.ndarray, tol: int = 12) -> np.ndarray:
    """Boolean mask of near-white pixels (all channels >= 255-tol)."""
    return np.all(rgb.astype(int) >= (255 - tol), axis=2)


def _dark_mask(rgb: np.ndarray, thresh: int = 90) -> np.ndarray:
    """Boolean mask of dark pixels (luminance below ``thresh``)."""
    a = rgb.astype(float)
    lum = 0.299 * a[:, :, 0] + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2]
    return lum < thresh


def _ink_mask(rgb: np.ndarray, white_tol: int = 12) -> np.ndarray:
    """Non-white pixels == 'ink' (text, backgrounds, figures)."""
    return ~_white_mask(rgb, tol=white_tol)


# ---------------------------------------------------------------------------
# RASTER CHECKS
# ---------------------------------------------------------------------------

def check_colorfulness(
    doc: RenderedDocument,
    *,
    min_color_fraction: float = 0.002,
    require_per_content_page: bool = False,
) -> CheckResult:
    """Colored (non-greyscale) INK fraction, per page AND document-wide.

    Defect 1 is "colors absent".  The correct, GENERAL pass criterion is
    document-level: the fixture always contains coloured elements (diff/
    highlight/diagram), so a faithful export's INK, taken as a whole, must be
    meaningfully coloured.  A single legitimately-greyscale page (e.g. a
    plain-prose continuation page) is NOT a defect, so we do NOT fail per page
    by default — that would punish ordinary text pages.

    We measure over INK only (non-white pixels) so a mostly-white page's colour
    signal is not diluted by its white background.  Per-page fractions are kept
    as diagnostics.  ``require_per_content_page=True`` opts into the stricter
    "every content page must carry colour" rule for callers whose fixture puts
    colour on every page (the sibling cards can request it).
    """
    per_page: Dict[int, float] = {}
    ink_fraction: Dict[int, float] = {}
    total_colored = 0
    total_ink = 0
    for p in doc.pages:
        ink = _ink_mask(p.rgb)
        ink_count = int(ink.sum())
        total = p.rgb.shape[0] * p.rgb.shape[1]
        ink_fraction[p.index] = ink_count / total if total else 0.0
        if ink_count == 0:
            per_page[p.index] = 0.0
            continue
        colored = int((ink & ~_greyscale_mask(p.rgb)).sum())
        per_page[p.index] = colored / ink_count
        total_colored += colored
        total_ink += ink_count

    doc_colored_fraction = (total_colored / total_ink) if total_ink else 0.0

    failures = []
    # Document-level criterion (default).
    if doc_colored_fraction < min_color_fraction:
        failures.append(
            f"document colored_ink_fraction={doc_colored_fraction:.5f} "
            f"< min {min_color_fraction} (colors absent)"
        )
    # Optional stricter per-content-page criterion.
    checked_pages: List[int] = []
    if require_per_content_page:
        checked_pages = [p.index for p in doc.pages if ink_fraction[p.index] > 0.005]
        for idx in checked_pages:
            if per_page.get(idx, 0.0) < min_color_fraction:
                failures.append(
                    f"page {idx}: colored_ink_fraction={per_page.get(idx, 0.0):.5f} "
                    f"< min {min_color_fraction}"
                )
    return CheckResult(
        name="colorfulness",
        passed=not failures,
        measurements={
            "document_colored_ink_fraction": doc_colored_fraction,
            "colored_ink_fraction_per_page": per_page,
            "ink_fraction_per_page": ink_fraction,
            "min_color_fraction": min_color_fraction,
            "require_per_content_page": require_per_content_page,
            "checked_pages": checked_pages,
        },
        failures=failures,
    )


def check_background_whiteness(
    doc: RenderedDocument,
    *,
    min_white_fraction: float = 0.60,
    max_dark_fraction: float = 0.15,
) -> CheckResult:
    """Per page: fraction white-backed and fraction dark.

    Catches defect 6 (dark-mode content not composited onto white) and defect 5
    (blackspace): a light-theme export page should be predominantly white and
    must not be dominated by dark regions.  The dark cap is the sharper signal —
    a page can be <60% white simply from a large light-grey code block, but a
    large DARK area means a dark theme leaked through.
    """
    white_frac: Dict[int, float] = {}
    dark_frac: Dict[int, float] = {}
    failures = []
    for p in doc.pages:
        total = p.rgb.shape[0] * p.rgb.shape[1]
        wf = int(_white_mask(p.rgb).sum()) / total if total else 0.0
        df = int(_dark_mask(p.rgb).sum()) / total if total else 0.0
        white_frac[p.index] = wf
        dark_frac[p.index] = df
        if df > max_dark_fraction:
            failures.append(
                f"page {p.index}: dark_fraction={df:.4f} > max {max_dark_fraction} "
                f"(dark-mode leak / blackspace)"
            )
        if wf < min_white_fraction:
            failures.append(
                f"page {p.index}: white_fraction={wf:.4f} < min {min_white_fraction}"
            )
    return CheckResult(
        name="background_whiteness",
        passed=not failures,
        measurements={
            "white_fraction_per_page": white_frac,
            "dark_fraction_per_page": dark_frac,
            "min_white_fraction": min_white_fraction,
            "max_dark_fraction": max_dark_fraction,
        },
        failures=failures,
    )


def _max_dark_row_run(rgb: np.ndarray, *, row_dark_fraction: float = 0.15,
                      dark_thresh: int = 120) -> int:
    """Longest run of CONSECUTIVE rows that are 'dark-heavy'.

    A row is dark-heavy when more than ``row_dark_fraction`` of its pixels are
    dark (max channel < ``dark_thresh``).  A solid dark diagram block (a
    dark-themed mermaid SVG leaking onto the white page) produces a tall
    contiguous run of such rows; ordinary light-theme diagrams draw thin dark
    strokes on white, so their dark-heavy rows are short and scattered.  This
    localizes a dark FIGURE that a whole-page dark-fraction is too diluted to
    catch once the figure has been scaled small (defect #6, latent).
    """
    a = rgb.astype(int)
    per_row = (a.max(axis=2) < dark_thresh).mean(axis=1)  # dark fraction per row
    heavy = per_row > row_dark_fraction
    best = cur = 0
    for h in heavy:
        cur = cur + 1 if h else 0
        if cur > best:
            best = cur
    return best


def check_dark_theme_leak(
    doc: RenderedDocument,
    *,
    max_dark_run_fraction: float = 0.015,
    row_dark_fraction: float = 0.15,
) -> CheckResult:
    """No page carries a tall contiguous DARK block (defect #6: a dark-themed
    diagram leaking onto the light page).

    Sharper than ``background_whiteness`` for this defect: a dark mermaid SVG
    scaled small by the oversized-figure fitter (PDF-03) contributes too few
    pixels to move the whole-page dark fraction past its cap, yet still paints a
    solid dark rectangle where the diagram is.  We measure the longest run of
    consecutive dark-heavy rows (a dark fill spans many adjacent rows) as a
    fraction of page height; a faithful light export keeps this small because
    light diagrams are thin strokes on white.

    Shared: Cards II/III render the same theme-baked SVG through the same /print
    path, so this raster gate applies to any backend that produces raster pages.
    """
    runs: Dict[int, float] = {}
    failures = []
    for p in doc.pages:
        h = p.rgb.shape[0]
        run = _max_dark_row_run(p.rgb, row_dark_fraction=row_dark_fraction)
        frac = run / h if h else 0.0
        runs[p.index] = frac
        if frac > max_dark_run_fraction:
            failures.append(
                f"page {p.index}: max dark-row run = {frac:.4f} of page height "
                f"> {max_dark_run_fraction} (dark diagram theme leaked onto the "
                f"light page)"
            )
    return CheckResult(
        name="dark_theme_leak",
        passed=not failures,
        measurements={
            "max_dark_row_run_fraction_per_page": runs,
            "max_dark_run_fraction": max_dark_run_fraction,
            "row_dark_fraction": row_dark_fraction,
        },
        failures=failures,
    )


def _connected_nonwhite_regions(
    rgb: np.ndarray, *, downscale: int = 4, min_pixels: int = 400
) -> List[Dict[str, Any]]:
    """Find contiguous non-white blobs via a simple flood fill on a downscaled
    ink mask.  Returns region bounding boxes + pixel areas (in downscaled px).
    Pure-numpy (no scipy) BFS labeling — adequate for figure-scale blobs.
    """
    ink = _ink_mask(rgb)
    small = ink[::downscale, ::downscale]
    h, w = small.shape
    visited = np.zeros_like(small, dtype=bool)
    regions: List[Dict[str, Any]] = []
    # iterative stack flood fill (4-connectivity)
    for y in range(h):
        row = small[y]
        for x in range(w):
            if not row[x] or visited[y, x]:
                continue
            stack = [(y, x)]
            visited[y, x] = True
            minx = maxx = x
            miny = maxy = y
            area = 0
            while stack:
                cy, cx = stack.pop()
                area += 1
                if cx < minx: minx = cx
                if cx > maxx: maxx = cx
                if cy < miny: miny = cy
                if cy > maxy: maxy = cy
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < h and 0 <= nx < w and small[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        stack.append((ny, nx))
            if area * (downscale ** 2) >= min_pixels:
                regions.append({
                    "area_px": area * (downscale ** 2),
                    "bbox": [minx * downscale, miny * downscale,
                             maxx * downscale, maxy * downscale],
                    "width_px": (maxx - minx + 1) * downscale,
                    "height_px": (maxy - miny + 1) * downscale,
                })
    regions.sort(key=lambda r: r["area_px"], reverse=True)
    return regions


def check_image_presence(
    doc: RenderedDocument,
    *,
    expected_figures: int = 3,
    min_figure_px: int = 5000,
) -> CheckResult:
    """Each expected diagram yields a contiguous non-white region of plausible
    size (defect 2: renderer images not visible).

    We count 'figure-scale' blobs across all pages — large contiguous ink
    regions well beyond a text-line footprint.  A page where the diagrams
    silently failed to render shows zero such blobs.  Threshold is deliberately
    generous (a Mermaid graph is thousands of px even downscaled) and counts the
    UNION across pages so a diagram split across a break still counts.
    """
    all_regions: List[Dict[str, Any]] = []
    per_page_big: Dict[int, int] = {}
    for p in doc.pages:
        regions = _connected_nonwhite_regions(p.rgb)
        big = [r for r in regions if r["area_px"] >= min_figure_px]
        per_page_big[p.index] = len(big)
        for r in big:
            r2 = dict(r)
            r2["page"] = p.index
            all_regions.append(r2)
    figure_like = len(all_regions)
    failures = []
    if figure_like < expected_figures:
        failures.append(
            f"found {figure_like} figure-scale non-white regions "
            f"(>= {min_figure_px}px), expected >= {expected_figures}"
        )
    return CheckResult(
        name="image_presence",
        passed=not failures,
        measurements={
            "figure_scale_regions": figure_like,
            "expected_figures": expected_figures,
            "min_figure_px": min_figure_px,
            "big_regions_per_page": per_page_big,
            "largest_regions": all_regions[:6],
        },
        failures=failures,
    )


def check_expected_color_presence(
    doc: RenderedDocument,
    *,
    signals: Optional[Dict[str, Dict[str, Any]]] = None,
) -> CheckResult:
    """diff-add green, diff-remove red and highlight hues actually appear
    (defects 1/3/4).  Uses the shared EXPECTED_COLOR_SIGNALS palette; each
    'min_pixels' signal must be met by the UNION across all pages.
    """
    signals = signals or {
        k: v for k, v in fixture.EXPECTED_COLOR_SIGNALS.items() if "min_pixels" in v
    }
    counts: Dict[str, int] = {k: 0 for k in signals}
    for p in doc.pages:
        for name, spec in signals.items():
            counts[name] += fixture.count_color_pixels(p.rgb, spec["rgb"], spec["tol"])
    failures = []
    for name, spec in signals.items():
        if counts[name] < spec["min_pixels"]:
            failures.append(
                f"{name}: {counts[name]} px within tol {spec['tol']} of "
                f"{spec['rgb']} < min {spec['min_pixels']}"
            )
    return CheckResult(
        name="expected_color_presence",
        passed=not failures,
        measurements={"color_pixel_counts": counts,
                      "signals": {k: v for k, v in signals.items()}},
        failures=failures,
    )


def _largest_white_band(rgb: np.ndarray, *, row_white_frac: float = 0.985) -> int:
    """Height (px) of the tallest run of consecutive near-fully-white rows."""
    white = _white_mask(rgb)
    row_frac = white.mean(axis=1)
    is_white_row = row_frac >= row_white_frac
    best = cur = 0
    for v in is_white_row:
        if v:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return int(best)


def check_whitespace_waste(
    doc: RenderedDocument,
    *,
    max_white_band_fraction: float = 0.45,
    ignore_last_page: bool = True,
) -> CheckResult:
    """Largest contiguous fully-white vertical band per page + ink coverage.

    A giant white band mid-document (defect 5: 'bizarre white space causing
    weirdly spaced pages') FAILS.  The last page is exempt by default (its tail
    is legitimately white).
    """
    band_frac: Dict[int, float] = {}
    ink_cov: Dict[int, float] = {}
    failures = []
    n = doc.page_count
    for p in doc.pages:
        band = _largest_white_band(p.rgb)
        frac = band / p.height_px if p.height_px else 0.0
        band_frac[p.index] = frac
        total = p.rgb.shape[0] * p.rgb.shape[1]
        ink_cov[p.index] = int(_ink_mask(p.rgb).sum()) / total if total else 0.0
        is_last = (p.index == n - 1)
        if is_last and ignore_last_page:
            continue
        if frac > max_white_band_fraction:
            failures.append(
                f"page {p.index}: largest_white_band_fraction={frac:.3f} "
                f"> max {max_white_band_fraction}"
            )
    return CheckResult(
        name="whitespace_waste",
        passed=not failures,
        measurements={
            "largest_white_band_fraction_per_page": band_frac,
            "ink_coverage_per_page": ink_cov,
            "max_white_band_fraction": max_white_band_fraction,
        },
        failures=failures,
    )


def check_page_break_sanity(
    doc: RenderedDocument,
    *,
    orphan_heading_marker: Optional[str] = None,
    orphan_zone_inches: float = 1.0,
    max_empty_fraction: float = 0.90,
) -> CheckResult:
    """Page-break sanity (defect 7), measured from the text layer + rasters.

    Three sub-signals, each numeric:
      1. no page (except the last) is more than ``max_empty_fraction`` empty
         (raster ink coverage).  A near-empty non-final page means a break was
         forced absurdly early.
      2. the orphan heading (``UNIQUE_TEXT_MARKERS['orphan_heading']``) is NOT
         stranded within ``orphan_zone_inches`` of a page bottom with no body
         text below it on that page.  Uses pdfplumber word positions.
      3. reports (does not fail on) the count of pages whose bottom-most and
         next page's top-most words look like a bisected code/table run — a
         soft signal exposed as a measurement for triage.

    Sub-signal 2 degrades gracefully to a measurement-only note when word
    positions are unavailable (non-PDF backend).
    """
    orphan_heading_marker = orphan_heading_marker or fixture.UNIQUE_TEXT_MARKERS["orphan_heading"]
    n = doc.page_count
    failures = []

    # A non-final page whose ink spans at least this fraction of the page
    # height is treated as legitimately content-filled (e.g. a tall figure
    # scaled to fit one page), even if its pixel coverage is low.  A tall thin
    # diagram fills the page top-to-bottom but paints few pixels; that is NOT a
    # nonsensical early break.  A page broken absurdly early has its little
    # content confined to a band at the top (small vertical span); a blank page
    # has no ink at all (span 0).
    min_content_vertical_span = 0.60

    # (1) near-empty non-final pages
    ink_cov: Dict[int, float] = {}
    vertical_span: Dict[int, float] = {}
    for p in doc.pages:
        total = p.rgb.shape[0] * p.rgb.shape[1]
        ink = _ink_mask(p.rgb)
        ink_cov[p.index] = int(ink.sum()) / total if total else 0.0
        # Vertical span of ink: fraction of page height between the topmost and
        # bottommost row that contains any ink (0.0 for a blank page).
        row_has_ink = ink.any(axis=1)
        rows = np.nonzero(row_has_ink)[0]
        if rows.size and p.rgb.shape[0]:
            vertical_span[p.index] = float(rows[-1] - rows[0] + 1) / p.rgb.shape[0]
        else:
            vertical_span[p.index] = 0.0
    for p in doc.pages:
        if p.index == n - 1:
            continue
        empty = 1.0 - ink_cov[p.index]
        if empty > max_empty_fraction:
            # Exempt a page whose (sparse) ink genuinely fills it top-to-bottom
            # -- a full-page figure, not an early break.  A blank page (span 0)
            # or an early-broken page (content in a top band) still fails.
            if vertical_span[p.index] >= min_content_vertical_span:
                continue
            failures.append(
                f"page {p.index}: empty_fraction={empty:.3f} > max "
                f"{max_empty_fraction} and vertical_ink_span="
                f"{vertical_span[p.index]:.3f} < {min_content_vertical_span} "
                f"(nonsensical early break)"
            )

    # (2) orphaned heading near a page bottom
    orphan_info: Dict[str, Any] = {"found": False}
    for p in doc.pages:
        if not p.words:
            continue
        # page height in PDF points: max word bottom is a proxy; fall back to
        # px/dpi*72 if words are sparse.
        page_pts = None
        hit = None
        for w in p.words:
            if orphan_heading_marker in (w.get("text") or ""):
                hit = w
                break
        if hit is None:
            continue
        # locate page bottom in points
        bottoms = [w.get("bottom", 0) for w in p.words]
        page_pts = max(bottoms) if bottoms else (p.height_px / doc.dpi * 72.0)
        zone_pts = orphan_zone_inches * 72.0
        heading_bottom = hit.get("bottom", 0)
        dist_from_bottom = page_pts - heading_bottom
        # is there body text BELOW the heading on this same page?
        below = [w for w in p.words if w.get("top", 0) > heading_bottom + 2]
        orphan_info = {
            "found": True,
            "page": p.index,
            "heading_bottom_pts": round(heading_bottom, 1),
            "page_bottom_pts": round(page_pts, 1),
            "dist_from_bottom_pts": round(dist_from_bottom, 1),
            "orphan_zone_pts": zone_pts,
            "words_below_on_page": len(below),
        }
        if dist_from_bottom < zone_pts and len(below) == 0:
            failures.append(
                f"page {p.index}: heading {orphan_heading_marker!r} orphaned "
                f"{dist_from_bottom:.0f}pt from bottom (< {zone_pts:.0f}pt) with "
                f"no body text below it"
            )
        break

    # (3) soft bisection signal (report only)
    bisection_suspects = 0
    for i in range(n - 1):
        cur, nxt = doc.pages[i], doc.pages[i + 1]
        if not cur.words or not nxt.words:
            continue
        # a code/table run typically has monospace-ish dense last/first lines;
        # we approximate "continues" as: last line of cur and first of nxt both
        # non-empty and neither ends/starts a sentence. Report only.
        cur_last = (cur.text.strip().splitlines() or [""])[-1]
        nxt_first = (nxt.text.strip().splitlines() or [""])[0]
        if cur_last and nxt_first and not cur_last.endswith((".", "!", "?", ":")):
            bisection_suspects += 1

    return CheckResult(
        name="page_break_sanity",
        passed=not failures,
        measurements={
            "ink_coverage_per_page": ink_cov,
            "vertical_ink_span_per_page": vertical_span,
            "min_content_vertical_span": min_content_vertical_span,
            "max_empty_fraction": max_empty_fraction,
            "orphan_heading": orphan_info,
            "bisection_suspect_boundaries": bisection_suspects,
            "page_count": n,
        },
        failures=failures,
    )


# ---------------------------------------------------------------------------
# FORMAT-NEUTRAL CHECKS (no raster; reused by Cards II/III)
# ---------------------------------------------------------------------------

def check_text_extractability(
    text: str,
    *,
    markers: Optional[Dict[str, str]] = None,
    min_recovered_fraction: float = 1.0,
) -> CheckResult:
    """The fixture's marker strings are recoverable from extracted text.

    FORMAT-NEUTRAL: accepts a plain string (PDF text layer, HTML text content,
    or exported markdown), so Cards II/III call it unchanged.  A PDF with no
    text layer (rasterised-only) recovers ~none and FAILS — the correct signal
    that the text is not selectable/searchable.
    """
    markers = markers or fixture.UNIQUE_TEXT_MARKERS
    recovered = {name: (m in text) for name, m in markers.items()}
    n_recovered = sum(recovered.values())
    frac = n_recovered / len(markers) if markers else 1.0
    failures = []
    if frac < min_recovered_fraction:
        missing = [name for name, ok in recovered.items() if not ok]
        failures.append(
            f"recovered {n_recovered}/{len(markers)} markers "
            f"(fraction {frac:.3f} < {min_recovered_fraction}); missing={missing}"
        )
    return CheckResult(
        name="text_extractability",
        passed=not failures,
        measurements={
            "recovered": recovered,
            "recovered_count": n_recovered,
            "total_markers": len(markers),
            "recovered_fraction": frac,
        },
        failures=failures,
        format_neutral=True,
    )


def check_content_completeness(
    text: str,
    *,
    markers: Optional[Dict[str, str]] = None,
) -> CheckResult:
    """Every UNIQUE marker appears EXACTLY ONCE in the extracted text.

    FORMAT-NEUTRAL.  Count 0 == an element (or whole message) was silently
    dropped; count >1 == content was duplicated (a real rendering bug, e.g. a
    message rendered twice).  This is the check that catches a dropped
    assistant turn without any raster involvement, so Cards II/III inherit it.
    """
    markers = markers or fixture.UNIQUE_TEXT_MARKERS
    counts = {name: text.count(m) for name, m in markers.items()}
    failures = []
    for name, c in counts.items():
        if c != 1:
            failures.append(f"marker {name!r} appears {c} times (expected exactly 1)")
    return CheckResult(
        name="content_completeness",
        passed=not failures,
        measurements={"marker_counts": counts, "total_markers": len(markers)},
        failures=failures,
        format_neutral=True,
    )


# ---------------------------------------------------------------------------
# HTML-SPECIFIC CHECKS (Card II)
#
# These read the rendered-DOM / computed-style probe the HTML backend attaches
# to ``doc.meta`` (``render_harness.render_html``), plus the static
# self-containment scan and html5lib parse-error list.  Each returns MEASURED
# NUMBERS.  They are pure w.r.t. their input: a can-fail test can synthesise a
# ``doc.meta`` dict (no browser) and prove pass<->fail.
#
# The probe shape (see _HTML_PROBE_JS in render_harness):
#   token_span_count:int, token_distinct_colors:[css-color str],
#   diff_insert_count:int, diff_insert_bgs:[str], diff_delete_count, diff_delete_bgs,
#   katex_count:int, mark_count:int, mark_bgs:[str], xss_fired:bool,
#   body_bg:str, inner_text:str
# doc.meta also carries: resource_refs {relative,localhost,blob,external_http},
#   parse_errors:[str], dark_rgb: ndarray|None, dark_probe: {...}
# ---------------------------------------------------------------------------


def _distinct_colors(colors: List[str]) -> List[str]:
    """Distinct non-transparent CSS color strings (ignores rgba alpha-0)."""
    seen = []
    for c in colors or []:
        if not c:
            continue
        cl = c.strip()
        if cl in ("transparent", "rgba(0, 0, 0, 0)"):
            continue
        if cl not in seen:
            seen.append(cl)
    return seen


def check_self_containment(doc: RenderedDocument) -> CheckResult:
    """No reference to a resource that will not resolve when the file is opened
    from disk: no relative src/link paths, no localhost URLs, no blob: URLs.
    Images must be data URIs or inline SVG.

    Reads ``doc.meta['resource_refs']`` (the static scan).  A standalone export
    that a user double-clicks must not depend on the dev server or the CWD.
    """
    refs = (doc.meta or {}).get("resource_refs", {}) or {}
    relative = refs.get("relative", [])
    localhost = refs.get("localhost", [])
    blob = refs.get("blob", [])
    external = refs.get("external_http", [])
    failures = []
    if relative:
        failures.append(f"{len(relative)} relative resource ref(s) will not resolve "
                        f"from disk: {relative[:5]}")
    if localhost:
        failures.append(f"{len(localhost)} localhost URL(s) (dev-server only): {localhost[:5]}")
    if blob:
        failures.append(f"{len(blob)} blob: URL(s) (session-scoped, dead on reopen): {blob[:5]}")
    if external:
        failures.append(f"{len(external)} external http(s) resource ref(s) "
                        f"(needs network / breaks offline): {external[:5]}")
    return CheckResult(
        name="self_containment",
        passed=not failures,
        measurements={
            "relative_refs": relative,
            "localhost_refs": localhost,
            "blob_refs": blob,
            "external_http_refs": external,
            "relative_count": len(relative),
            "localhost_count": len(localhost),
            "blob_count": len(blob),
            "external_http_count": len(external),
        },
        failures=failures,
    )


def check_dark_mode_independence(
    doc: RenderedDocument,
    *,
    min_white_fraction: float = 0.60,
    max_dark_fraction: float = 0.15,
) -> CheckResult:
    """Rendering the file with ``prefers-color-scheme: dark`` forced must STILL
    produce a light, legible document.

    This is the downloaded-file defect: an ``@media (prefers-color-scheme:dark)``
    block in the exported CSS makes the .html open DARK on a dark-mode machine.
    We compare the forced-dark screenshot (``doc.meta['dark_rgb']``) against the
    same whiteness/darkness thresholds the light render must satisfy.  A faithful
    export ignores the host color scheme (light regardless).
    """
    dark_rgb = (doc.meta or {}).get("dark_rgb")
    if dark_rgb is None:
        return CheckResult(
            name="dark_mode_independence",
            passed=False,
            measurements={"note": "no dark_rgb in doc.meta (HTML backend did not "
                                  "capture a forced-dark screenshot)"},
            failures=["dark-scheme screenshot unavailable — cannot prove independence"],
        )
    total = dark_rgb.shape[0] * dark_rgb.shape[1]
    wf = int(_white_mask(dark_rgb).sum()) / total if total else 0.0
    df = int(_dark_mask(dark_rgb).sum()) / total if total else 0.0
    # Also inspect the body background the probe measured under dark scheme.
    dark_probe = (doc.meta or {}).get("dark_probe", {}) or {}
    body_bg = dark_probe.get("body_bg")
    failures = []
    if df > max_dark_fraction:
        failures.append(
            f"forced-dark render dark_fraction={df:.4f} > max {max_dark_fraction} "
            f"(the exported CSS follows prefers-color-scheme:dark; body_bg={body_bg!r})"
        )
    if wf < min_white_fraction:
        failures.append(
            f"forced-dark render white_fraction={wf:.4f} < min {min_white_fraction} "
            f"(document is not light under a dark host scheme; body_bg={body_bg!r})"
        )
    return CheckResult(
        name="dark_mode_independence",
        passed=not failures,
        measurements={
            "dark_scheme_white_fraction": wf,
            "dark_scheme_dark_fraction": df,
            "dark_scheme_body_bg": body_bg,
            "min_white_fraction": min_white_fraction,
            "max_dark_fraction": max_dark_fraction,
        },
        failures=failures,
    )


def check_diff_coloring(doc: RenderedDocument) -> CheckResult:
    """Per-line add/remove elements EXIST and carry distinguishable background
    colors.

    Reads the probe's ``diff_insert_*`` / ``diff_delete_*``.  A faithful diff
    render has >=1 insert element and >=1 delete element, and the insert
    background differs from the delete background (green vs red).  The Python
    regex exporter emits a single uncolored ``<pre><code class="language-diff">``
    with no per-line elements — 0 inserts, 0 deletes — and FAILS.
    """
    probe = (doc.meta or {}).get("probe", {}) or {}
    n_ins = probe.get("diff_insert_count", 0)
    n_del = probe.get("diff_delete_count", 0)
    ins_bgs = _distinct_colors(probe.get("diff_insert_bgs", []))
    del_bgs = _distinct_colors(probe.get("diff_delete_bgs", []))
    failures = []
    if n_ins < 1:
        failures.append("no per-line diff INSERT element found (diff not colored per line)")
    if n_del < 1:
        failures.append("no per-line diff DELETE element found (diff not colored per line)")
    # distinguishable: insert has a bg, delete has a bg, and they differ.
    distinguishable = bool(ins_bgs) and bool(del_bgs) and (set(ins_bgs) != set(del_bgs))
    if (n_ins >= 1 and n_del >= 1) and not distinguishable:
        failures.append(
            f"insert/delete backgrounds not distinguishable "
            f"(insert_bgs={ins_bgs}, delete_bgs={del_bgs})"
        )
    return CheckResult(
        name="diff_coloring",
        passed=not failures,
        measurements={
            "insert_elements": n_ins,
            "delete_elements": n_del,
            "insert_bgs": ins_bgs,
            "delete_bgs": del_bgs,
            "distinguishable": distinguishable,
        },
        failures=failures,
    )


def check_syntax_highlighting(
    doc: RenderedDocument, *, min_distinct_colors: int = 2
) -> CheckResult:
    """Code blocks contain multiple distinctly-colored token spans rather than
    one uniform color.

    Reads the probe's ``token_span_count`` + ``token_distinct_colors``.  A
    Prism-highlighted block has many spans across several colors (keyword,
    string, comment, …).  The Python regex exporter emits a bare
    ``<pre><code>`` with zero token spans and FAILS.
    """
    probe = (doc.meta or {}).get("probe", {}) or {}
    n_spans = probe.get("token_span_count", 0)
    distinct = _distinct_colors(probe.get("token_distinct_colors", []))
    failures = []
    if n_spans < 1:
        failures.append("no token spans inside code blocks (no syntax highlighting)")
    if len(distinct) < min_distinct_colors:
        failures.append(
            f"code tokens use {len(distinct)} distinct color(s) "
            f"< min {min_distinct_colors} (uniform color == no highlighting): {distinct}"
        )
    return CheckResult(
        name="syntax_highlighting",
        passed=not failures,
        measurements={
            "token_span_count": n_spans,
            "distinct_token_colors": distinct,
            "distinct_color_count": len(distinct),
            "min_distinct_colors": min_distinct_colors,
        },
        failures=failures,
    )


def check_math_rendering(doc: RenderedDocument) -> CheckResult:
    """KaTeX output is present rather than raw LaTeX source.

    Reads the probe's ``katex_count``.  A faithful render of the fixture's
    ``$$...$$`` block produces KaTeX DOM (``.katex``); the Python regex exporter
    leaves the literal ``$$\\int...$$`` as text and FAILS.
    """
    probe = (doc.meta or {}).get("probe", {}) or {}
    n_katex = probe.get("katex_count", 0)
    failures = []
    if n_katex < 1:
        failures.append("no KaTeX output (.katex) found — math left as raw LaTeX source")
    return CheckResult(
        name="math_rendering",
        passed=not failures,
        measurements={"katex_element_count": n_katex},
        failures=failures,
    )


def check_table_rendering(doc: RenderedDocument) -> CheckResult:
    """A markdown table renders as a real ``<table>`` grid, not literal pipe
    text (HTML-06).

    Reads the probe's ``table_count`` / ``table_cell_count`` /
    ``literal_pipe_table_rows``.  The shared fixture contains a GFM table, so a
    faithful render has >=1 ``<table>`` with cells and ZERO surviving
    ``| --- | --- |`` delimiter rows in the rendered innerText.  The Python
    regex exporter USED to emit the table as literal ``|``-delimited text with
    ``<br>``s (0 tables, >=1 literal delimiter row) — content survived but the
    grid layout was lost, a defect the content/text checks could not see.
    """
    probe = (doc.meta or {}).get("probe", {}) or {}
    n_tables = probe.get("table_count", 0)
    n_cells = probe.get("table_cell_count", 0)
    n_literal = probe.get("literal_pipe_table_rows", 0)
    failures = []
    if n_tables < 1:
        failures.append("no <table> element rendered (markdown table left as literal pipe text)")
    if n_tables >= 1 and n_cells < 1:
        failures.append("<table> present but has no <td>/<th> cells")
    if n_literal > 0:
        failures.append(
            f"{n_literal} literal '| --- |' delimiter row(s) survived into "
            f"rendered text (table not converted to a grid)"
        )
    return CheckResult(
        name="table_rendering",
        passed=not failures,
        measurements={
            "table_count": n_tables,
            "table_cell_count": n_cells,
            "table_row_count": probe.get("table_row_count", 0),
            "literal_pipe_table_rows": n_literal,
        },
        failures=failures,
    )


def check_highlight_preservation(doc: RenderedDocument) -> CheckResult:
    """Highlight/``<mark>`` spans survive AND carry a non-transparent background.

    Reads the probe's ``mark_count`` + ``mark_bgs``.  NOTE (documented by Card
    I, PDF-01): Ziya's shared MarkdownRenderer has NO content-highlight
    construct — ``==...==`` is literal and a raw ``<mark>`` is HTML-escaped — so
    for the canonical fixture there is legitimately nothing to preserve.  This
    check therefore reports its measurement and passes VACUOUSLY when the source
    contains no highlight construct (``mark_count == 0`` AND no ``<mark>`` in the
    rendered text), and only FAILS when a mark element exists but has lost its
    background color.  A future real highlight feature (or Card III/adversarial
    input that injects a genuine <mark>) makes it bite.
    """
    probe = (doc.meta or {}).get("probe", {}) or {}
    n_mark = probe.get("mark_count", 0)
    mark_bgs = _distinct_colors(probe.get("mark_bgs", []))
    failures = []
    if n_mark >= 1 and not mark_bgs:
        failures.append(
            f"{n_mark} <mark>/highlight element(s) present but all have a "
            f"transparent/absent background (highlight color lost)"
        )
    return CheckResult(
        name="highlight_preservation",
        passed=not failures,
        measurements={
            "mark_element_count": n_mark,
            "mark_backgrounds": mark_bgs,
            "vacuous_pass": n_mark == 0,
        },
        failures=failures,
    )


def check_structural_validity(doc: RenderedDocument) -> CheckResult:
    """The document parses as HTML WITHOUT triggering unclosed-tag / mis-nesting
    error-recovery.

    Reads ``doc.meta['parse_errors']`` (html5lib's error list).  Zero errors ==
    well-formed.  A non-empty list means the browser only rendered it via
    error recovery (unclosed <pre>, stray </div>, …), which is fragile across
    consumers.
    """
    errors = (doc.meta or {}).get("parse_errors", []) or []
    # html5lib is very strict (flags e.g. bare '&' as an error) — to avoid
    # punishing cosmetically-noisy-but-safe documents we distinguish
    # STRUCTURAL errors (tag nesting / unexpected close) from character-level
    # nits.  A structural error is the real "unclosed-tag recovery" signal.
    structural = [
        e for e in errors
        if any(k in e for k in (
            "unexpected-end-tag", "expected-closing-tag",
            "unexpected-cell-end-tag", "eof-in", "unexpected-start-tag",
            "end-tag-too-early", "unexpected-token-in-table",
            "table-in", "misplaced",
        ))
    ]
    failures = []
    if structural:
        failures.append(
            f"{len(structural)} structural HTML parse error(s) (document only "
            f"renders via error recovery): {structural[:6]}"
        )
    return CheckResult(
        name="structural_validity",
        passed=not failures,
        measurements={
            "total_parse_errors": len(errors),
            "structural_errors": structural,
            "all_errors_sample": errors[:12],
        },
        failures=failures,
    )


def check_xss_neutralized(doc: RenderedDocument) -> CheckResult:
    """A conversation containing an XSS attempt exports NEUTRALIZED.

    Two independent signals:
      1. the in-browser canary ``probe['xss_fired']`` must be False — no
         injected ``<script>`` ran and no ``on*`` handler fired when the
         exported HTML was loaded;
      2. the raw exported HTML must not contain an un-escaped executable
         construct (a real ``<script>`` tag, an ``onerror=/onload=`` handler on
         a live tag, or a ``href="javascript:"``).  The escaped forms
         (``&lt;script&gt;`` / ``&lt;img onerror=`` / ``javascript:`` shown as
         plain text) are safe and expected.

    This gates the Stage-0 security hardening (PenPal #51/#116) so it survives
    whatever Stage 2 builds; the route-driven mode must be at least as safe.
    """
    probe = (doc.meta or {}).get("probe", {}) or {}
    html = (doc.meta or {}).get("html", "") or ""
    fired = bool(probe.get("xss_fired", False))
    low = html.lower()
    import re as _re
    executable = []
    # A real <script> element (opening tag), not the escaped &lt;script&gt;.
    if _re.search(r"<script[\s>]", low):
        executable.append("<script> tag")
    # An on* event handler attribute on a live tag: `onerror=` / `onload=` /
    # `onclick=` immediately following an unescaped attribute context.  The
    # escaped payload renders as text "&lt;img ... onerror=" and won't match
    # because it is not inside a real tag — but to be conservative we only flag
    # an on*= that is preceded by an unescaped '<tag'.
    for m in _re.finditer(r"<[a-z][a-z0-9]*\b[^>]*?\son[a-z]+\s*=", low):
        executable.append(f"inline event handler: {m.group(0)[:40]!r}")
        break
    if _re.search(r'href\s*=\s*"javascript:', low) or _re.search(r"href\s*=\s*'javascript:", low):
        executable.append('href="javascript:"')
    failures = []
    if fired:
        failures.append("XSS canary fired: an injected script/handler executed on open")
    if executable:
        failures.append(f"exported HTML contains executable construct(s): {executable}")
    return CheckResult(
        name="xss_neutralized",
        passed=not failures,
        measurements={
            "xss_canary_fired": fired,
            "executable_constructs": executable,
        },
        failures=failures,
    )


# ---------------------------------------------------------------------------
# MARKDOWN-SPECIFIC CHECKS (Card III)
#
# These are TEXT-LEVEL analyzers (no raster, no browser) that operate on the
# exported markdown STRING.  Markdown has no colour of its own — syntax
# highlighting, diff colouring and highlight spans are supplied by the CONSUMER
# (GitHub Gist, a local viewer).  So a high-fidelity markdown export is judged
# on (a) losing no information, (b) emitting markup the target renderer will
# render correctly, and (c) staying a legitimate markdown document.  Each check
# returns MEASURED NUMBERS.  They are pure functions of a string, so a can-fail
# test synthesises a broken string and proves pass<->fail with no browser.
#
# All markdown checks take the exported markdown text as a plain ``str`` (like
# the format-neutral pair), so ``run_all_checks`` calls them on ``doc.full_text``
# for a ``source_format == "markdown"`` document.
# ---------------------------------------------------------------------------

# A fenced-code opener/closer: >=3 backticks at line start (after optional
# indent), optional info string.  We match run-length so a 4-backtick fence
# (used to wrap 3-backtick content) is tracked distinctly from a 3-backtick one.
_FENCE_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<ticks>`{3,})(?P<info>[^\n]*)$")


def _iter_fence_events(text: str):
    """Yield (line_index, ticks_len, info) for every line that is a code fence.

    A closing fence has an empty (or whitespace-only) info string and >= the
    opener's tick count; CommonMark's real rule is more subtle, but for the
    integrity measurement we track the standard open/close-by-length behaviour
    the exporter and Gist both follow.
    """
    for i, line in enumerate(text.split("\n")):
        m = _FENCE_RE.match(line)
        if m:
            yield i, len(m.group("ticks")), m.group("info").strip()


def check_md_fence_integrity(
    text: str,
    *,
    expected_langs: Optional[List[str]] = None,
) -> CheckResult:
    """Every opened code fence closes; language tags survive; diffs stay fenced.

    Walks the markdown as CommonMark does: a fence opens with N backticks and
    closes with the first later line of >= N backticks and no info string.  An
    ODD number of net fence toggles means a fence was left open — which, in
    CommonMark, swallows everything to EOF (a later message, the footer) into a
    code block.  MEASURES: total fence lines, unbalanced state, per-language
    opener counts, and whether at least one ``diff`` fence is present (so the
    consumer can colour it).
    """
    expected_langs = expected_langs or ["python", "diff", "mermaid"]
    events = list(_iter_fence_events(text))

    # Simulate CommonMark open/close: stack of open fence tick-lengths.
    open_ticks: Optional[int] = None
    open_infos: List[str] = []
    lang_counts: Dict[str, int] = {}
    unterminated = 0
    for _i, ticks, info in events:
        if open_ticks is None:
            # Opening a fence.
            open_ticks = ticks
            if info:
                lang = info.split()[0]
                lang_counts[lang] = lang_counts.get(lang, 0) + 1
                open_infos.append(lang)
            else:
                open_infos.append("")
        else:
            # Inside a fence: a closer needs >= opener ticks and no info.
            if ticks >= open_ticks and not info:
                open_ticks = None
            # else: a longer/info fence line inside is content, ignored.
    if open_ticks is not None:
        unterminated = 1

    failures = []
    if unterminated:
        failures.append(
            "a code fence was left OPEN (odd toggle count) — under CommonMark it "
            "swallows all following content (next message / footer) into a code block"
        )
    missing_langs = [l for l in expected_langs if lang_counts.get(l, 0) < 1]
    if missing_langs:
        failures.append(
            f"expected language-tagged fence(s) missing: {missing_langs} "
            f"(present: {sorted(lang_counts)})"
        )
    if lang_counts.get("diff", 0) < 1:
        failures.append(
            "no ```diff fence present — a diff not inside a diff fence cannot be "
            "colour-highlighted by the consumer (Gist)"
        )
    return CheckResult(
        name="md_fence_integrity",
        passed=not failures,
        measurements={
            "fence_line_count": len(events),
            "unterminated_fence": unterminated,
            "lang_opener_counts": lang_counts,
            "diff_fence_count": lang_counts.get("diff", 0),
        },
        failures=failures,
        format_neutral=True,
    )


# The details/summary wrapper the exporter emits around tool output
# (_clean_tool_blocks). Line-anchored so it matches the block-level tags only.
_TOOL_DETAILS_OPEN_RE = re.compile(r"^[ \t]*<details>[ \t]*$")
_TOOL_SUMMARY_RE = re.compile(r"^[ \t]*<summary>Tool Output</summary>[ \t]*$")
_TOOL_DETAILS_CLOSE_RE = re.compile(r"^[ \t]*</details>[ \t]*$")


def check_md_tool_block_fence_integrity(text: str) -> CheckResult:
    """Tool output stays fully INSIDE its wrapper fence (defect MD-04).

    The exporter wraps each tool result in a ``<details>`` block containing a
    single code fence: ``<summary>Tool Output</summary>`` then an opening fence,
    the tool's raw output, then a closing fence.  If the tool output itself
    contains a backtick run >= the wrapper's tick count (extremely common — shell
    output, nested code), that inner run PREMATURELY CLOSES the wrapper: the
    remainder of the tool output then renders as top-level prose OUTSIDE the code
    block, corrupting the details section.

    Note this is INVISIBLE to ``md_fence_integrity``: the inner runs often pair
    up so the net toggle count is even (unterminated_fence == 0) even though the
    block is split into several fences with prose leaking between them.

    This check walks each ``<details>``/``</details>`` tool block and verifies its
    body opens exactly ONE fence that stays open until the block's closing fence,
    i.e. there is no non-fence, non-blank content line sitting OUTSIDE a fence
    between ``<summary>`` and ``</details>``.  MEASURES: number of tool blocks,
    and how many of them leak content outside their wrapper fence.
    """
    lines = text.split("\n")
    n = len(lines)
    tool_blocks = 0
    leaking_blocks = 0
    leaked_line_samples: List[str] = []

    i = 0
    while i < n:
        if not _TOOL_DETAILS_OPEN_RE.match(lines[i]):
            i += 1
            continue
        # Found a <details> — locate its matching </details>.
        j = i + 1
        end = None
        while j < n:
            if _TOOL_DETAILS_CLOSE_RE.match(lines[j]):
                end = j
                break
            # Nested <details> would break the naive scan; the exporter never
            # nests tool blocks, so a second <details> before a close means we
            # bail on this block conservatively (do not flag).
            if _TOOL_DETAILS_OPEN_RE.match(lines[j]):
                break
            j += 1
        if end is None:
            i += 1
            continue
        # Only treat it as a tool block if it carries the Tool Output summary.
        body = lines[i + 1:end]
        if not any(_TOOL_SUMMARY_RE.match(b) for b in body):
            i = end + 1
            continue

        tool_blocks += 1
        # Walk the body with CommonMark fence semantics and flag any non-blank,
        # non-fence, non-<summary> line that sits OUTSIDE an open fence.
        open_ticks: Optional[int] = None
        leaked = False
        for b in body:
            m = _FENCE_RE.match(b)
            if m:
                ticks = len(m.group("ticks"))
                info = m.group("info").strip()
                if open_ticks is None:
                    open_ticks = ticks
                elif ticks >= open_ticks and not info:
                    open_ticks = None
                # else: longer/info fence line inside a block is content.
                continue
            if open_ticks is not None:
                # inside the code fence — fine.
                continue
            # Outside any fence. Blank lines and the <summary> tag are structural.
            stripped = b.strip()
            if not stripped or _TOOL_SUMMARY_RE.match(b):
                continue
            # Anything else here is tool output that leaked out of the wrapper.
            leaked = True
            if len(leaked_line_samples) < 5:
                leaked_line_samples.append(stripped[:80])
        if leaked:
            leaking_blocks += 1
        i = end + 1

    failures = []
    if leaking_blocks:
        failures.append(
            f"{leaking_blocks}/{tool_blocks} tool block(s) leak content OUTSIDE their "
            f"wrapper fence — an inner ``` run closed the wrapper early "
            f"(samples: {leaked_line_samples})"
        )
    return CheckResult(
        name="md_tool_block_fence_integrity",
        passed=not failures,
        measurements={
            "tool_block_count": tool_blocks,
            "leaking_tool_blocks": leaking_blocks,
            "leaked_line_samples": leaked_line_samples,
        },
        failures=failures,
        format_neutral=True,
    )


def check_md_diagram_embedding(
    text: str,
    *,
    presence_markers: Optional[Dict[str, str]] = None,
) -> CheckResult:
    """Every fixture diagram yields an embedded image OR a preserved source fence.

    A diagram must NEVER silently vanish.  For each diagram-label marker
    (``NodeAlphaMRK`` etc.) we require EITHER: the label text still appears
    inside a fenced ```mermaid block (source preserved), OR an embedded image
    (a ``data:image/...`` data URI) is present in the document.  MEASURES: per
    marker whether it is inside a preserved fence, and the count of embedded
    data-URI images.
    """
    presence_markers = presence_markers or fixture.PRESENCE_MARKERS
    # Collect the bodies of every fenced block so we can test "inside a fence".
    fence_bodies: List[str] = []
    cur: Optional[List[str]] = None
    open_ticks: Optional[int] = None
    for line in text.split("\n"):
        m = _FENCE_RE.match(line)
        if m and open_ticks is None:
            open_ticks = len(m.group("ticks"))
            cur = []
            continue
        if m and open_ticks is not None and len(m.group("ticks")) >= open_ticks and not m.group("info").strip():
            fence_bodies.append("\n".join(cur or []))
            cur = None
            open_ticks = None
            continue
        if cur is not None:
            cur.append(line)
    fenced_text = "\n".join(fence_bodies)
    data_uri_count = len(re.findall(r"data:image/[a-zA-Z0-9.+-]+;base64,", text))

    per_marker = {}
    failures = []
    for name, marker in presence_markers.items():
        in_fence = marker in fenced_text
        # An embedded image replaces the source fence, so the label may not be
        # present as text at all in that case; presence of ANY embedded image is
        # accepted as the substitute evidence for the whole set.
        per_marker[name] = {"in_source_fence": in_fence}
        if not in_fence and data_uri_count == 0:
            failures.append(
                f"diagram {name!r} ({marker}) neither preserved in a source fence "
                f"nor embedded as an image — silently vanished"
            )
    return CheckResult(
        name="md_diagram_embedding",
        passed=not failures,
        measurements={
            "per_marker": per_marker,
            "embedded_data_uri_count": data_uri_count,
            "fenced_block_count": len(fence_bodies),
        },
        failures=failures,
        format_neutral=True,
    )


def check_md_math_preservation(text: str) -> CheckResult:
    """Display ($$…$$) and inline ($…$) math delimiters survive intact.

    The exporter must round-trip LaTeX verbatim (it must NOT try to render it —
    the consumer's renderer does).  MEASURES: display-delimiter count (``$$``,
    must be even and >= 2) and whether the fixture's block-math body survives.
    """
    dd = text.count("$$")
    failures = []
    if dd < 2:
        failures.append(f"no display-math ($$…$$) delimiters found (count {dd})")
    elif dd % 2 != 0:
        failures.append(f"odd number of $$ delimiters ({dd}) — a display-math block is unbalanced")
    # The canonical fixture's block math body.
    body_ok = "\\int_0^1 x^2" in text or "\\frac{1}{3}" in text
    if not body_ok:
        failures.append("fixture block-math body ($$\\int_0^1 x^2 …$$) not found intact")
    return CheckResult(
        name="md_math_preservation",
        passed=not failures,
        measurements={"display_delim_count": dd, "block_body_present": body_ok},
        failures=failures,
        format_neutral=True,
    )


def check_md_table_integrity(text: str) -> CheckResult:
    """Pipe tables remain well-formed: a header row, a delimiter row of dashes,
    and body rows with a consistent column count.

    A GFM table needs a ``| --- | --- |`` delimiter line immediately under the
    header; Gist and every GFM renderer require it.  MEASURES: number of
    delimiter rows found and, for the first table, that header/delimiter/body
    column counts agree.
    """
    lines = text.split("\n")
    delim_re = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$")

    def ncols(row: str) -> int:
        s = row.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        return len(re.split(r"(?<!\\)\|", s))

    delim_rows = 0
    first_table_ok = None
    for i, line in enumerate(lines):
        if delim_re.match(line) and i > 0 and "|" in lines[i - 1]:
            delim_rows += 1
            if first_table_ok is None:
                hdr = ncols(lines[i - 1])
                dlm = ncols(line)
                body_ok = True
                j = i + 1
                while j < len(lines) and "|" in lines[j] and lines[j].strip():
                    if ncols(lines[j]) != hdr:
                        body_ok = False
                    j += 1
                first_table_ok = (hdr == dlm) and body_ok
    failures = []
    if delim_rows < 1:
        failures.append("no GFM table delimiter row (| --- | …) found — pipe table not well-formed")
    elif first_table_ok is False:
        failures.append("first table's header/delimiter/body column counts disagree — malformed table")
    return CheckResult(
        name="md_table_integrity",
        passed=not failures,
        measurements={"delimiter_rows": delim_rows, "first_table_columns_consistent": first_table_ok},
        failures=failures,
        format_neutral=True,
    )


def check_md_structural_sanity(text: str) -> CheckResult:
    """Heading levels and the ---/rule separators are present and sane.

    The exporter emits ``## 👤 User`` / ``## 🤖 AI Assistant`` per message and a
    ``---`` horizontal rule between messages.  MEASURES: count of message
    headings and rule separators, and that at least one of each exists.  Also
    flags an accidental H1 collision (more than one ``# `` top-level heading
    beyond the export title) which would indicate user content injected a
    document-title-level heading.
    """
    lines = text.split("\n")
    msg_headings = sum(1 for l in lines if l.startswith("## ") and ("User" in l or "AI Assistant" in l))
    rules = sum(1 for l in lines if l.strip() == "---")
    h1 = sum(1 for l in lines if re.match(r"^# \S", l))
    failures = []
    if msg_headings < 1:
        failures.append("no per-message headings (## User / ## AI Assistant) found")
    if rules < 1:
        failures.append("no horizontal-rule (---) separators found")
    return CheckResult(
        name="md_structural_sanity",
        passed=not failures,
        measurements={"message_headings": msg_headings, "rule_separators": rules, "h1_count": h1},
        failures=failures,
        format_neutral=True,
    )


def check_md_roundtrip_legible(text: str) -> CheckResult:
    """The exported markdown parses as CommonMark without structural corruption.

    Uses ``markdown-it-py`` (a CommonMark parser) when available: the export
    should parse and, critically, NOT collapse the whole tail of the document
    into a single unterminated fence (the failure mode when a message contains
    an unbalanced ``` fence).  MEASURES: token count, fence-token count, and the
    fraction of the document's non-blank lines that land INSIDE fence tokens (a
    runaway fence pushes this near 1.0).  Degrades to the fence-integrity walk
    when the parser is unavailable, so it never hard-fails on a missing dep.
    """
    total_nonblank = sum(1 for l in text.split("\n") if l.strip())
    try:
        from markdown_it import MarkdownIt
        md = MarkdownIt("commonmark")
        tokens = md.parse(text)
    except Exception:
        # Parser unavailable: fall back to the fence walk (same signal, coarser).
        fi = check_md_fence_integrity(text)
        return CheckResult(
            name="md_roundtrip_legible",
            passed=fi.passed,
            measurements={"parser": "unavailable", "fence_integrity_passed": fi.passed},
            failures=["markdown-it-py unavailable; " + "; ".join(fi.failures)] if not fi.passed else [],
            format_neutral=True,
        )
    fence_tokens = [t for t in tokens if t.type == "fence"]
    fenced_lines = 0
    for t in fence_tokens:
        fenced_lines += sum(1 for l in (t.content or "").split("\n") if l.strip())
    fenced_fraction = (fenced_lines / total_nonblank) if total_nonblank else 0.0
    # A single fence containing > 60% of the document's content is the runaway
    # signature (footer + several messages swallowed into one code block).
    max_fence_lines = max(
        (sum(1 for l in (t.content or "").split("\n") if l.strip()) for t in fence_tokens),
        default=0,
    )
    runaway = total_nonblank > 20 and max_fence_lines > 0.6 * total_nonblank
    failures = []
    if runaway:
        failures.append(
            f"a single fenced block holds {max_fence_lines}/{total_nonblank} non-blank "
            f"lines (>{0.6:.0%}) — an unbalanced fence swallowed the document tail"
        )
    return CheckResult(
        name="md_roundtrip_legible",
        passed=not failures,
        measurements={
            "token_count": len(tokens),
            "fence_token_count": len(fence_tokens),
            "fenced_line_fraction": round(fenced_fraction, 4),
            "max_single_fence_lines": max_fence_lines,
            "total_nonblank_lines": total_nonblank,
        },
        failures=failures,
        format_neutral=True,
    )


# ---------------------------------------------------------------------------
# EXPORT-HYGIENE CHECKS (Card III markdown + Card IV PDF)
#
# Cover the two user-reported defects that apply to EVERY export format.  Both
# are text-level and format-neutral (Card IV runs the same assertions on the
# PDF text layer).
# ---------------------------------------------------------------------------

def check_no_superseded_diffs(text: str) -> CheckResult:
    """A file diffed twice in one message exports ONLY the final diff.

    The UI greys out a superseded diff (opacity 0.45); markdown has no opacity,
    so a retained stale diff is INDISTINGUISHABLE from the live one — actively
    misleading.  Uses the shared supersession fixture: the earlier diff's added
    line (``superseded_add``) MUST be ABSENT and the surviving diff's added line
    (``final_add``) MUST be present.  MEASURES both counts plus the total number
    of ``diff --git`` sections (a hygienic export of the fixture has exactly 1).
    """
    m = fixture.SUPERSESSION_MARKERS
    stale = text.count(m["superseded_add"])
    live = text.count(m["final_add"])
    diff_sections = text.count("diff --git")
    failures = []
    if stale > 0:
        failures.append(
            f"superseded diff retained: {m['superseded_add']!r} appears {stale} time(s) "
            f"(expected 0 — the UI greys this diff out)"
        )
    if live < 1:
        failures.append(
            f"live diff dropped: {m['final_add']!r} absent (expected present)"
        )
    return CheckResult(
        name="no_superseded_diffs",
        passed=not failures,
        measurements={
            "superseded_add_count": stale,
            "final_add_count": live,
            "diff_git_sections": diff_sections,
        },
        failures=failures,
        format_neutral=True,
    )


def check_no_ui_chrome(
    text: str,
    *,
    forbidden: Optional[List[str]] = None,
    keep_marker: Optional[str] = None,
) -> CheckResult:
    """Live-session UI chrome (auto-added-context banner etc.) is absent.

    The 'Auto-added N file(s) to context … Remove via the A button in the Files
    panel.' banner is a live-session affordance, meaningless in an export.  The
    real answer next to it MUST survive.  MEASURES: which forbidden substrings
    leaked, and whether the keep-marker survived.
    """
    forbidden = forbidden if forbidden is not None else fixture.UI_CHROME_FORBIDDEN_SUBSTRINGS
    keep_marker = keep_marker or fixture.UI_CHROME_MARKERS["answer"]
    leaked = [s for s in forbidden if s in text]
    kept = keep_marker in text
    failures = []
    if leaked:
        failures.append(f"live-session UI chrome leaked into export: {leaked}")
    if not kept:
        failures.append(f"real answer dropped: keep-marker {keep_marker!r} absent")
    return CheckResult(
        name="no_ui_chrome",
        passed=not failures,
        measurements={"leaked_substrings": leaked, "answer_kept": kept},
        failures=failures,
        format_neutral=True,
    )


# ---------------------------------------------------------------------------
# FIGURE-FLOW QUALITY (NEW-1) — a figure placed WELL, not merely not-bisected.
# ---------------------------------------------------------------------------

def _page_ink_bounds(rgb: np.ndarray) -> Optional[tuple]:
    """(top_row, bottom_row) of the ink on a page, or None if the page is blank."""
    ink = _ink_mask(rgb)
    rows = np.nonzero(ink.any(axis=1))[0]
    if rows.size == 0:
        return None
    return int(rows[0]), int(rows[-1])


def check_figure_flow_quality(
    doc: RenderedDocument,
    *,
    min_figure_px: int = 5000,
    lonely_figure_ink_fraction: float = 0.14,
    min_flow_shrink_floor: float = 0.75,
    min_companion_words: int = 6,
    applied_shrink_factors: Optional[List[float]] = None,
) -> CheckResult:
    """A figure is placed WELL — near its introducing prose — not stranded.

    NEW-1: an embedded figure that fits a page but not alongside its intro prose
    gets bumped WHOLE to its own page, leaving a large empty band behind and
    divorcing the figure from its context.  A figure alone on an otherwise-empty
    page passes ``page_break_sanity`` (its ink spans the height) yet looks
    amateurish.  This check measures, per page that is dominated by a single
    figure blob:

      * ``figure_only_pages`` — non-final pages whose ONLY substantial ink is
        one figure-scale blob and whose total ink coverage is below
        ``lonely_figure_ink_fraction`` (i.e. no accompanying prose): a
        "lonely-figure page" is the NEW-1 symptom.
      * ``gap_above_figure_fraction`` — for the fixture flow page, the blank
        band above the first ink (proxy for a figure pushed down).

    USER RULING: flow-driven shrinking may go as small as 0.75x but NEVER below.
    If ``applied_shrink_factors`` is supplied (measured from the render — e.g.
    a data-flow-shrink attribute or a before/after natural-size ratio), the
    check asserts every factor attributed to FLOW is >= ``min_flow_shrink_floor``.

    A hygienic (well-typeset) export of the flow fixture has ZERO lonely-figure
    pages: the figure shares a page with its prose (shrunk if needed, >=0.75x).
    """
    n = doc.page_count
    lonely_pages: List[int] = []
    per_page: Dict[int, Dict[str, Any]] = {}
    for p in doc.pages:
        total = p.rgb.shape[0] * p.rgb.shape[1]
        ink_cov = int(_ink_mask(p.rgb).sum()) / total if total else 0.0
        regions = _connected_nonwhite_regions(p.rgb)
        big = [r for r in regions if r["area_px"] >= min_figure_px]
        # a page "dominated by one figure" == exactly one figure-scale blob and
        # that blob is the overwhelming majority of the page's ink.
        big_area = sum(r["area_px"] for r in big)
        total_ink = int(_ink_mask(p.rgb).sum())
        fig_share = (big_area / total_ink) if total_ink else 0.0
        is_last = (p.index == n - 1)
        # COMPANION PROSE.  NEW-1 is about a figure DIVORCED from its prose, not
        # merely a sparse page.  A mermaid diagram's own node labels are text
        # words that sit INSIDE the figure blobs; the introducing / following
        # PROSE sits OUTSIDE them.  When word positions are available we count
        # the words falling outside every figure bounding box — that is the
        # companion prose sharing the page with the figure.  A figure page that
        # carries real companion prose is NOT lonely (the flow fix succeeded:
        # the figure co-resides with its text), even if the raster ink is
        # figure-dominated.  Falls back to the raster-only proxy when a page has
        # no positioned words (e.g. a scanned/image PDF).
        companion_words = None
        if p.words and big:
            scale = doc.dpi / 72.0  # PDF points -> raster px
            boxes = [r["bbox"] for r in big]  # [minx,miny,maxx,maxy] in px
            pad = 8

            def _inside_a_figure(word: Dict[str, Any]) -> bool:
                cx = ((word.get("x0", 0) + word.get("x1", 0)) / 2.0) * scale
                cy = ((word.get("top", 0) + word.get("bottom", 0)) / 2.0) * scale
                for (x0, y0, x1, y1) in boxes:
                    if x0 - pad <= cx <= x1 + pad and y0 - pad <= cy <= y1 + pad:
                        return True
                return False

            companion_words = sum(1 for w in p.words if not _inside_a_figure(w))
        # A mermaid diagram rasterises as MANY blobs (nodes + edges), not one,
        # so the raster proxy is "figure-scale ink DOMINATES this non-final page
        # and there is essentially no prose": >=1 figure-scale blob, the blobs
        # are the overwhelming majority of the page's ink (fig_share), and total
        # coverage is low.  A figure page is LONELY only if it is figure-
        # dominated AND lacks companion prose.  With word positions the
        # companion count is authoritative; without them fall back to the ink
        # proxy.
        raster_dominated = (
            len(big) >= 1
            and ink_cov < lonely_figure_ink_fraction
            and fig_share >= 0.85
        )
        if companion_words is not None:
            lonely = (
                (not is_last)
                and len(big) >= 1
                and companion_words < min_companion_words
            )
        else:
            lonely = (not is_last) and raster_dominated
        per_page[p.index] = {
            "ink_coverage": round(ink_cov, 4),
            "figure_scale_blobs": len(big),
            "figure_ink_share": round(fig_share, 3),
            "companion_words": companion_words,
            "lonely_figure_page": lonely,
        }
        if lonely:
            lonely_pages.append(p.index)

    # gap above first ink on the page carrying the flow figure's intro (proxy).
    gap_fraction = None
    for p in doc.pages:
        bounds = _page_ink_bounds(p.rgb)
        if bounds is None:
            continue
        top, _bottom = bounds
        # only meaningful when this page has a figure-scale blob
        if per_page[p.index]["figure_scale_blobs"] >= 1 and p.height_px:
            gap_fraction = round(top / p.height_px, 4)
            break

    failures: List[str] = []
    if lonely_pages:
        failures.append(
            f"figure(s) stranded on lonely page(s) {lonely_pages}: a figure that "
            f"fits a page but not alongside its intro prose was bumped whole to its "
            f"own near-empty page (NEW-1) instead of being shrunk (>=0.75x) to keep "
            f"flow"
        )
    shrink_report: Dict[str, Any] = {}
    if applied_shrink_factors is not None:
        below = [f for f in applied_shrink_factors if f < min_flow_shrink_floor]
        shrink_report = {
            "applied_shrink_factors": applied_shrink_factors,
            "min_observed": (min(applied_shrink_factors) if applied_shrink_factors else None),
            "flow_shrink_floor": min_flow_shrink_floor,
            "below_floor": below,
        }
        if below:
            failures.append(
                f"flow-driven shrink factor(s) {below} below the 0.75 floor "
                f"(a figure was shrunk more aggressively than the user ruling allows)"
            )

    return CheckResult(
        name="figure_flow_quality",
        passed=not failures,
        measurements={
            "per_page": per_page,
            "lonely_figure_pages": lonely_pages,
            "gap_above_figure_fraction": gap_fraction,
            "lonely_figure_ink_fraction": lonely_figure_ink_fraction,
            "page_count": n,
            **({"shrink": shrink_report} if shrink_report else {}),
        },
        failures=failures,
    )


# ---------------------------------------------------------------------------
# DIFF HEADER BINDING (NEW-3) — no blank band between a diff header and its body.
# ---------------------------------------------------------------------------

def check_diff_header_binding(
    doc: RenderedDocument,
    *,
    header_marker: str = "Modify:",
    body_start_marker: Optional[str] = None,
    body_end_marker: Optional[str] = None,
    max_header_body_gap_pts: float = 120.0,
) -> CheckResult:
    """A 'Modify: <path>' diff header sits with its body — no blank band between.

    NEW-3: print.css ``break-inside: avoid`` on the bare ``table`` selector also
    catches diff tables (diffs render as ``table.diff-table``), forcing a
    page-tall diff whole and leaving a large blank band after its 'Modify:'
    header (or stranding the header at a page bottom with the body overleaf).

    Measured from the PDF word positions:

      * the 'Modify:' header and the diff body's FIRST changed line
        (``body_start_marker``) must be on the SAME page (never split), and
      * the vertical gap between the header's bottom and that first body line's
        top must be < ``max_header_body_gap_pts`` (no large band).

    Degrades to a measurement-only note (no failure) when word positions are
    unavailable (non-PDF backend).  ``header_marker`` defaults to the literal
    'Modify:' the renderer emits (see MarkdownRenderer.extractDiffFileTitle).
    """
    body_start_marker = body_start_marker or fixture.HEADER_BINDING_MARKERS["body_start"]
    body_end_marker = body_end_marker or fixture.HEADER_BINDING_MARKERS["body_end"]

    def _find_word(words, needle):
        for w in words:
            if needle in (w.get("text") or ""):
                return w
        return None

    # locate header + body_start pages/positions across the document
    header_hit = None   # (page_index, word)
    body_start_hit = None
    have_positions = any(p.words for p in doc.pages)
    for p in doc.pages:
        if not p.words:
            continue
        if header_hit is None:
            w = _find_word(p.words, header_marker)
            if w is not None:
                header_hit = (p.index, w)
        if body_start_hit is None:
            w = _find_word(p.words, body_start_marker)
            if w is not None:
                body_start_hit = (p.index, w)

    failures: List[str] = []
    measurements: Dict[str, Any] = {
        "have_word_positions": have_positions,
        "header_marker": header_marker,
        "header_found": header_hit is not None,
        "body_start_found": body_start_hit is not None,
    }

    if not have_positions:
        measurements["note"] = "no word positions (non-PDF backend); binding not measurable"
        return CheckResult(name="diff_header_binding", passed=True,
                           measurements=measurements, failures=[])

    if header_hit is None or body_start_hit is None:
        # If the header text isn't extractable we cannot bind it; report but do
        # not fail on a missing header (some backends omit the title text).
        measurements["note"] = "header and/or body-start marker not found in text layer"
        return CheckResult(name="diff_header_binding", passed=True,
                           measurements=measurements, failures=[])

    h_page, h_word = header_hit
    b_page, b_word = body_start_hit
    measurements["header_page"] = h_page
    measurements["body_start_page"] = b_page
    same_page = (h_page == b_page)
    measurements["header_body_same_page"] = same_page
    if not same_page:
        failures.append(
            f"diff header {header_marker!r} on page {h_page} but its body "
            f"({body_start_marker!r}) on page {b_page} — header stranded from body (NEW-3)"
        )
    else:
        gap = float(b_word.get("top", 0)) - float(h_word.get("bottom", 0))
        measurements["header_body_gap_pts"] = round(gap, 1)
        measurements["max_header_body_gap_pts"] = max_header_body_gap_pts
        if gap > max_header_body_gap_pts:
            failures.append(
                f"blank band of {gap:.0f}pt between diff header {header_marker!r} and "
                f"its body ({body_start_marker!r}) on page {h_page} "
                f"(> {max_header_body_gap_pts:.0f}pt) — NEW-3"
            )

    return CheckResult(
        name="diff_header_binding",
        passed=not failures,
        measurements=measurements,
        failures=failures,
    )


def check_wide_table_completeness(
    doc: RenderedDocument,
    *,
    left_marker: str = "WIDECELL_0",
    right_marker: str = "WIDECELL_19",
    closing_marker: str = "ADVWIDE_CLOSING",
) -> CheckResult:
    """A markdown table far wider than the page keeps ALL its columns (PDF-09b).

    A table so wide its cells cannot wrap enough to fit is clipped by the print
    layout: headless Chromium's ``page.pdf()`` does not scroll or scale it, so
    the right-hand columns fall off the content margin and are DROPPED from the
    captured pages entirely (verified: the rightmost cell ``WIDECELL_19`` was
    absent from the extracted PDF text while the leftmost ``WIDECELL_0``
    survived).  The fix (``fitOverwideTables`` in PrintRenderPage) zoom-scales
    only genuinely over-wide tables so every column reflows within the margin.

    FORMAT-NEUTRAL: reads the extracted text.  A faithful export contains BOTH
    the leftmost and the rightmost cell marker (and the closing prose), proving
    no column was clipped away.  The rightmost marker is the survival probe —
    it is precisely the one a right-margin clip drops.
    """
    text = doc.full_text or ""
    left_present = left_marker in text
    right_present = right_marker in text
    closing_present = closing_marker in text
    failures: List[str] = []
    if not left_present:
        failures.append(f"leftmost cell marker {left_marker!r} missing (table not rendered)")
    if not right_present:
        failures.append(
            f"rightmost cell marker {right_marker!r} missing from extracted text — "
            f"right-hand columns clipped off the content margin (PDF-09b)"
        )
    if not closing_present:
        failures.append(f"closing prose {closing_marker!r} missing (content truncated)")
    return CheckResult(
        name="wide_table_completeness",
        passed=not failures,
        measurements={
            "left_marker": left_marker,
            "right_marker": right_marker,
            "left_present": left_present,
            "right_present": right_present,
            "closing_present": closing_present,
        },
        failures=failures,
        format_neutral=True,
    )


# ===========================================================================
# DOCUMENT-QUALITY checks (Card IV — QUAL family).
#
# Card I's nine checks are all CORRECTNESS checks (is the content present, the
# right colour, not clipped).  None of them asks whether the artefact is a
# well-made DOCUMENT.  These checks read the PDF *structure* (via ``pypdf`` on
# ``doc.raw_bytes``) rather than the pixels or the text layer, and measure the
# craft dimensions a reader on a DIFFERENT machine actually depends on:
#
#   * font_embedding      — every referenced font is embedded (else the PDF
#                           renders in a substitute face elsewhere: a real,
#                           machine-invisible fidelity failure).
#   * pdf_outline         — a navigable bookmark tree reflecting conversation
#                           structure, with destinations resolving to pages.
#   * link_annotations    — markdown/footer links are real clickable Link
#                           annotations, not blue-styled dead text.
#   * document_metadata   — /Title /Author /Subject /Creator + a creation date
#                           are set and sensible, not Chromium defaults.
#   * text_quality        — extracted text copy-pastes cleanly: no ligature
#                           corruption, no spurious hyphenation, word spacing
#                           preserved — measured against KNOWN source strings
#                           for FIDELITY (not mere presence; that is Card I's
#                           text_extractability).
#   * vector_preservation — diagrams reach the PDF as VECTOR content (the
#                           established truth for this pipeline — see the probe
#                           in the Card IV state file: image_xobjects=0,
#                           vector_path_op_hits>0), so they stay resolution
#                           independent.  If a future change rasterises them,
#                           this check measures effective placed resolution and
#                           flags anything below print quality (~150 dpi).
#
# They are INDEPENDENT, individually-callable, and return MEASURED NUMBERS, in
# the same style as every other analyzer.  They consume ``doc.raw_bytes`` (the
# PDF bytes the harness already carries) and degrade gracefully — a document
# with no ``raw_bytes`` (e.g. a synthetic raster-only fixture) yields an
# ``inapplicable`` pass rather than an exception, so the runner never breaks.
#
# NOTE ON REGISTRATION: like the two NEW-1/NEW-3 raster checks, these are NOT
# added to ``RASTER_CHECKS`` / ``run_all_checks``.  Card I's 18-check canonical
# baseline is the regression floor and must stay byte-stable; these are invoked
# explicitly by the QUAL audit (and by the mutation tests).  A separate
# ``PDF_QUALITY_CHECKS`` registry groups them for that audit.
# ===========================================================================

def _load_reader(doc: "RenderedDocument"):
    """Return a pypdf ``PdfReader`` over ``doc.raw_bytes`` (or None if absent)."""
    raw = getattr(doc, "raw_bytes", None)
    if not raw:
        return None
    import io as _io
    from pypdf import PdfReader
    return PdfReader(_io.BytesIO(raw))


def _inapplicable(name: str, reason: str) -> CheckResult:
    return CheckResult(name=name, passed=True,
                       measurements={"inapplicable": True, "reason": reason},
                       failures=[])


_SUBSET_PREFIX_RE = re.compile(r"^/?[A-Z]{6}\+")


def _iter_font_objects(reader):
    """Yield (base_font, subtype, descriptors[], is_type3) for every distinct
    font referenced by any page's /Resources, following Type0 ->
    DescendantFonts.  ``is_type3`` flags fonts whose glyphs are ``/CharProcs``
    content streams (Type3) — those are self-embedded and need no FontFile."""
    seen = set()
    idx = 0
    for page in reader.pages:
        res = page.get("/Resources")
        if not res:
            continue
        res = res.get_object()
        fonts = res.get("/Font")
        if not fonts:
            continue
        for _key, fref in fonts.get_object().items():
            try:
                fobj = fref.get_object()
            except Exception:
                continue
            subtype = str(fobj.get("/Subtype", "?"))
            is_type3 = (subtype == "/Type3") or ("/CharProcs" in fobj)
            base = str(fobj.get("/BaseFont", "?"))
            # Type3 fonts routinely have NO /BaseFont; give each a stable key so
            # distinct Type3 fonts are not collapsed under a single "?" name.
            key = base if base != "?" else f"<Type3 #{idx}>"
            idx += 1
            if key in seen:
                continue
            seen.add(key)
            descs = []
            fd = fobj.get("/FontDescriptor")
            if fd is not None:
                descs.append(fd.get_object())
            desc_fonts = fobj.get("/DescendantFonts")
            if desc_fonts is not None:
                for d in desc_fonts.get_object():
                    do = d.get_object()
                    dfd = do.get("/FontDescriptor")
                    if dfd is not None:
                        descs.append(dfd.get_object())
            yield key, subtype, descs, is_type3


def check_font_embedding(doc: "RenderedDocument") -> CheckResult:
    """Every font the PDF references is EMBEDDED.

    A PDF that references a non-embedded font renders in whatever substitute
    face the reading machine happens to have — different metrics, different
    line breaks, different glyphs — a genuine fidelity failure that is INVISIBLE
    to any raster check taken on THIS machine (which has the font).  Walks each
    page's font resources and looks for a ``/FontFile``/``/FontFile2``/
    ``/FontFile3`` in the font descriptor (following Type0 composite fonts into
    their descendant CIDFont descriptor).

    Type3 fonts are treated as embedded: their glyphs are ``/CharProcs`` content
    streams stored directly in the document, so they carry no FontFile by design
    and render identically anywhere.  (Chromium emits Type3 fonts for SVG/mermaid
    diagram text — flagging them would be a false positive.)

    MEASURES embedded vs non-embedded counts, the non-embedded base-font names,
    how many are Type3 (self-embedded), and how many are subset (``ABCDEF+``
    prefix — a well-behaved subsetting exporter).
    """
    reader = _load_reader(doc)
    if reader is None:
        return _inapplicable("font_embedding", "no raw_bytes (non-PDF document)")
    embedded, non_embedded, subset, type3 = [], [], [], []
    for base, _subtype, descs, is_type3 in _iter_font_objects(reader):
        has_file = any(
            any(k in d for k in ("/FontFile", "/FontFile2", "/FontFile3"))
            for d in descs
        )
        if is_type3:
            type3.append(base)
        if has_file or is_type3:
            embedded.append(base)
        else:
            non_embedded.append(base)
        if _SUBSET_PREFIX_RE.match(base):
            subset.append(base)
    failures = []
    if non_embedded:
        failures.append(
            f"{len(non_embedded)} referenced font(s) NOT embedded: {non_embedded} "
            f"— will render in a substitute face on another machine"
        )
    return CheckResult(
        name="font_embedding",
        passed=not failures,
        measurements={
            "n_fonts": len(embedded) + len(non_embedded),
            "n_embedded": len(embedded),
            "n_non_embedded": len(non_embedded),
            "n_type3_self_embedded": len(type3),
            "n_subset": len(subset),
            "non_embedded_fonts": non_embedded,
            "embedded_fonts": embedded,
        },
        failures=failures,
    )


def _iter_outline(reader):
    """Flatten the outline into [{title, page, depth}], resolving destinations."""
    items = []

    def rec(node, depth=0):
        for it in node:
            if isinstance(it, list):
                rec(it, depth + 1)
            else:
                try:
                    title = str(it.title)
                except Exception:
                    title = str(getattr(it, "title", "?"))
                try:
                    pg = reader.get_destination_page_number(it)
                except Exception:
                    pg = None
                items.append({"title": title, "page": pg, "depth": depth})

    try:
        rec(reader.outline)
    except Exception:
        return items
    return items


def check_pdf_outline(
    doc: "RenderedDocument",
    *,
    min_items: int = 2,
) -> CheckResult:
    """A navigable bookmark/outline tree exists with resolvable destinations.

    A long transcript with NO outline is effectively unnavigable — the reader
    cannot jump between messages/sections; they must scroll blindly.  Chromium's
    ``page.pdf()`` emits NO outline by default, so this is expected to FAIL until
    the exporter builds one (per-message or per-heading).  MEASURES: the number
    of outline items, how many resolve to a real page, the max nesting depth,
    and how many distinct destination pages the outline reaches (an outline that
    points every entry at page 0 is nearly as useless as none).
    """
    reader = _load_reader(doc)
    if reader is None:
        return _inapplicable("pdf_outline", "no raw_bytes (non-PDF document)")
    items = _iter_outline(reader)
    resolved = [it for it in items if it["page"] is not None]
    distinct_pages = sorted({it["page"] for it in resolved})
    max_depth = max((it["depth"] for it in items), default=-1)
    failures = []
    if len(items) < min_items:
        failures.append(
            f"outline has {len(items)} item(s) (expected >= {min_items}) — a long "
            f"transcript with no bookmark tree is unnavigable"
        )
    if items and not resolved:
        failures.append(
            "outline present but NO item resolves to a page destination "
            "(dangling bookmarks)"
        )
    return CheckResult(
        name="pdf_outline",
        passed=not failures,
        measurements={
            "n_items": len(items),
            "n_resolved": len(resolved),
            "n_distinct_dest_pages": len(distinct_pages),
            "max_depth": max_depth,
            "titles": [it["title"] for it in items[:12]],
        },
        failures=failures,
    )


_URL_TEXT_RE = re.compile(r"https?://[^\s)\]]+")


def _iter_link_annotations(reader):
    """Yield (page_index, uri) for every /Link annotation carrying a URI."""
    out = []
    for pno, page in enumerate(reader.pages):
        annots = page.get("/Annots")
        if not annots:
            continue
        try:
            annots = annots.get_object()
        except Exception:
            continue
        for a in annots:
            try:
                ao = a.get_object()
            except Exception:
                continue
            if ao.get("/Subtype") != "/Link":
                continue
            uri = None
            act = ao.get("/A")
            if act is not None:
                act = act.get_object()
                if act.get("/URI") is not None:
                    uri = str(act["/URI"])
            out.append((pno, uri))
    return out


def check_link_annotations(doc: "RenderedDocument") -> CheckResult:
    """URL-looking text is backed by real clickable Link annotations.

    A markdown link or the footer's Ziya URL that renders as blue text but is
    NOT a Link annotation is dead in the PDF — the reader cannot click it.  This
    compares the number of URL-looking runs in the extracted text layer against
    the number of Link annotations that carry a ``/URI`` action, and flags the
    gap.  MEASURES: annotation count, annotation URIs, url-text count, and the
    unbacked count (url texts with no corresponding annotation).
    """
    reader = _load_reader(doc)
    if reader is None:
        return _inapplicable("link_annotations", "no raw_bytes (non-PDF document)")
    annots = _iter_link_annotations(reader)
    annot_uris = [u for (_p, u) in annots if u]
    text = doc.full_text or "\n".join((p.extract_text() or "") for p in reader.pages)
    url_texts = _URL_TEXT_RE.findall(text)
    # A url text is "backed" if some annotation URI starts with / equals it
    # (pdf text extraction sometimes truncates a trailing char at a line break).
    def _backed(u):
        return any(u.startswith(a) or a.startswith(u) or a == u for a in annot_uris)
    unbacked = [u for u in url_texts if not _backed(u)]
    failures = []
    if url_texts and not annot_uris:
        failures.append(
            f"{len(url_texts)} URL(s) appear as text but there are ZERO Link "
            f"annotations — links are dead (blue text only): {url_texts[:5]}"
        )
    elif unbacked:
        failures.append(
            f"{len(unbacked)} URL text run(s) have no backing Link annotation: "
            f"{unbacked[:5]}"
        )
    return CheckResult(
        name="link_annotations",
        passed=not failures,
        measurements={
            "n_link_annotations": len(annots),
            "n_annotation_uris": len(annot_uris),
            "annotation_uris": annot_uris[:10],
            "n_url_texts": len(url_texts),
            "n_unbacked_url_texts": len(unbacked),
        },
        failures=failures,
    )


_CHROMIUM_DEFAULT_TITLES = {
    "", "about:blank", "untitled", "chromium", "ziya - code assistant",
}
_DATE_RE = re.compile(r"D:(\d{4})(\d{2})(\d{2})")


def check_document_metadata(
    doc: "RenderedDocument",
    *,
    expected_title_substr: Optional[str] = None,
) -> CheckResult:
    """The document Info dict carries sensible /Title /Author /Subject /Creator
    and a creation date — not Chromium defaults, empty, or the app-shell title.

    Metadata is what a file manager, a PDF library, and assistive tech read to
    label the document.  Chromium's ``page.pdf()`` leaves /Title as the page's
    ``<title>`` (the app shell — 'Ziya - Code Assistant'), /Creator as
    'Chromium', and sets NO /Author or /Subject.  A well-made export sets a
    conversation-specific title, an author, a subject, and its own creator
    string.  MEASURES which fields are present, which look like defaults, and
    whether a parseable creation date exists.
    """
    reader = _load_reader(doc)
    if reader is None:
        return _inapplicable("document_metadata", "no raw_bytes (non-PDF document)")
    md = reader.metadata or {}
    md = {str(k): (str(v) if v is not None else None) for k, v in dict(md).items()}
    title = (md.get("/Title") or "").strip()
    author = (md.get("/Author") or "").strip()
    subject = (md.get("/Subject") or "").strip()
    creator = (md.get("/Creator") or "").strip()
    producer = (md.get("/Producer") or "").strip()
    creation = md.get("/CreationDate") or ""
    failures = []
    if not title or title.lower() in _CHROMIUM_DEFAULT_TITLES:
        failures.append(
            f"/Title is missing or a default/app-shell value ({title!r}) — a "
            f"conversation-specific title is expected"
        )
    elif expected_title_substr and expected_title_substr not in title:
        failures.append(
            f"/Title {title!r} does not contain expected {expected_title_substr!r}"
        )
    if not author:
        failures.append("/Author is not set")
    if not subject:
        failures.append("/Subject is not set")
    if not creator or creator.lower() == "chromium":
        failures.append(
            f"/Creator is missing or the Chromium default ({creator!r}) — the "
            f"exporter should set its own creator string"
        )
    if not _DATE_RE.search(creation):
        failures.append(f"/CreationDate missing or unparseable ({creation!r})")
    return CheckResult(
        name="document_metadata",
        passed=not failures,
        measurements={
            "title": title, "author": author, "subject": subject,
            "creator": creator, "producer": producer,
            "creation_date": creation,
            "has_title": bool(title) and title.lower() not in _CHROMIUM_DEFAULT_TITLES,
            "has_author": bool(author),
            "has_subject": bool(subject),
            "has_creator": bool(creator) and creator.lower() != "chromium",
            "has_creation_date": bool(_DATE_RE.search(creation)),
        },
        failures=failures,
    )


# Ligature-prone letter pairs and the mojibake they degrade into when a PDF's
# ToUnicode map is broken or the exporter bakes presentation-form ligatures with
# no reverse mapping.  Presence of a raw ligature CODEPOINT in extracted text is
# the corruption signal (clean extraction yields the ASCII pair).
_LIGATURE_CODEPOINTS = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl",
    "\ufb03": "ffi", "\ufb04": "ffl",
}


def check_text_quality(
    doc: "RenderedDocument",
    *,
    expected_phrases: Optional[List[str]] = None,
) -> CheckResult:
    """Extracted text copy-pastes CLEANLY — fidelity, not mere presence.

    Card I's ``text_extractability`` already proves the marker strings are
    RECOVERABLE.  This is stricter: it checks the prose a human would copy out
    is not silently corrupted.  Against KNOWN fixture source phrases it verifies:

      * ligature integrity — no raw ``ﬁ``/``ﬂ``/``ﬀ`` codepoints leaking into
        the text layer (a broken ToUnicode map turns 'efficient' into 'e\ufb03cient'
        that pastes as garbage);
      * word-spacing preserved — the exact multi-word phrase is recoverable with
        its single spaces (a bad exporter drops inter-word spaces, yielding
        'theofficeworkflow');
      * no spurious mid-word hyphenation — a soft hyphen (U+00AD) or a
        'word-\\nword' break inside a known unbroken word.

    MEASURES: leaked ligature codepoints, which expected phrases survived intact,
    and any hyphenation artefacts.  ``expected_phrases`` defaults to the
    text-quality probe phrases baked into the fixture.
    """
    reader = _load_reader(doc)
    text = doc.full_text
    if text is None and reader is not None:
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
    if not text:
        return _inapplicable("text_quality", "no extractable text")

    phrases = expected_phrases if expected_phrases is not None else list(
        getattr(fixture, "TEXT_QUALITY_PHRASES", [])
    )
    # Ligature corruption: raw ligature codepoints in the extracted layer.
    leaked_ligatures = {
        cp: text.count(cp) for cp in _LIGATURE_CODEPOINTS if cp in text
    }
    # Soft hyphen leakage.
    soft_hyphens = text.count("\u00ad")
    # Normalise whitespace for phrase-fidelity comparison (a wrapped line turns
    # an inter-word space into a newline; that is legitimate reflow, NOT a
    # dropped space, so collapse runs of whitespace to a single space before
    # comparing — a DROPPED space would still fail because the two words fuse).
    norm = re.sub(r"\s+", " ", text)
    intact, missing = [], []
    for ph in phrases:
        if ph in norm:
            intact.append(ph)
        else:
            missing.append(ph)
    failures = []
    if leaked_ligatures:
        failures.append(
            f"ligature codepoints leaked into text layer (copy-paste corruption): "
            f"{ {k: v for k, v in leaked_ligatures.items()} }"
        )
    if soft_hyphens:
        failures.append(
            f"{soft_hyphens} soft-hyphen (U+00AD) char(s) in text layer — spurious "
            f"mid-word hyphenation that corrupts copy-paste"
        )
    if missing:
        failures.append(
            f"{len(missing)} known source phrase(s) not recoverable intact "
            f"(word-spacing/reflow corruption): {missing}"
        )
    return CheckResult(
        name="text_quality",
        passed=not failures,
        measurements={
            "n_expected_phrases": len(phrases),
            "n_phrases_intact": len(intact),
            "phrases_missing": missing,
            "leaked_ligature_codepoints": {k: v for k, v in leaked_ligatures.items()},
            "soft_hyphen_count": soft_hyphens,
        },
        failures=failures,
    )


_PATH_OP_RE = re.compile(rb"(?:^|\s)(?:re|c|v|y)(?:\s|$)")
_DO_RE = re.compile(rb"/[A-Za-z0-9_.]+\s+Do\b")


def _iter_image_xobjects(reader):
    """Yield (page_index, name, width, height, dpi_estimate) for image XObjects.

    dpi_estimate uses the image's pixel size vs the CTM-independent heuristic:
    without parsing the full graphics state we approximate placed size as the
    page media box (upper bound), giving a LOWER bound on effective dpi — a
    conservative 'is this at least print quality' read.
    """
    out = []
    for pno, page in enumerate(reader.pages):
        res = page.get("/Resources")
        if not res:
            continue
        res = res.get_object()
        xo = res.get("/XObject")
        if not xo:
            continue
        try:
            mb = page.mediabox
            page_w_pt = float(mb.width)
            page_h_pt = float(mb.height)
        except Exception:
            page_w_pt = page_h_pt = 612.0
        for name, ref in xo.get_object().items():
            try:
                obj = ref.get_object()
            except Exception:
                continue
            if obj.get("/Subtype") != "/Image":
                continue
            w = int(obj.get("/Width", 0) or 0)
            h = int(obj.get("/Height", 0) or 0)
            # lower-bound dpi: assume the image spans the full content width.
            content_w_in = page_w_pt / 72.0
            dpi_est = (w / content_w_in) if content_w_in else 0.0
            out.append({"page": pno, "name": str(name), "width_px": w,
                        "height_px": h, "min_effective_dpi": round(dpi_est, 1)})
    return out


def check_vector_preservation(
    doc: "RenderedDocument",
    *,
    min_print_dpi: float = 150.0,
    expect_vector: bool = True,
) -> CheckResult:
    """Diagrams reach the PDF as VECTOR content, staying resolution-independent.

    INVESTIGATED (Card IV probe on the real pipeline): the canonical export has
    ``image_xobjects == 0`` and hundreds of vector path operators plus Form-
    XObject invocations, i.e. Ziya's inline-``<svg>`` diagrams reach the PDF as
    VECTOR ops, NOT rasterised images.  (The pipeline's defensive
    ``rasterizeCanvasesToImages`` is a no-op because the renderers emit
    ``<svg>``.)  So the fidelity guarantee is: diagrams STAY vector.

    This check measures, over the whole PDF: image-XObject count, vector path-op
    hits, and Form-XObject ``Do`` invocations.  With ``expect_vector`` (default),
    it FAILS if a diagram-bearing document contains image XObjects where vector
    content is expected (a regression to rasterised diagrams).  For every image
    XObject that IS present it also reports a lower-bound effective dpi, and
    flags any below ``min_print_dpi`` — so if the pipeline ever legitimately
    switches to raster, the same check measures whether the raster is at least
    print quality instead of silently passing.
    """
    reader = _load_reader(doc)
    if reader is None:
        return _inapplicable("vector_preservation", "no raw_bytes (non-PDF document)")
    images = _iter_image_xobjects(reader)
    path_hits = 0
    do_hits = 0
    stream_bytes = 0
    for page in reader.pages:
        try:
            data = page.get_contents().get_data()
        except Exception:
            continue
        stream_bytes += len(data)
        path_hits += len(_PATH_OP_RE.findall(data))
        do_hits += len(_DO_RE.findall(data))
    low_dpi_images = [im for im in images if im["min_effective_dpi"] < min_print_dpi]
    failures = []
    if expect_vector and images:
        failures.append(
            f"{len(images)} image XObject(s) present where diagrams are expected as "
            f"VECTOR — diagrams appear rasterised (resolution-dependent regression)"
        )
    # Whether raster by regression or by design, sub-print-dpi images are a fail.
    if low_dpi_images:
        failures.append(
            f"{len(low_dpi_images)} image(s) below {min_print_dpi:.0f} dpi at full-width "
            f"placement: {[ (im['name'], im['min_effective_dpi']) for im in low_dpi_images[:5] ]}"
        )
    return CheckResult(
        name="vector_preservation",
        passed=not failures,
        measurements={
            "n_image_xobjects": len(images),
            "vector_path_op_hits": path_hits,
            "xobject_do_invocations": do_hits,
            "content_stream_bytes": stream_bytes,
            "is_vector": len(images) == 0 and path_hits > 0,
            "images": images[:8],
        },
        failures=failures,
    )


# ---------------------------------------------------------------------------
# Registry / orchestration
# ---------------------------------------------------------------------------

# Raster checks operate on a RenderedDocument.
RASTER_CHECKS: Dict[str, Callable[[RenderedDocument], CheckResult]] = {
    "colorfulness": check_colorfulness,
    "background_whiteness": check_background_whiteness,
    "dark_theme_leak": check_dark_theme_leak,
    "image_presence": check_image_presence,
    "expected_color_presence": check_expected_color_presence,
    "whitespace_waste": check_whitespace_waste,
    "page_break_sanity": check_page_break_sanity,
}

# Format-neutral checks operate on extracted text.
FORMAT_NEUTRAL_CHECKS: Dict[str, Callable[[str], CheckResult]] = {
    "text_extractability": check_text_extractability,
    "content_completeness": check_content_completeness,
}

# Markdown checks (Card III).  TEXT-LEVEL: operate on the exported markdown
# STRING (like the format-neutral pair), so the runner calls them on
# ``doc.full_text`` for a ``source_format == "markdown"`` document.  The two
# export-hygiene checks (no_superseded_diffs, no_ui_chrome) are also text-level
# and format-neutral, so Card IV (PDF) reuses them on the PDF text layer.
MARKDOWN_CHECKS: Dict[str, Callable[[str], CheckResult]] = {
    "md_fence_integrity": check_md_fence_integrity,
    "md_tool_block_fence_integrity": check_md_tool_block_fence_integrity,
    "md_diagram_embedding": check_md_diagram_embedding,
    "md_math_preservation": check_md_math_preservation,
    "md_table_integrity": check_md_table_integrity,
    "md_structural_sanity": check_md_structural_sanity,
    "md_roundtrip_legible": check_md_roundtrip_legible,
}

# Export-hygiene checks (Card III markdown + Card IV PDF).  Text-level; run for
# a markdown document AND available for the PDF text layer.
HYGIENE_CHECKS: Dict[str, Callable[[str], CheckResult]] = {
    "no_superseded_diffs": check_no_superseded_diffs,
    "no_ui_chrome": check_no_ui_chrome,
}

# HTML-specific checks (Card II).  Operate on a RenderedDocument whose
# ``source_format == "html"`` and whose ``meta`` carries the rendered-DOM probe
# + static scans (see render_harness.render_html).  Registered separately so
# the runner applies them only to HTML output.
HTML_CHECKS: Dict[str, Callable[[RenderedDocument], CheckResult]] = {
    "self_containment": check_self_containment,
    "dark_mode_independence": check_dark_mode_independence,
    "diff_coloring": check_diff_coloring,
    "syntax_highlighting": check_syntax_highlighting,
    "math_rendering": check_math_rendering,
    "table_rendering": check_table_rendering,
    "highlight_preservation": check_highlight_preservation,
    "structural_validity": check_structural_validity,
    "xss_neutralized": check_xss_neutralized,
}

# Document-quality checks (Card IV — QUAL family).  Operate on the PDF STRUCTURE
# via ``doc.raw_bytes`` (pypdf), not the pixels or text layer.  Deliberately NOT
# folded into ``run_all_checks`` / ``RASTER_CHECKS``: Card I's 18-check canonical
# baseline is the regression floor and must stay byte-stable.  The QUAL audit
# (and the mutation tests) invoke these explicitly.
PDF_QUALITY_CHECKS: Dict[str, Callable[[RenderedDocument], CheckResult]] = {
    "font_embedding": check_font_embedding,
    "pdf_outline": check_pdf_outline,
    "link_annotations": check_link_annotations,
    "document_metadata": check_document_metadata,
    "text_quality": check_text_quality,
    "vector_preservation": check_vector_preservation,
}


def run_all_checks(doc: RenderedDocument) -> List[CheckResult]:
    """Run every applicable check against a rendered document.

    Raster checks run only when the backend produced raster pages; the
    HTML-specific checks run only for an HTML-source document; the
    format-neutral checks always run against ``doc.full_text``.
    """
    results: List[CheckResult] = []
    has_raster = bool(doc.pages) and doc.pages[0].rgb is not None
    if has_raster:
        for fn in RASTER_CHECKS.values():
            results.append(fn(doc))
    if doc.source_format == "html":
        for fn in HTML_CHECKS.values():
            results.append(fn(doc))
    if doc.source_format == "markdown":
        for fn in MARKDOWN_CHECKS.values():
            results.append(fn(doc.full_text))
        # Hygiene checks are meaningful only when the hygiene fixtures are the
        # source; run them here so a markdown audit of those fixtures is gated.
        for fn in HYGIENE_CHECKS.values():
            results.append(fn(doc.full_text))
    for fn in FORMAT_NEUTRAL_CHECKS.values():
        results.append(fn(doc.full_text))
    return results
