"""
Headless diagram rendering service using Playwright.

Launches a persistent headless Chromium instance and navigates to the
/render route of the frontend.  Diagram specs are injected via
page.evaluate(), and the rendered output is captured as PNG or SVG.

This produces pixel-perfect output because it runs the exact same
D3Renderer pipeline, plugins, and post-render enhancers as the chat UI.

Usage from Python:
    renderer = await DiagramRenderer.create(server_port=6969)
    png_bytes = await renderer.render_diagram({
        "type": "mermaid",
        "definition": "graph LR\\n  A-->B",
        "theme": "dark",
    })
    await renderer.close()

Requires: ``pip install playwright`` + ``playwright install chromium``
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

# The playwright package is a hard dependency, but the Chromium build it
# drives is a post-install step pip cannot run (`ziya-install-extras
# --browser`).  Checked lazily and cached for the process: the answer only
# changes after an install, which needs a Ziya restart anyway.
_playwright_available: Optional[bool] = None


def _check_playwright() -> bool:
    """True only when BOTH the package and a Chromium build are present.

    The package-only check used to pass on `pip install playwright` alone, so
    render_diagram was offered and then died at launch with "Executable
    doesn't exist" -- the second of the two first-run failure modes.
    """
    global _playwright_available
    if _playwright_available is None:
        from app.utils import optional_features
        _playwright_available = (
            optional_features.playwright_package_available()
            and optional_features.chromium_browser_available()
        )
    return _playwright_available


# Maximum pixel extent (in EITHER dimension) of a rendered raster we will
# emit. The downstream model image pipeline (Bedrock / Anthropic vision) hard-
# rejects any image whose width OR height exceeds 8000px with
# "image.source.base64.data: At least one of the image dimensions exceed max
# allowed size: 8000 pixels" (a ValidationException that aborts the whole
# turn). A diagram whose NATURAL layout is enormous — a deep top-to-bottom
# inheritance chain, a very wide record, a huge grid — produces a full-element
# screenshot far past that cap, so the render "succeeds" yet the bytes are
# unusable. We defensively downscale any such raster here, at the single choke
# point every caller (MCP tool + HTTP route) funnels through, so no oversized
# PNG can ever leave the renderer. This is the upper-bound analog of the
# graphviz sub-pixel-collapse clamp (frontend Issue 33).
IMAGE_MAX_DIMENSION_PX = 8000


def clamp_png_dimensions(
    png_bytes: bytes, max_dim: int = IMAGE_MAX_DIMENSION_PX
) -> bytes:
    """Downscale a PNG so neither dimension exceeds ``max_dim`` pixels.

    Aspect ratio is preserved; the largest dimension is scaled down to exactly
    ``max_dim`` and the other dimension scaled proportionally (floored at 1px).
    A PNG already within the cap is returned BYTE-IDENTICAL (no re-encode), so
    this is a gap-fill for the oversized case, never a blanket re-compression.

    This is a PURE, side-effect-free helper (given the same input it always
    returns the same output) so it can be unit-tested without a browser. It is
    also defensive: any decode/encode failure, a non-PNG payload, or a missing
    Pillow install returns the original bytes unchanged — a guard must never be
    able to destroy the image it is protecting.
    """
    if not isinstance(png_bytes, (bytes, bytearray)) or len(png_bytes) < 8:
        return png_bytes
    if not isinstance(max_dim, int) or max_dim < 1:
        return png_bytes
    try:
        import io

        from PIL import Image
    except Exception:  # pragma: no cover - Pillow always present in this app
        return png_bytes

    # These bytes are our OWN trusted render output; the whole point is to
    # decode a deliberately-large screenshot so we can shrink it. Pillow's
    # decompression-bomb guard would otherwise raise on very large rasters and
    # send us down the fallback path — returning the oversized bytes unchanged,
    # which is exactly the failure we are here to prevent. Neutralize the guard
    # for the duration of this decode, restoring the prior value afterwards.
    prev_max_pixels = getattr(Image, "MAX_IMAGE_PIXELS", None)
    try:
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(io.BytesIO(png_bytes)) as img:
            width, height = img.size
            if width <= max_dim and height <= max_dim:
                # Within the cap — do not touch the bytes.
                return png_bytes

            scale = max_dim / float(max(width, height))
            new_width = max(1, int(width * scale))
            new_height = max(1, int(height * scale))
            # Guard against rounding leaving a dimension one pixel over.
            new_width = min(new_width, max_dim)
            new_height = min(new_height, max_dim)

            resized = img.resize((new_width, new_height), Image.LANCZOS)
            out = io.BytesIO()
            resized.save(out, format="PNG")
            logger.info(
                "Downscaled oversized render raster %dx%d -> %dx%d "
                "(max_dim=%d) so the downstream image pipeline accepts it",
                width,
                height,
                new_width,
                new_height,
                max_dim,
            )
            return out.getvalue()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "clamp_png_dimensions failed (%s); returning original bytes", exc
        )
        return png_bytes
    finally:
        # Always restore the process-wide decompression-bomb limit.
        Image.MAX_IMAGE_PIXELS = prev_max_pixels


# Maximum pixel extent (in EITHER dimension) of the surface we ask Chromium
# to rasterise for a single element screenshot. This is deliberately BELOW the
# 8000px downstream vision cap (IMAGE_MAX_DIMENSION_PX): a diagram whose natural
# layout is enormous is scaled-to-fit within this ceiling BEFORE capture so the
# rasterised surface stays well inside Chromium's own capture limits. Past a few
# thousand pixels a full-natural-size element screenshot of a big mermaid state
# graph fails outright with "Unable to capture screenshot" (Page.captureScreenshot
# protocol error) — capping the pre-capture surface is what keeps the capture
# succeeding at all. 6000 leaves headroom under both the Chromium ceiling and the
# 8000 cap, so the returned raster needs no second downscale in the common case.
CAPTURE_MAX_DIMENSION_PX = 6000

# Slack (px) before natural content is judged to overflow the rendered/captured
# box. Sub-pixel rounding and border/scrollbar chrome routinely differ by 1-2px
# on a diagram that is NOT actually clipped; this tolerance keeps the fit path
# from firing on those, so a diagram that already fits is captured byte-for-byte
# as it was before this change.
CAPTURE_OVERFLOW_TOL_PX = 4

# D-012 (gfx-sweep G-12): legibility floor for UNDERSIZE captures. The headless
# harness screenshots a bounded window that is far larger than a small diagram,
# so an as-is element screenshot of a tiny surface produces a raster whose text
# is sub-pixel / illegible once viewed (packet "undersize-canvas" mechanism; a
# wide grid after its SVG is forced to natural size but still authored tiny on
# one axis). When the diagram's largest natural axis is below this floor AND it
# is not clipped/over-ceiling, the full-extent SVG is enlarged (aspect ratio
# preserved) toward this target before capture. 800px is chosen so it never
# touches diagrams that already fill a normal window (>= this on the long axis
# are left byte-identical) yet is enough to lift a ~270px undersize packet to a
# legible size. It sits far below CAPTURE_MAX_DIMENSION_PX so an upscaled target
# can never approach the capture ceiling.
CAPTURE_MIN_DIMENSION_PX = 800

# Hard cap on the upscale factor so the enlargement stays a bounded, predictable
# change: a genuinely microscopic surface is lifted at most this many times, not
# blown up arbitrarily. Vector SVG upscales without quality loss, but capping
# keeps the blast radius small and the target well under the ceiling.
CAPTURE_UPSCALE_MAX = 3.0


def compute_capture_fit(
    natural_width: float,
    natural_height: float,
    rendered_width: float,
    rendered_height: float,
    max_dim: int = CAPTURE_MAX_DIMENSION_PX,
    tol: int = CAPTURE_OVERFLOW_TOL_PX,
    min_dim: int = CAPTURE_MIN_DIMENSION_PX,
    upscale_cap: float = CAPTURE_UPSCALE_MAX,
) -> tuple[bool, float, int, int]:
    """Decide how to capture a rendered diagram whose natural layout may exceed
    the bounded, overflow-clipped capture window.

    The headless harness screenshots ``#diagram-render-container`` — a bounded
    box. When a diagram's natural layout is larger than that box, an as-is
    element screenshot captures only the visible window: the content is clipped
    (D-164 mermaid sliver, D-219 packet vertical crop) and, once the natural
    surface passes Chromium's rasterisation ceiling, the capture fails to
    produce bytes at all (D-165). This helper drives the fit-to-viewport
    remedy that resolves all three: scale the oversized surface DOWN so the
    whole diagram fits, rather than clipping most of it away.

    Returns ``(needs_fit, scale, target_width, target_height)``:

    * ``needs_fit`` — True when the natural content is larger than what the
      container actually shows (by more than ``tol``) on either axis, OR when
      the natural content exceeds ``max_dim`` (would overrun the capture
      ceiling). Only then does the caller mutate the DOM; a diagram that
      already fits takes the unchanged capture path.
    * ``scale`` — factor that maps the natural surface to a capture-safe,
      legible one. ``< 1`` shrinks oversized/clipped content so neither axis
      exceeds ``max_dim`` (D-164/D-165/D-219). ``> 1`` enlarges an UNDERSIZE
      diagram whose largest axis is below ``min_dim`` toward that legibility
      floor (D-012), capped at ``upscale_cap`` and never pushing a target past
      ``max_dim``. ``1.0`` leaves the capture byte-identical.
    * ``target_width`` / ``target_height`` — the integer scaled dimensions
      (>= 1px), aspect ratio preserved.

    Pure and side-effect-free (same inputs -> same output) so it is
    unit-testable without a browser, mirroring ``clamp_png_dimensions``.
    """
    # Defensive: unusable measurements mean "do nothing / capture as-is".
    try:
        nw = float(natural_width)
        nh = float(natural_height)
        rw = float(rendered_width)
        rh = float(rendered_height)
    except (TypeError, ValueError):
        return (False, 1.0, 0, 0)
    if nw <= 0 or nh <= 0:
        return (False, 1.0, 0, 0)
    if not isinstance(max_dim, int) or max_dim < 1:
        return (False, 1.0, int(nw), int(nh))
    # Defensive: an unusable floor disables the upscale branch (downscale still
    # runs); the cap is clamped to >= 1 so it can never shrink via the up path.
    if not isinstance(min_dim, int) or min_dim < 1 or min_dim >= max_dim:
        min_dim = 0
    try:
        upscale_cap = max(1.0, float(upscale_cap))
    except (TypeError, ValueError):
        upscale_cap = 1.0

    longest = float(max(nw, nh))
    over_cap = (nw > max_dim) or (nh > max_dim)
    clipped = (nw > rw + tol) or (nh > rh + tol)

    if over_cap:
        # Oversized past the capture ceiling: shrink so the largest axis lands
        # on the ceiling. Preserves the D-165 capture-failure guard and takes
        # precedence over clipping (shrinking already unclips).
        scale = max_dim / longest
        needs_fit = True
    elif clipped:
        # Fits under the ceiling but larger than the shown window: unclip and
        # capture at natural size, no rescale (D-164 sliver / D-219 crop).
        scale = 1.0
        needs_fit = True
    elif min_dim and longest < min_dim:
        # D-012: undersize content sitting inside a much larger capture window.
        # Enlarge the full-extent surface toward the legibility floor, aspect
        # preserved, bounded by upscale_cap and never past the ceiling.
        scale = min(upscale_cap, min_dim / longest)
        if longest * scale > max_dim:  # defensive; floor << ceiling so unreached
            scale = max_dim / longest
        needs_fit = scale > 1.0
    else:
        scale = 1.0
        needs_fit = False

    target_w = max(1, min(max_dim, int(nw * scale)))
    target_h = max(1, min(max_dim, int(nh * scale)))
    return (needs_fit, scale, target_w, target_h)


def content_extent_from_boxes(
    vb_x: float, vb_y: float, vb_w: float, vb_h: float,
    bb_x: float, bb_y: float, bb_w: float, bb_h: float,
) -> tuple[int, int, int, int]:
    """Union of an SVG's declared ``viewBox`` and its actual content bounding box.

    Returns ``(min_x, min_y, extent_w, extent_h)`` — the smallest box that
    contains BOTH the declared viewBox and the rendered content (``getBBox``).

    D-117 / D-119 (gfx-sweep G-0050a2): several engines place content OUTSIDE
    their declared viewBox, and the pre-fix capture measured the viewBox alone,
    so the overflow was screenshotted CROPPED rather than scaled-to-fit:

    * a 340-char graphviz node label overflowing to negative x (w2-06),
    * a ``size="60,60!" ratio=fill`` forced upscale that pushes nodes above the
      declared box (w2-08),
    * a 60-field record whose vertical stack runs off the bottom (w2-13),
    * a 120-node ``circo`` ring whose extent exceeds the box, of which only a
      quadrant fit before (w2-14).

    Taking the UNION never crops (the result is always ``>=`` the viewBox), and
    is what ``_CAPTURE_FIT_JS`` applies as the new viewBox + SVG size so the
    whole diagram — including the previously off-canvas content — is captured,
    then handed to ``compute_capture_fit`` for scale-to-fit. Because content
    that overflowed to negative coordinates shifts ``min_x``/``min_y`` negative,
    the applied viewBox origin moves with it, which is what actually reveals a
    left/top overflow rather than merely making the box bigger.

    Pure and side-effect-free so this geometry is unit-testable without a
    browser (mirrors ``compute_capture_fit`` / ``clamp_png_dimensions``). The
    identical arithmetic is mirrored in ``_CAPTURE_MEASURE_JS`` /
    ``_CAPTURE_FIT_JS`` for the runtime application. A degenerate (zero-area)
    box on either side is ignored; if both are degenerate the result is zeros
    (caller treats that as "capture as-is").
    """
    try:
        vbx, vby, vbw, vbh = float(vb_x), float(vb_y), float(vb_w), float(vb_h)
        have_vb = vbw > 0 and vbh > 0
    except (TypeError, ValueError):
        vbx = vby = vbw = vbh = 0.0
        have_vb = False
    try:
        bbx, bby, bbw, bbh = float(bb_x), float(bb_y), float(bb_w), float(bb_h)
        have_bb = bbw > 0 and bbh > 0
    except (TypeError, ValueError):
        have_bb = False

    if not have_vb and not have_bb:
        return (0, 0, 0, 0)
    if not have_bb:
        return (
            int(math.floor(vbx)), int(math.floor(vby)),
            int(math.ceil(vbw)), int(math.ceil(vbh)),
        )
    if not have_vb:
        return (
            int(math.floor(bbx)), int(math.floor(bby)),
            int(math.ceil(bbw)), int(math.ceil(bbh)),
        )
    min_x = min(vbx, bbx)
    min_y = min(vby, bby)
    max_x = max(vbx + vbw, bbx + bbw)
    max_y = max(vby + vbh, bby + bbh)
    return (
        int(math.floor(min_x)), int(math.floor(min_y)),
        int(math.ceil(max_x - min_x)), int(math.ceil(max_y - min_y)),
    )


def merge_declared_svg_extent(
    nat_w: float, nat_h: float, decl_w: float, decl_h: float,
) -> tuple[int, int]:
    """Fold an SVG's explicitly DECLARED px width/height into a measured
    natural extent, taking the MAX on each axis.

    G-d4a898 (D-256/D-257/D-311): the pure ``compute_capture_fit`` already flags
    ``needs_fit`` for a tall Vega chart (natural 2400 vs a ~990px shown window),
    and the group's earlier fixes even added a unit test pinning that. Yet the
    defects stayed *still-broken*, because at RENDER time the measurement never
    reported 2400: Vega-Lite emits **no viewBox** and writes the true rendered
    surface only as explicit px ``width``/``height`` attributes on the root
    ``<svg>``, and ``getBBox`` / ``scrollHeight`` under-report that surface in
    headless Chromium once the bounded container clips it — so the fit decision
    was fed the clipped window height and never triggered the unclip.

    Reading the declared px size closes that gap. It is MONOTONIC (max-only): it
    can only RAISE a measurement, never shrink one, so viewBox-based engines
    (mermaid/graphviz/packet), whose declared size is already ``<=`` the
    viewBox∪content union, are unaffected and every previously-verified overflow
    render is byte-identical. A non-positive / unusable declared value is
    ignored.

    Pure and side-effect-free, mirroring ``compute_capture_fit`` /
    ``content_extent_from_boxes``; the identical MAX is applied in
    ``_CAPTURE_MEASURE_JS`` (measurement) and ``_CAPTURE_FIT_JS`` (the surface
    the unclipped SVG is sized to), so the runtime decision and the runtime
    resize agree.
    """
    try:
        nw = float(nat_w)
        nh = float(nat_h)
    except (TypeError, ValueError):
        return (0, 0)
    try:
        dw = float(decl_w)
    except (TypeError, ValueError):
        dw = 0.0
    try:
        dh = float(decl_h)
    except (TypeError, ValueError):
        dh = 0.0
    out_w = max(nw, dw) if dw > 0 else nw
    out_h = max(nh, dh) if dh > 0 else nh
    return (
        int(math.ceil(out_w)) if out_w > 0 else 0,
        int(math.ceil(out_h)) if out_h > 0 else 0,
    )


def intersect_visible_extent(
    cont_x: float, cont_y: float, cont_w: float, cont_h: float,
    clips: "list[tuple[float, float, float, float]] | None",
) -> tuple[int, int]:
    """Intersect a container's LAYOUT box with every clipping ancestor rect
    (and the viewport) to get the extent that is actually VISIBLE — i.e. what a
    screenshot can capture.

    G-6790d9 (D-256/D-311): a tall Vega-Lite chart (authored height 2400 →
    a ~2442px SVG, WITH a viewBox and a declared px height) is measured
    correctly — ``naturalHeight`` is 2442. The prior G-5b9ba1 / G-d4a898 fixes
    chased an "under-reported natural" that was never the bug. The real defect
    is ``renderedHeight``: the vega sizingConfig gives ``#diagram-render-container``
    ``height:auto``, so the container's own ``getBoundingClientRect`` GROWS to
    the full 2442. ``compute_capture_fit`` then sees ``rendered == natural`` and
    reports ``clipped == False``, so the unclip never fires — even though the
    harness root (``#diagram-render-root``: ``height:100vh``, ``overflow:hidden``,
    flex ``align-items:center``) crops the container to a ~960px centred paint
    window, which is all a Playwright element screenshot captures (element
    screenshots are subject to ancestor overflow clipping).

    Measuring the visible window as the intersection of the container box with
    its clipping ancestors makes ``clipped`` fire (2442 > 960), so the existing
    ``_CAPTURE_FIT_JS`` unclip + full-extent resize runs and the whole chart is
    captured. This is monotonic for diagrams that already fit: when nothing
    clips, the intersection equals the container box, ``rendered`` is unchanged,
    ``needs_fit`` stays False and the capture is byte-identical.

    ``clips`` is a list of ancestor/viewport ``(x, y, w, h)`` rects (screen
    coords). Returns ``(visible_w, visible_h)`` as ints ``>= 0``. An empty /
    None clip list (nothing clips) returns the container box size. A degenerate
    container box returns ``(0, 0)``.

    Pure and side-effect-free (mirrored by the ancestor walk in
    ``_CAPTURE_MEASURE_JS``) so the geometry is unit-testable without a browser.
    """
    try:
        x0 = float(cont_x)
        y0 = float(cont_y)
        w = float(cont_w)
        h = float(cont_h)
    except (TypeError, ValueError):
        return (0, 0)
    if w <= 0 or h <= 0:
        return (0, 0)
    x1 = x0 + w
    y1 = y0 + h
    for clip in (clips or []):
        try:
            cx, cy, cw, ch = (float(clip[0]), float(clip[1]),
                              float(clip[2]), float(clip[3]))
        except (TypeError, ValueError, IndexError):
            continue
        if cw <= 0 or ch <= 0:
            continue
        x0 = max(x0, cx)
        y0 = max(y0, cy)
        x1 = min(x1, cx + cw)
        y1 = min(y1, cy + ch)
    vis_w = x1 - x0
    vis_h = y1 - y0
    return (
        int(math.ceil(vis_w)) if vis_w > 0 else 0,
        int(math.ceil(vis_h)) if vis_h > 0 else 0,
    )


# JS run on the render page to MEASURE the diagram's natural layout size versus
# the size the bounded container actually shows. Read-only: it mutates nothing,
# so measuring can never alter a render that turns out not to need fitting.
#
def capture_should_rewrite_svg(measure: Any) -> bool:
    """Decide whether ``_CAPTURE_FIT_JS`` may rewrite the SVG's viewBox/size.

    D-301/D-304 (gfx-sweep G-f761db): the fit step sizes ``c.querySelector('svg')``
    to the union of its declared viewBox and content ``getBBox`` — the correct
    unclip for a SINGLE-root-SVG engine (graphviz / mermaid / vega). A Plotly
    figure, however, is a stack of several absolutely-positioned ``.main-svg``
    layers (plus WebGL canvases); rewriting only the first layer's geometry
    desynchronises the stack and clips the title / axis. That is why the prior
    render-div min-height (D-301) and title-automargin (D-304) fixes were
    necessary but insufficient: whenever ``needs_fit`` fired (extreme aspect or
    legend-grown figures) the fit step then re-broke the Plotly layout.

    Returns ``False`` — do NOT rewrite the SVG — when the container is a Plotly
    figure (``isPlotly``) or holds more than one ``<svg>``; the container-level
    unclip + scale transform in ``_CAPTURE_FIT_JS`` still reveals the clipped
    figure and is layer-agnostic. Returns ``True`` for single-SVG engines,
    preserving the historic behaviour and every overflow spec verified on it.

    Pure and side-effect-free so it is unit-testable without a browser.
    """
    if not isinstance(measure, dict):
        return True
    if measure.get("isPlotly"):
        return False
    try:
        if int(measure.get("svgCount") or 0) > 1:
            return False
    except (TypeError, ValueError):
        pass
    return True


# D-117/D-119: the natural extent is the UNION of the declared viewBox and the
# content getBBox (mirrors content_extent_from_boxes), so content that overflows
# the declared viewBox (negative-x labels, forced-size upscales, off-canvas
# records, oversized circo rings) is measured at its true size rather than
# clipped to the box.
_CAPTURE_MEASURE_JS = """
() => {
  const c = document.getElementById('diagram-render-container');
  if (!c) return null;
  const svg = c.querySelector('svg');
  let natW = c.scrollWidth || 0, natH = c.scrollHeight || 0;
  if (svg) {
    // D-117/D-119: natural extent = UNION of the declared viewBox and the
    // rendered content getBBox, so content that overflows the declared box
    // (negative-x labels, forced-size upscales, off-canvas records / rings)
    // is measured rather than clipped to the box. Mirrors
    // content_extent_from_boxes in diagram_renderer.py.
    const vb = (svg.viewBox && svg.viewBox.baseVal) || null;
    let haveVb = !!(vb && vb.width && vb.height);
    let minX = haveVb ? vb.x : 0, minY = haveVb ? vb.y : 0;
    let maxX = haveVb ? vb.x + vb.width : 0, maxY = haveVb ? vb.y + vb.height : 0;
    try {
      const bb = svg.getBBox();
      if (bb && bb.width && bb.height) {
        if (haveVb) {
          minX = Math.min(minX, bb.x); minY = Math.min(minY, bb.y);
          maxX = Math.max(maxX, bb.x + bb.width); maxY = Math.max(maxY, bb.y + bb.height);
        } else {
          minX = bb.x; minY = bb.y; maxX = bb.x + bb.width; maxY = bb.y + bb.height;
          haveVb = true;
        }
      }
    } catch (e) {}
    if (haveVb) {
      const gw = Math.ceil(maxX - minX), gh = Math.ceil(maxY - minY);
      // D-118 (explicit-undersize-not-upscaled): a graphic SMALLER than its
      // container makes c.scrollWidth report the CONTAINER box (block/inline
      // layout), not the drawing. Measuring that inflated width defeats the
      // undersize-upscale floor in compute_capture_fit (min_dim), so a
      // size="1.5,1.5!"-forced graph is captured as a sub-pixel island instead
      // of being enlarged toward legibility. When the container is NOT
      // overflowed (scrollWidth/Height within tol of client box) the true
      // natural extent IS the SVG graphic, so use it directly and let the
      // upscale branch fire. When content DOES overflow (HTML foreignObject
      // labels drawn past the svg getBBox, oversized graphs) keep the
      // union-vs-scroll MAX so nothing is under-measured or clipped -- this
      // preserves D-117 and every previously-verified overflow render.
      const TOL = 4;
      const noOverflow = (c.scrollWidth <= (c.clientWidth + TOL)) &&
                         (c.scrollHeight <= (c.clientHeight + TOL));
      if (noOverflow && gw > 0 && gh > 0) {
        natW = gw; natH = gh;
      } else {
        natW = Math.max(natW, gw);
        natH = Math.max(natH, gh);
      }
    }
    // G-d4a898 (D-256/D-257/D-311): Vega-Lite emits NO viewBox and writes the
    // true rendered surface only as explicit px width/height on the root <svg>.
    // getBBox / scrollHeight under-report that surface in headless Chromium once
    // the bounded container clips a tall/composite chart, so the fit decision
    // saw the clipped window height and never unclipped. Fold the declared px
    // size in via MAX (monotonic: can only raise, never shrink a measurement —
    // viewBox engines are unaffected). Mirrors merge_declared_svg_extent().
    try {
      const dw = (svg.width && svg.width.baseVal && svg.width.baseVal.value) || 0;
      const dh = (svg.height && svg.height.baseVal && svg.height.baseVal.value) || 0;
      if (dw > 0) natW = Math.max(natW, Math.ceil(dw));
      if (dh > 0) natH = Math.max(natH, Math.ceil(dh));
    } catch (e) {}
  }
  // D-301/D-304: report whether the container holds a Plotly figure (and how
  // many <svg> layers it has) so the fit step can SKIP the single-SVG
  // viewBox/width rewrite for Plotly. Plotly stacks several absolutely-
  // positioned `.main-svg` layers (plus canvases); rewriting only the first
  // one's geometry desynchronises the stack and clips the title/axis. Single-
  // SVG engines (graphviz/mermaid/vega) report svgCount<=1 / isPlotly=false and
  // keep the historic rewrite.
  const svgCount = c.querySelectorAll('svg').length;
  const isPlotly = !!c.querySelector('.js-plotly-plot');
  const rect = c.getBoundingClientRect();
  // G-6790d9 (D-256/D-311): the "rendered" (shown) size must be what is
  // actually VISIBLE — the container box intersected with every clipping
  // ancestor and the viewport — NOT the container's own layout box. When the
  // plugin sizingConfig gives the container height:auto it GROWS to the full
  // content (e.g. a 2442px tall Vega chart), so rect.height == naturalHeight
  // and compute_capture_fit would see clipped==false and never unclip — even
  // though the harness root (#diagram-render-root: 100vh, overflow:hidden,
  // flex align-items:center) crops it to a ~960px centred paint window, which
  // is all a Playwright element screenshot captures. Intersecting with the
  // clipping ancestors makes `clipped` fire so the existing unclip runs.
  // Monotonic for diagrams that already fit: when nothing clips, the
  // intersection equals rect and rendered* is unchanged (byte-identical).
  // Mirrors intersect_visible_extent() in diagram_renderer.py.
  let visX0 = rect.left, visY0 = rect.top;
  let visX1 = rect.right, visY1 = rect.bottom;
  try {
    let anc = c.parentElement;
    while (anc && anc !== document.documentElement) {
      let ov = 'visible';
      try {
        const cs = getComputedStyle(anc);
        // Any non-visible overflow on either axis clips the descendant.
        ov = (cs.overflowX !== 'visible' || cs.overflowY !== 'visible' ||
              cs.overflow !== 'visible') ? 'clip' : 'visible';
      } catch (e) { ov = 'visible'; }
      if (ov === 'clip') {
        const ar = anc.getBoundingClientRect();
        if (ar.width > 0 && ar.height > 0) {
          visX0 = Math.max(visX0, ar.left); visY0 = Math.max(visY0, ar.top);
          visX1 = Math.min(visX1, ar.right); visY1 = Math.min(visY1, ar.bottom);
        }
      }
      anc = anc.parentElement;
    }
    // The layout viewport clips too.
    const vw = window.innerWidth || 0, vh = window.innerHeight || 0;
    if (vw > 0) { visX0 = Math.max(visX0, 0); visX1 = Math.min(visX1, vw); }
    if (vh > 0) { visY0 = Math.max(visY0, 0); visY1 = Math.min(visY1, vh); }
  } catch (e) { /* fall back to the container box below */ }
  let visW = Math.ceil(visX1 - visX0), visH = Math.ceil(visY1 - visY0);
  if (!(visW > 0)) visW = Math.ceil(rect.width);
  if (!(visH > 0)) visH = Math.ceil(rect.height);
  return {
    naturalWidth: natW, naturalHeight: natH,
    renderedWidth: visW, renderedHeight: visH,
    svgCount: svgCount, isPlotly: isPlotly
  };
}
"""

# JS run ONLY when compute_capture_fit reports needs_fit. It (a) removes the
# overflow clip and any max-size cap from the container and its ancestors so the
# whole diagram is inside the box, (b) forces an SVG to its natural viewBox size
# (defeating the plugins' max-width:100%/height:auto shrink-to-container), (c)
# lets the container shrink-wrap that content, and (d) if scale != 1 applies a
# top-left CSS scale so the element Playwright screenshots is within the capture
# ceiling. scale<1 shrinks an oversized surface (scaled-to-fit, small but
# complete, instead of clipped); scale>1 enlarges an undersize one toward the
# legibility floor (D-012). A transform is visual only (it doesn't relayout),
# and Playwright's element screenshot honours the transformed bounding box, so
# the same top-left scale works in both directions.
_CAPTURE_FIT_JS = """
(arg) => {
  // arg is either a bare scale number (legacy) or {scale, rewriteSvg}.
  const scale = (arg && typeof arg === 'object') ? arg.scale : arg;
  const rewriteSvg = (arg && typeof arg === 'object'
    && typeof arg.rewriteSvg === 'boolean') ? arg.rewriteSvg : true;
  const c = document.getElementById('diagram-render-container');
  if (!c) return;
  let el = c;
  while (el && el !== document.body) {
    el.style.overflow = 'visible';
    el.style.maxHeight = 'none';
    el.style.maxWidth = 'none';
    el = el.parentElement;
  }
  if (document.body) { document.body.style.overflow = 'visible'; }
  const svg = c.querySelector('svg');
  // D-301/D-304: only rewrite the SVG geometry for single-SVG engines. For a
  // Plotly figure (rewriteSvg=false) the stacked `.main-svg` layers must NOT be
  // resized individually — the container-level unclip + scale transform below
  // is layer-agnostic and is the correct way to reveal a clipped Plotly figure.
  if (svg && rewriteSvg) {
    // D-117/D-119: size to the UNION of the declared viewBox and the content
    // getBBox, AND expand the viewBox origin to that union, so content that
    // overflowed the declared box (negative-x labels, forced-size upscales,
    // off-canvas records / oversized circo rings) is brought inside the
    // captured area instead of clipped. Mirrors content_extent_from_boxes.
    const vb = (svg.viewBox && svg.viewBox.baseVal) || null;
    let haveVb = !!(vb && vb.width && vb.height);
    let minX = haveVb ? vb.x : 0, minY = haveVb ? vb.y : 0;
    let maxX = haveVb ? vb.x + vb.width : 0, maxY = haveVb ? vb.y + vb.height : 0;
    try {
      const bb = svg.getBBox();
      if (bb && bb.width && bb.height) {
        if (haveVb) {
          minX = Math.min(minX, bb.x); minY = Math.min(minY, bb.y);
          maxX = Math.max(maxX, bb.x + bb.width); maxY = Math.max(maxY, bb.y + bb.height);
        } else {
          minX = bb.x; minY = bb.y; maxX = bb.x + bb.width; maxY = bb.y + bb.height;
          haveVb = true;
        }
      }
    } catch (e) {}
    let w = Math.ceil(maxX - minX), h = Math.ceil(maxY - minY);
    // G-d4a898: fold the SVG's declared px width/height (Vega's only reliable
    // size source — it emits no viewBox) into the target so the unclipped SVG
    // is sized to its full surface, not the under-reported getBBox. Max-only,
    // so viewBox engines are unchanged. Mirrors merge_declared_svg_extent().
    try {
      const dw = (svg.width && svg.width.baseVal && svg.width.baseVal.value) || 0;
      const dh = (svg.height && svg.height.baseVal && svg.height.baseVal.value) || 0;
      if (dw > 0) w = Math.max(w, Math.ceil(dw));
      if (dh > 0) h = Math.max(h, Math.ceil(dh));
      // A Vega SVG with no viewBox but a declared size still needs sizing: adopt
      // a viewBox anchored at the (possibly negative) content origin so an
      // off-top/-left overflow is revealed rather than cropped.
      if (!haveVb && dw > 0 && dh > 0) { haveVb = true; }
    } catch (e) {}
    if (haveVb && w > 0 && h > 0) {
      svg.setAttribute('viewBox', minX + ' ' + minY + ' ' + w + ' ' + h);
      svg.style.maxWidth = 'none'; svg.style.width = w + 'px'; svg.style.height = h + 'px';
    }
  }
  c.style.display = 'inline-block';
  c.style.width = 'auto';
  c.style.height = 'auto';
  if (scale && scale !== 1) {
    c.style.transformOrigin = 'top left';
    c.style.transform = 'scale(' + scale + ')';
  }
  void c.offsetWidth;
}
"""


def build_chromium_launch_args(no_sandbox: bool = False) -> list:
    """Build the Chromium launch args for the headless renderer.

    SECURITY (F-027): the Chromium sandbox is the primary defense against
    renderer-process exploits, and this renderer processes attacker-influenced
    SVG/HTML from model output. ``--no-sandbox`` is therefore OFF by default
    and only added when the operator explicitly opts in via
    ``ZIYA_CHROMIUM_NO_SANDBOX`` (needed when the sandbox cannot run, e.g.
    running as root in a container). The caller logs a warning when it's on.
    """
    # D-186/D-045: --disable-gpu is deliberately NOT included. It disables the
    # GPU process outright, which also kills the software (SwiftShader) WebGL
    # context the flags below install -- leaving WebGL-only plotly traces
    # (surface / scatter3d / mesh3d / volume / cone / streamtube / parcoords)
    # showing only the grey "WebGL is not supported" panel (total data loss).
    # --disable-dev-shm-usage is a safe container baseline (avoids /dev/shm
    # exhaustion) and does not touch GPU/WebGL.
    args = ["--disable-dev-shm-usage"]
    # D-237: WebGL-only trace families (plotly surface / scatter3d / parcoords,
    # and the *gl trace types) render to a <canvas> via a WebGL context. With no
    # GPU in the headless container the browser otherwise shows only a grey
    # "WebGL is not supported by your browser" panel -- TOTAL DATA LOSS for the
    # whole 3D feature set. ANGLE's SwiftShader backend gives a software WebGL
    # context that renders those traces correctly (slower, but this is a one-shot
    # server-side PNG so throughput is irrelevant). SwiftShader ships inside the
    # Playwright Chromium build, so no extra install is required; the flags are a
    # no-op for non-WebGL renders. Newer Chromium gates software WebGL behind
    # --enable-unsafe-swiftshader, hence all three.
    args += [
        "--use-gl=angle",
        "--use-angle=swiftshader",
        "--enable-unsafe-swiftshader",
    ]
    if no_sandbox:
        args.append("--no-sandbox")
    return args


def normalize_spec_definition(spec: dict[str, Any]) -> dict[str, Any]:
    """Ensure ``spec['definition']`` crosses the boundary as a string.

    Every frontend plugin reads this field via ``JSON.parse(spec.definition)``
    (see frontend/src/plugins/d3/), so it must arrive as text.  But the
    structured diagram types -- vega-lite, vega, plotly, packet, music,
    joint, chord, network, d3 -- have specs that are natively JSON objects,
    and a caller holding one passes the object as readily as its serialized
    form.  That used to fail differently at each call site: emit_artifact
    raised ``KeyError: slice(...)`` truncating the object for its record,
    and anything that reached the browser handed ``JSON.parse`` the string
    "[object Object]".

    Serializing at the one chokepoint all render call sites funnel through
    makes object and string definitions interchangeable everywhere, rather
    than leaving each caller to decide.
    """
    if not isinstance(spec, dict):
        return spec
    definition = spec.get("definition")
    if isinstance(definition, (dict, list)):
        return {**spec, "definition": json.dumps(definition)}
    return spec


class DiagramRenderer:
    """Headless Chromium renderer for diagram specs."""

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._base_url: str = ""
        self._lock = asyncio.Lock()

    # -- Lifecycle ----------------------------------------------------

    @classmethod
    async def create(cls, server_port: int = 6969) -> "DiagramRenderer":
        """Factory that initialises the browser.  Raises ImportError if
        Playwright or its Chromium build is not installed."""
        if not _check_playwright():
            from app.utils import optional_features
            # The status query returns None when the probe disagrees with the
            # cached gate; the description helper always names a subject.
            missing = optional_features.playwright_missing_description()
            raise ImportError(
                f"Headless diagram rendering is unavailable: missing {missing}. "
                f"{optional_features.browser_hint()}"
            )
        instance = cls()
        instance._base_url = f"http://localhost:{server_port}"
        await instance._ensure_browser()
        return instance

    async def _ensure_browser(self) -> None:
        if self._browser and self._browser.is_connected():
            return
        from playwright.async_api import async_playwright
        from app.config.env_registry import ziya_env
        self._playwright = await async_playwright().start()
        no_sandbox = ziya_env("ZIYA_CHROMIUM_NO_SANDBOX")
        if no_sandbox:
            logger.warning(
                "Headless Chromium launching with --no-sandbox "
                "(ZIYA_CHROMIUM_NO_SANDBOX is set) — the renderer sandbox is "
                "disabled; this weakens isolation when rendering model-supplied "
                "SVG/HTML."
            )
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=build_chromium_launch_args(no_sandbox=no_sandbox),
        )
        logger.info("Headless Chromium launched for diagram rendering")

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("Headless Chromium closed")

    # -- Page acquisition (for harnesses driving a route other than
    # /render) ---------------------------------------------------------

    @property
    def base_url(self) -> str:
        """Origin this renderer's pages are served from."""
        return self._base_url

    async def acquire_page(
        self, *, viewport_width: int = 1280, viewport_height: int = 960,
    ) -> Any:
        """Return a fresh page on the shared browser.

        Exists so a harness that must screenshot the real application (rather
        than the isolated /render page) can reuse this one warm Chromium
        instead of launching a second: startup is ~1s and a duplicate instance
        doubles the memory floor for the life of the server.  ``new_page``
        gives each caller its own browser context, so localStorage /
        sessionStorage seeded by one caller cannot leak into another -- which
        the chat-message renderer relies on, since it seeds the selected
        conversation id into sessionStorage.

        The caller owns the returned page and MUST close it.
        """
        async with self._lock:
            await self._ensure_browser()
        return await self._browser.new_page(
            viewport={"width": viewport_width, "height": viewport_height},
        )

    # -- Diagnostics --------------------------------------------------

    async def _collect_diagnostics(
        self,
        page: Any,
        spec: dict[str, Any],
        console_log: list[str],
        pageerror_log: list[str],
    ) -> dict[str, Any]:
        """Best-effort dump of everything the harness page can tell us
        about why a render didn't reach a terminal state."""
        diag: dict[str, Any] = {
            "spec_type": spec.get("type"),
            "console_tail": console_log[-20:],
            "pageerrors": pageerror_log[-10:],
        }
        try:
            diag["render_status"] = await page.get_attribute(
                "#diagram-render-root", "data-render-status"
            )
            diag["page_error"] = await page.get_attribute(
                "#diagram-render-root", "data-error"
            )
            diag["elapsed_ms"] = await page.get_attribute(
                "#diagram-render-root", "data-elapsed-ms"
            )
            diag["last_event"] = await page.get_attribute(
                "#diagram-render-root", "data-last-event"
            )
        except Exception as e:
            diag["attr_read_error"] = repr(e)
        try:
            diag["dom_counts"] = await page.evaluate(
                """() => {
                    const c = document.getElementById('diagram-render-container');
                    if (!c) return {missing_container: true};
                    return {
                        children: c.children.length,
                        svg: c.querySelectorAll('svg').length,
                        canvas: c.querySelectorAll('canvas').length,
                        img: c.querySelectorAll('img').length,
                        html_len: c.innerHTML.length,
                        html_head: c.innerHTML.slice(0, 500),
                    };
                }"""
            )
        except Exception as e:
            diag["dom_eval_error"] = repr(e)
        return diag

    @staticmethod
    def _classify_console_log(console_log: list[str]) -> dict[str, list[str]]:
        """Split the raw ``[type] text`` console log into warning/error
        buckets, dropping routine ``[log]``/``[info]``/``[debug]`` noise.

        This is what lets a caller distinguish "rendered, but the mermaid
        plugin logged a fixup warning" from a clean render — the console
        listener was already wired up (for the timeout/error diagnostic
        dump), but nothing surfaced it on the SUCCESS path, so a render
        that completes with warnings looked identical to one with none.
        """
        warnings: list[str] = []
        errors: list[str] = []
        for entry in console_log:
            if entry.startswith("[warning]") or entry.startswith("[warn]"):
                warnings.append(entry)
            elif entry.startswith("[error]"):
                errors.append(entry)
        return {"warnings": warnings, "errors": errors}

    # -- Rendering ----------------------------------------------------

    async def render_diagram(
        self,
        spec: dict[str, Any],
        *,
        format: Literal["png", "svg"] = "png",
        viewport_width: int = 1280,
        viewport_height: int = 960,
        timeout_ms: int = 30_000,
    ) -> bytes:
        """Render a diagram spec and return image bytes only.

        Back-compat wrapper around ``render_diagram_with_diagnostics`` for
        callers (HTTP route, conversation exporter) that only want the
        image and have no use for console diagnostics.
        """
        image_bytes, _diagnostics = await self.render_diagram_with_diagnostics(
            spec, format=format, viewport_width=viewport_width,
            viewport_height=viewport_height, timeout_ms=timeout_ms,
        )
        return image_bytes

    async def render_diagram_with_diagnostics(
        self,
        spec: dict[str, Any],
        *,
        format: Literal["png", "svg"] = "png",
        viewport_width: int = 1280,
        viewport_height: int = 960,
        timeout_ms: int = 30_000,
    ) -> bytes:
        """Render a diagram spec and return image bytes.

        Parameters
        ----------
        spec : dict
            Must include ``type`` and ``definition``.  Optional keys:
            ``theme`` ('dark'|'light'), ``width``, ``height``, ``title``.
        format : 'png' | 'svg'
            Output format.  SVG extraction works for SVG-based renderers
            only; falls back to PNG screenshot for canvas-based ones.
        viewport_width, viewport_height : int
            Headless browser viewport dimensions.
        timeout_ms : int
            Maximum time to wait for the render to complete.
        """
        # Object/array definitions (the structured diagram types) are
        # serialized here so every caller -- the two builtin tools, the HTTP
        # route, the conversation exporter -- gets identical treatment and
        # the browser always receives the string its plugins JSON.parse.
        spec = normalize_spec_definition(spec)

        async with self._lock:
            await self._ensure_browser()

        page = await self._browser.new_page(
            viewport={"width": viewport_width, "height": viewport_height},
        )

        # Capture console messages and page errors so we can include them
        # in any diagnostic dump on failure.
        console_log: list[str] = []
        pageerror_log: list[str] = []

        def _on_console(msg: Any) -> None:
            try:
                console_log.append(f"[{msg.type}] {msg.text}")
            except Exception:  # pragma: no cover - defensive
                pass

        def _on_pageerror(err: Any) -> None:
            try:
                pageerror_log.append(str(err))
            except Exception:  # pragma: no cover - defensive
                pass

        page.on("console", _on_console)
        page.on("pageerror", _on_pageerror)

        try:
            # Navigate to the render harness page
            response = await page.goto(
                f"{self._base_url}/render",
                wait_until="networkidle",
                timeout=timeout_ms,
            )

            # Fail loudly if the shell itself did not load.  A non-2xx body
            # (e.g. a 404 when the /render SPA passthrough is missing from
            # app/routes/page_routes.py) reaches "networkidle" instantly, so
            # the goto looks successful and the failure only surfaces later as
            # an opaque "window.__renderDiagram is not a function" TypeError.
            # Require a real int: a mocked page.goto() yields a Mock whose
            # .status is another Mock, and comparing that to an int raises
            # TypeError.  A driver guard must never be able to crash the
            # render it is protecting.
            status = getattr(response, "status", None) if response is not None else None
            if isinstance(status, int) and not isinstance(status, bool) and status >= 400:
                raise RuntimeError(
                    f"GET {self._base_url}/render returned HTTP {status}; the "
                    f"SPA shell never loaded, so the /render route could not "
                    f"mount. Check that a server-side shell passthrough for "
                    f"/render is registered in app/routes/page_routes.py."
                )

            # Inject the spec via the imperative API. Tell the in-page
            # harness exactly how long it has to render so its safety
            # timeout fires before Playwright's wait_for_function does.
            spec_with_timeout = {**spec, "renderTimeoutMs": timeout_ms}
            spec_json = json.dumps(spec_with_timeout)

            # Wait for DiagramRenderPage to define the injector before calling
            # it.  Calling it directly turns *any* mount failure -- 404 shell,
            # a stale bundle, a lazy-chunk load error, a crash in a provider
            # above the component -- into the same misleading "not a function"
            # TypeError, pointing at the frontend when the fault is elsewhere.
            try:
                await page.wait_for_function(
                    "() => typeof window.__renderDiagram === 'function'",
                    timeout=timeout_ms,
                )
            except Exception as inject_err:
                diag = await self._collect_diagnostics(
                    page, spec, console_log, pageerror_log
                )
                logger.error(
                    "window.__renderDiagram was never defined. Diagnostics: %s",
                    diag,
                )
                raise RuntimeError(
                    f"window.__renderDiagram was never defined after "
                    f"{timeout_ms}ms -- DiagramRenderPage did not mount. "
                    f"page_url={page.url!r} "
                    f"console_tail={diag.get('console_tail')!r} "
                    f"pageerrors={diag.get('pageerrors')!r}"
                ) from inject_err

            success = await page.evaluate(
                f"window.__renderDiagram({json.dumps(spec_json)})"
            )
            if not success:
                error = await page.get_attribute("#diagram-render-root", "data-error")
                raise RuntimeError(f"Spec injection failed: {error}")

            # Wait for the render to complete.
            # The in-page safety timeout (DiagramRenderPage) is 30s and starts
            # after React mounts the container — give Playwright extra headroom
            # so the in-page timer always fires first and sets a terminal status.
            try:
                await page.wait_for_function(
                    """() => {
                        const root = document.getElementById('diagram-render-root');
                        const status = root?.getAttribute('data-render-status');
                        return status === 'complete' || status === 'error';
                    }""",
                    timeout=timeout_ms + 5_000,
                )
            except Exception as wait_err:
                # Playwright's wait timed out before the page reached a
                # terminal status. Pull every diagnostic the page exposes
                # so the caller can see *why*.
                diag = await self._collect_diagnostics(
                    page, spec, console_log, pageerror_log
                )
                logger.error(
                    "Diagram render wait_for_function timed out (type=%s). "
                    "Diagnostics: %s",
                    spec.get("type"),
                    diag,
                )
                raise RuntimeError(
                    f"Diagram render timed out after {timeout_ms + 5000}ms "
                    f"(type={spec.get('type')}). "
                    f"page_status={diag.get('render_status')!r} "
                    f"last_event={diag.get('last_event')!r} "
                    f"elapsed_ms={diag.get('elapsed_ms')!r} "
                    f"page_error={diag.get('page_error')!r} "
                    f"dom_counts={diag.get('dom_counts')!r} "
                    f"console_tail={diag.get('console_tail')!r} "
                    f"pageerrors={diag.get('pageerrors')!r}"
                ) from wait_err

            # Check for errors
            render_status = await page.get_attribute(
                "#diagram-render-root", "data-render-status"
            )
            if render_status == "error":
                error_msg = await page.get_attribute(
                    "#diagram-render-root", "data-error"
                )
                diag = await self._collect_diagnostics(
                    page, spec, console_log, pageerror_log
                )
                logger.error(
                    "Diagram render reported error (type=%s): %s. Diagnostics: %s",
                    spec.get("type"),
                    error_msg,
                    diag,
                )
                raise RuntimeError(f"Diagram render failed: {error_msg}")

            # Console diagnostics for a render that DID complete. A
            # successful render can still log fixup-layer warnings/errors
            # (e.g. drawio ELK layout fallback, mermaid auto-quote repair)
            # that indicate the output may not faithfully represent the
            # spec even though it produced an image. Surface them here
            # rather than only on the timeout/error diagnostic path.
            console_summary = self._classify_console_log(console_log)
            diagnostics: dict[str, Any] = {
                "console_warnings": console_summary["warnings"],
                "console_errors": console_summary["errors"],
                "pageerrors": pageerror_log,
            }

            # Capture the output
            container = page.locator("#diagram-render-container")

            if format == "svg":
                svg_content = await container.evaluate(
                    """el => {
                        const svg = el.querySelector('svg');
                        return svg ? svg.outerHTML : null;
                    }"""
                )
                if svg_content:
                    return svg_content.encode("utf-8"), diagnostics
                # Fall through to PNG if no SVG found
                logger.info("No SVG element found, falling back to PNG screenshot")

            # PNG screenshot of just the diagram container.
            # A full-element screenshot captures the ENTIRE natural size of the
            # diagram, which for an extreme layout (deep TB inheritance chain,
            # very wide record/method identifier, huge grid) can exceed the
            # downstream 8000px vision-pipeline cap. Clamp before returning so a
            # pathologically large-but-otherwise-valid render is downscaled
            # instead of rejected. See clamp_png_dimensions / Issue 42 defect 3.
            # Fit an oversized natural layout into a capture-safe surface
            # BEFORE the screenshot. The container is a bounded, overflow-
            # clipped window; without this, a diagram larger than the window is
            # captured as a clipped sliver (D-164 mermaid, D-219 packet) or, past
            # Chromium's rasterisation ceiling, fails to capture at all (D-165).
            # compute_capture_fit only reports needs_fit when the natural content
            # actually exceeds what the container shows, so a diagram that already
            # fits keeps the byte-identical as-is capture path below.
            try:
                measure = await container.evaluate(_CAPTURE_MEASURE_JS)
            except Exception as measure_err:  # pragma: no cover - defensive
                logger.warning(
                    "capture measure step failed (%s); capturing as-is",
                    measure_err,
                )
                measure = None
            if isinstance(measure, dict) and measure.get("naturalWidth") \
                    and measure.get("naturalHeight"):
                needs_fit, scale, _tw, _th = compute_capture_fit(
                    measure.get("naturalWidth"),
                    measure.get("naturalHeight"),
                    measure.get("renderedWidth") or 0,
                    measure.get("renderedHeight") or 0,
                )
                if needs_fit:
                    logger.info(
                        "Oversized render (%sx%s natural vs %sx%s shown); "
                        "scaling to fit (scale=%.4f) before capture so the "
                        "whole diagram is captured instead of clipped.",
                        measure.get("naturalWidth"),
                        measure.get("naturalHeight"),
                        measure.get("renderedWidth"),
                        measure.get("renderedHeight"),
                        scale,
                    )
                    try:
                        # D-301/D-304: never resize a Plotly figure's stacked
                        # SVG layers individually; only single-SVG engines get
                        # the viewBox/size rewrite. Container-level unclip +
                        # scale still applies for Plotly.
                        rewrite_svg = capture_should_rewrite_svg(measure)
                        await container.evaluate(
                            _CAPTURE_FIT_JS,
                            {"scale": scale, "rewriteSvg": rewrite_svg},
                        )
                        # Let layout settle after unclipping/scaling.
                        await page.wait_for_timeout(50)
                    except Exception as fit_err:  # pragma: no cover - defensive
                        logger.warning(
                            "capture fit step failed (%s); capturing as-is",
                            fit_err,
                        )

            png_bytes = await container.screenshot(type="png")
            return clamp_png_dimensions(png_bytes), diagnostics

        finally:
            await page.close()


# -- Module-level singleton ----------------------------------------------

_renderer_instance: Optional[DiagramRenderer] = None
_renderer_lock = asyncio.Lock()


async def get_diagram_renderer(server_port: int = 6969) -> DiagramRenderer:
    """Get or create the singleton DiagramRenderer instance."""
    global _renderer_instance
    async with _renderer_lock:
        if _renderer_instance is None:
            _renderer_instance = await DiagramRenderer.create(server_port)
        return _renderer_instance


async def shutdown_diagram_renderer() -> None:
    """Shut down the singleton renderer (call during app shutdown)."""
    global _renderer_instance
    if _renderer_instance:
        await _renderer_instance.close()
        _renderer_instance = None
