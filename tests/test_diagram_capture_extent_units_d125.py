"""Regression test for the D-125 small-LR-graph viewport bug (gfx-sweep
group G-4686aa).

D-125 is a RECOVERY defect: eight lexically-malformed graphviz specs (markdown
fence, JSON envelope, smart/single quotes, dialect mix, comma node-group,
unquoted multiword value) must survive ``repairGraphvizSource`` and reach
layout instead of dying as a silent 30s watchdog timeout. That lexical repair
was verified once and is confirmed working (the frontend jest suite
graphvizG4686.test.ts covers all eight).

It then REGRESSED -- but not in the repair. Wave-4 re-sweep evidence showed the
five ``rankdir=LR`` specs (w4-01, w4-03, w4-10, w4-13, w4-15) captured blank or
cropped ("blank-canvas-content-not-rendered" / "viewport-crop-content-lost"),
while the ``rankdir=TB`` specs (w4-02, w4-09, w4-14) rendered. A control in the
triage proved it: the SAME graph rendered blank in LR and fine in TB, so the
variable is layout aspect, not the recovery.

Root cause: ``_CAPTURE_MEASURE_JS`` measured the drawing's natural extent from
the SVG ``getBBox`` / ``viewBox`` -- in SVG USER UNITS, which for graphviz are
POINTS, not CSS px (Viz.js declares ``width="Npt"`` and numbers its viewBox in
points). ``compute_capture_fit`` compares that against the px ``rendered*`` dims
and against the px ``min_dim`` legibility floor (800). A wide-short LR graph has
a natural extent of roughly 300x50 pt, so it read as a sub-pixel island far
under the 800px floor; the undersize-upscale branch fired, forcing the
already-correctly-sized ~1280px drawing back to its point count as px and
rescaling it to a thin strip that captured blank/cropped. A tall-narrow TB
graph, being closer to square, escaped the worst of it.

``measured_drawing_extent_px`` is the fix: prefer the drawing's ON-SCREEN px
box (the SVG element's client rect, equal to the drawing in the no-overflow
branch) as the unit-correct natural extent. The test below verifies the
DIRECTION -- with the buggy user-unit (point) extent ``compute_capture_fit``
mis-fires the undersize-upscale, and with the on-screen px extent it does not.

The helper is NEW, so importing it fails against the pre-fix module.
"""

from app.services.diagram_renderer import (
    compute_capture_fit,
    measured_drawing_extent_px,
    fit_rewrite_svg_px_extent,
    compute_capture_fit as _ccf,  # noqa: F401  (re-export alias for clarity)
    CAPTURE_MIN_DIMENSION_PX,
    _CAPTURE_MEASURE_JS,
    _CAPTURE_FIT_JS,
)


# A small `rankdir=LR` graph (3 nodes in one horizontal rank). Viz.js lays it
# out at ~300x50 POINTS; the graphviz plugin renders it on-screen filling the
# container width at ~1280x214 CSS px.
LR_NATURAL_UU = (300.0, 50.0)      # getBBox / viewBox extent, in POINTS
LR_ONSCREEN_PX = (1280.0, 214.0)   # SVG element's client rect, in CSS px
SHOWN_PX = (1280.0, 214.0)         # what the container actually shows


def test_point_extent_wrongly_triggers_undersize_upscale():
    """The pre-fix path fed the user-unit (point) extent to the fit decision.
    300pt is under the 800px floor, so the undersize-upscale mis-fires: a
    correctly-sized drawing would be forced smaller and captured as a strip."""
    assert LR_NATURAL_UU[0] < CAPTURE_MIN_DIMENSION_PX
    needs_fit, scale, _tw, _th = compute_capture_fit(
        LR_NATURAL_UU[0], LR_NATURAL_UU[1], SHOWN_PX[0], SHOWN_PX[1]
    )
    assert needs_fit is True
    assert scale > 1.0  # spurious upscale of an already-correctly-sized graph


def test_onscreen_px_extent_does_not_mis_fire():
    """The fix measures the drawing's true on-screen px extent (1280x214).
    That is >= the floor and equals what is shown, so no fit is needed and the
    drawing is captured as-is (the LR graph renders instead of collapsing)."""
    px_w, px_h = measured_drawing_extent_px(
        LR_NATURAL_UU[0], LR_NATURAL_UU[1], LR_ONSCREEN_PX[0], LR_ONSCREEN_PX[1]
    )
    assert (px_w, px_h) == LR_ONSCREEN_PX
    needs_fit, scale, _tw, _th = compute_capture_fit(px_w, px_h, SHOWN_PX[0], SHOWN_PX[1])
    assert needs_fit is False
    assert scale == 1.0


def test_helper_falls_back_to_user_units_without_px_box():
    """When no on-screen px box is available the helper preserves the historic
    user-unit extent, so engines/paths without a client rect are unchanged."""
    assert measured_drawing_extent_px(300.0, 50.0, 0, 0) == (300.0, 50.0)
    assert measured_drawing_extent_px(300.0, 50.0, None, None) == (300.0, 50.0)


def test_px_measurement_is_monotonic_for_square_graphs():
    """A near-square graph (e.g. rankdir=TB) whose user units already track px
    (mermaid/vega) is unchanged: passing the same numbers as px returns them."""
    assert measured_drawing_extent_px(400.0, 380.0, 400.0, 380.0) == (400.0, 380.0)


