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
    CAPTURE_MIN_DIMENSION_PX,
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
