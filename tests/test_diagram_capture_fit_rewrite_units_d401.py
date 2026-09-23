"""Regression test for D-401 (blank-canvas-content-not-rendered), the
rewrite-side twin of the D-125 small-LR-graph viewport bug (gfx-sweep group
G-1d832a).

D-125 corrected the MEASURE side: ``measured_drawing_extent_px`` feeds the fit
decision the drawing's on-screen px extent instead of its SVG user-unit (point)
extent, so a wide-short graphviz graph no longer reads as a sub-pixel island.

The REWRITE side had the mirror bug. When the capture DOES need a fit,
``_CAPTURE_FIT_JS`` expands the SVG viewBox to the union of the declared viewBox
and the content ``getBBox`` (``content_extent_from_boxes``) so off-canvas
content is revealed — that union is in SVG USER UNITS. It then sized
``svg.style.width`` to the raw union number as *px*. For graphviz (1 user unit =
1pt) that collapses the drawing back to its point count; once
``compute_capture_fit``'s px-derived ``scale`` is applied on top via the
container transform, the drawing is rescaled to a thin strip that captures blank
or cropped — the same mechanism D-125 fixed on the measure side.

``fit_rewrite_svg_px_extent`` is the fix: size the SVG to the union scaled by
the SVG's current on-screen px-per-user-unit ratio (client width / current
viewBox width), preserving the on-screen scale while the widened viewBox reveals
the overflow. The helper is NEW, so importing it fails against the pre-fix
module.
"""

from app.services.diagram_renderer import fit_rewrite_svg_px_extent


# A graphviz drawing whose CURRENT viewBox is 300x50 POINTS but which the plugin
# has rendered on-screen at 1200px wide (px-per-unit ratio = 4.0). The capture
# fit expands the viewBox to a slightly larger union (say 320x60 units) to
# reveal a label that overflowed the declared box.
CUR_VB_W = 300.0
SVG_CLIENT_W = 1200.0          # on-screen px width (ratio = 1200/300 = 4.0)
UNION_UU = (320.0, 60.0)       # viewBox∪getBBox extent, in POINTS


def test_point_union_scaled_to_onscreen_px():
    """The union must be sized in px at the drawing's on-screen scale (x4),
    NOT collapsed to its raw point count."""
    px_w, px_h = fit_rewrite_svg_px_extent(
        UNION_UU[0], UNION_UU[1], CUR_VB_W, SVG_CLIENT_W
    )
    assert (round(px_w), round(px_h)) == (1280, 240)  # 320*4, 60*4


def test_raw_union_would_collapse_the_drawing():
    """Direction check: the pre-fix behaviour sized the SVG to the raw union
    number (320px) — under a third of the on-screen width — which is what
    produced the blank/thin-strip capture. The fix must NOT return that."""
    px_w, _ = fit_rewrite_svg_px_extent(
        UNION_UU[0], UNION_UU[1], CUR_VB_W, SVG_CLIENT_W
    )
    assert px_w > UNION_UU[0]          # not the raw point count
    assert px_w == UNION_UU[0] * (SVG_CLIENT_W / CUR_VB_W)


def test_px_unit_engine_unchanged():
    """A px-unit engine (mermaid/vega) whose user units already track px has
    ratio ~= 1, so the union px extent equals the union user-unit extent."""
    px_w, px_h = fit_rewrite_svg_px_extent(800.0, 600.0, 800.0, 800.0)
    assert (px_w, px_h) == (800.0, 600.0)


def test_falls_back_to_union_without_client_box():
    """When no usable current viewBox / client width is available the helper
    keeps the historic behaviour (ratio 1) rather than zeroing the size."""
    assert fit_rewrite_svg_px_extent(320.0, 60.0, 0, 0) == (320.0, 60.0)
    assert fit_rewrite_svg_px_extent(320.0, 60.0, None, None) == (320.0, 60.0)


def test_degenerate_union_is_safe():
    """A non-positive union extent returns a non-negative, non-crashing size."""
    assert fit_rewrite_svg_px_extent(0, 0, 300.0, 1200.0) == (0.0, 0.0)