# ---------------------------------------------------------------------------
# D-125 regression (run a9f37149): the second, rewrite-side mechanism.
#
# The measure-side fix above was verified once and then D-125 REGRESSED again
# on the SAME five rankdir=LR specs. The residual cause was on the capture
# REWRITE / declared-size path, not the measure branch:
#
#   ``_CAPTURE_MEASURE_JS`` / ``_CAPTURE_FIT_JS`` folded the SVG's DECLARED px
#   size read as ``svg.width.baseVal.value``. But the graphviz plugin REMOVES
#   the width/height attributes and drives size via CSS, so for a graphviz SVG
#   ``baseVal.value`` does not return 0 — it synthesizes the SVG default (100%
#   resolved to the viewport width, ~1280). On the rewrite side that fabricated
#   ~1280 was folded into the POINT-unit union ``w``/``h`` (so w := max(300,
#   1280) = 1280) and then multiplied by the px-per-unit ratio (~1280/300 ≈
#   4.27) -> the SVG was sized to ~5461px and captured cropped/oversized.
#
# The pure helper ``fit_rewrite_svg_px_extent`` is correct: it takes the
# DECLARED px width as a parameter (0 when none). The bug was the JS glue
# SOURCING that parameter from ``baseVal.value`` instead of the attribute. The
# fix reads the width/height ATTRIBUTE (0 when absent, as for graphviz).
# ---------------------------------------------------------------------------

# The wide-short LR graph again: viewBox 300x50 pt, shown filling the container
# at ~1280 px. cur_vb_w is the pre-expansion viewBox width (300 pt); the SVG's
# on-screen client width is ~1280 px.
LR_UNION_UU = (300.0, 50.0)   # viewBox+getBBox union, in POINTS (user units)
LR_CUR_VB_W = 300.0           # current viewBox width, in POINTS
LR_CLIENT_PX_W = 1280.0       # SVG element on-screen client width, in px
# The fabricated declared width baseVal.value returns for a graphviz SVG whose
# width attribute the plugin removed: the SVG default 100% resolved to the
# viewport width.
FABRICATED_DECL_PX = 1280.0


def test_rewrite_with_absent_declared_width_keeps_onscreen_scale():
    """graphviz has NO width attribute after the plugin, so declared_px_w is 0.
    The rewrite sizes the SVG to union * (client/vbw) = 300 * (1280/300) ≈ 1280
    px wide — the correct on-screen size, not a collapsed strip. This is the
    value the fixed JS now passes (attribute absent -> 0)."""
    px_w, px_h = fit_rewrite_svg_px_extent(
        LR_UNION_UU[0], LR_UNION_UU[1], LR_CUR_VB_W, LR_CLIENT_PX_W, 0.0
    )
    assert abs(px_w - 1280.0) < 1.0
    # >= the 800px floor and consistent with the shown box: no mis-fire.
    needs_fit, scale, _tw, _th = compute_capture_fit(px_w, px_h, 1280.0, 214.0)
    assert needs_fit is False
    assert scale == 1.0


def test_rewrite_with_fabricated_declared_width_would_crop():
    """The PRE-FIX JS sourced declared_px_w from baseVal.value = ~1280 for a
    graphviz SVG (attribute removed). Feeding that fabricated px as the union's
    own units (the old fold `w := max(union, declared)` then `* ratio`) blows
    the drawing up to thousands of px — the cropped/oversized capture. This
    encodes the regression the attribute-only read prevents."""
    # Old fold treated the fabricated px as a user-unit and MAX-folded it into
    # the union width, so the union width became the fabricated 1280 (pt),
    # while the ratio reference also saw it -> a self-reinforcing blow-up.
    union_w_polluted = max(LR_UNION_UU[0], FABRICATED_DECL_PX)  # 1280 (as "pt")
    px_w, _px_h = fit_rewrite_svg_px_extent(
        union_w_polluted, LR_UNION_UU[1], LR_CUR_VB_W,
        LR_CLIENT_PX_W, FABRICATED_DECL_PX,
    )
    # ~1280 * (1280/300) ≈ 5461 px — a grossly oversized surface, > 4x the shown
    # box, in which the real ~300pt drawing is a tiny island -> cropped/blank
    # capture. Contrast with the correct path above (~1280 px, needs_fit False).
    assert px_w > LR_CLIENT_PX_W * 3
    needs_fit, _scale, _tw, _th = compute_capture_fit(px_w, 214.0, 1280.0, 214.0)
    # The oversized surface no longer matches the shown box, so the capture path
    # is forced to fit/unclip a surface many times larger than the drawing.
    assert needs_fit is True


def test_capture_js_reads_declared_size_from_attribute_not_basevalue():
    """Contract guard for the fix: neither capture-JS block may source the
    declared SVG size from ``svg.width.baseVal.value`` (which fabricates a
    viewport-sized default for the attribute-less graphviz SVG). Both must read
    the width/height ATTRIBUTE. Fails against the pre-fix module."""
    for js in (_CAPTURE_MEASURE_JS, _CAPTURE_FIT_JS):
        # The exact pre-fix code read (guarded form) must be gone.
        assert "svg.width.baseVal && svg.width.baseVal.value" not in js
        # The declared size is now read from the width/height ATTRIBUTE
        # (directly for the ratio reference, or via the declPx('width'/'height')
        # helper which calls svg.getAttribute(nm) in the fold blocks).
        assert "svg.getAttribute(" in js

