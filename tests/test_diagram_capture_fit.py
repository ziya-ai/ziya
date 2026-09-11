"""Regression tests for compute_capture_fit (gfx-sweep group G-38).

Three defects share one root cause in the headless capture path — the harness
screenshots the bounded ``#diagram-render-container`` at natural size, so a
diagram whose layout is larger than that window is captured as a clipped sliver
(D-164 mermaid oversize graphs, D-219 packet tall canvas) or, once the natural
surface passes Chromium's rasterisation ceiling, fails to capture at all
(D-165, "Unable to capture screenshot"). The fix measures the natural layout,
and when it overflows the shown window (or the capture ceiling) scales it DOWN
to fit before the screenshot — scaled-to-fit (small but complete) instead of
clipped-away. ``compute_capture_fit`` is the pure decision at the centre of
that path.

These tests import the REAL helper from app.services.diagram_renderer (not a
re-implementation), so they FAIL against the pre-fix code: the symbol did not
exist there (ImportError), i.e. a test that would pass against unpatched code
cannot exist for a brand-new function — the direction is guaranteed.

Structural (geometry) defect: geometry precedes painting and the fit is
theme-independent, so these assert the deterministic scale/overflow maths
rather than colour in either theme (both-theme render verification is deferred
to the shared build+render stage).
"""
from __future__ import annotations

from app.services.diagram_renderer import (
    CAPTURE_MAX_DIMENSION_PX,
    CAPTURE_MIN_DIMENSION_PX,
    CAPTURE_UPSCALE_MAX,
    compute_capture_fit,
)


def test_fits_when_content_within_window_no_fit():
    # Natural content equals what the container shows and is under the ceiling:
    # capture must be left byte-identical (needs_fit False, scale 1.0). This is
    # the invariant that keeps every currently-passing small diagram unchanged.
    needs_fit, scale, tw, th = compute_capture_fit(800, 600, 800, 600)
    assert needs_fit is False
    assert scale == 1.0
    assert (tw, th) == (800, 600)


def test_clipped_content_triggers_fit_even_under_ceiling():
    # Natural layout is larger than the shown window but still under the pixel
    # ceiling: this is the packet vertical-crop / mermaid sliver case. We must
    # flag needs_fit so the DOM is unclipped and the whole diagram captured,
    # even though no downscale (scale==1.0) is required to stay under the cap.
    needs_fit, scale, tw, th = compute_capture_fit(1200, 4000, 1200, 960)
    assert needs_fit is True
    assert scale == 1.0  # 4000 < CAPTURE_MAX_DIMENSION_PX -> no shrink needed
    assert (tw, th) == (1200, 4000)


def test_over_ceiling_is_scaled_down_to_fit():
    # Natural height 3000 fits, but width 12000 exceeds the 6000 ceiling: must
    # scale by 0.5 so the largest axis lands exactly on the ceiling, preserving
    # aspect ratio. This is what keeps Chromium's capture from failing (D-165).
    needs_fit, scale, tw, th = compute_capture_fit(12000, 3000, 1280, 960)
    assert needs_fit is True
    assert scale == 0.5
    assert tw == CAPTURE_MAX_DIMENSION_PX  # 6000
    assert th == 1500


# --- D-012 (gfx-sweep G-12): legibility-floor UPSCALE for undersize captures.
# The prior fix above only ever scaled DOWN; an undersize diagram sitting inside
# the far-larger bounded capture window was screenshotted as a tiny, sub-pixel
# surface (packet "undersize-canvas"). These assert the new scale>1 branch. They
# FAIL against the pre-fix code: it returned scale==1.0 / needs_fit False for
# small content (and did not export CAPTURE_MIN_DIMENSION_PX at all), so a test
# that would pass unpatched cannot exist here -- the direction is guaranteed.


def test_undersize_content_is_upscaled_to_legibility_floor():
    # Largest natural axis (300) is below the floor and the content is not
    # clipped/over-ceiling: it must be enlarged toward the floor so its text is
    # legible in the captured raster. 300 -> min(cap, 800/300=2.667) = 2.667x.
    needs_fit, scale, tw, th = compute_capture_fit(300, 200, 1200, 1000)
    assert needs_fit is True
    assert scale > 1.0
    assert abs(scale - (CAPTURE_MIN_DIMENSION_PX / 300.0)) < 1e-9
    assert max(tw, th) == CAPTURE_MIN_DIMENSION_PX  # long axis reaches the floor
    assert (tw, th) == (CAPTURE_MIN_DIMENSION_PX, int(200 * scale))


def test_microscopic_content_upscale_is_capped():
    # A microscopic surface must not be blown up arbitrarily: 100px would need
    # 8x to reach the 800 floor, but the cap bounds it to CAPTURE_UPSCALE_MAX.
    needs_fit, scale, tw, th = compute_capture_fit(100, 100, 1200, 1000)
    assert needs_fit is True
    assert scale == CAPTURE_UPSCALE_MAX
    assert (tw, th) == (int(100 * CAPTURE_UPSCALE_MAX), int(100 * CAPTURE_UPSCALE_MAX))
    # Capped target stays comfortably under the capture ceiling.
    assert max(tw, th) < CAPTURE_MAX_DIMENSION_PX


def test_content_at_or_above_floor_is_not_upscaled():
    # The paired other-direction guard for the theme-independent behaviour: a
    # diagram whose long axis already meets the floor is left byte-identical
    # (no upscale), so ordinary/large diagrams are untouched by the new branch.
    needs_fit, scale, tw, th = compute_capture_fit(
        CAPTURE_MIN_DIMENSION_PX, 600, 1200, 1000
    )
    assert scale == 1.0
    assert needs_fit is False
    assert (tw, th) == (CAPTURE_MIN_DIMENSION_PX, 600)


def test_clipped_undersize_prefers_unclip_over_upscale():
    # If small content is nonetheless clipped by an even-smaller shown window,
    # the clip (data loss) takes priority: unclip at natural size, do not also
    # upscale. Guards against the two fit reasons fighting.
    needs_fit, scale, tw, th = compute_capture_fit(300, 200, 300, 90)
    assert needs_fit is True
    assert scale == 1.0
    assert (tw, th) == (300, 200)


def test_over_ceiling_even_when_reported_rendered_equals_natural():
    # Defensive: if the container somehow reports rendered==natural but natural
    # exceeds the ceiling, we still must scale (over_cap path) or the capture
    # overruns Chromium. Largest axis is height here.
    needs_fit, scale, tw, th = compute_capture_fit(2000, 9000, 2000, 9000)
    assert needs_fit is True
    assert scale == CAPTURE_MAX_DIMENSION_PX / 9000.0
    assert th == CAPTURE_MAX_DIMENSION_PX
    assert tw == int(2000 * scale)


def test_tolerance_absorbs_subpixel_difference():
    # A 1-2px difference (rounding/border chrome) is NOT overflow: must not fire
    # the fit path, or every ordinary diagram would take the mutation branch.
    needs_fit, _scale, _tw, _th = compute_capture_fit(802, 601, 800, 600)
    assert needs_fit is False


def test_invalid_measurements_are_safe_noop():
    # Zero / negative / non-numeric measurements must degrade to "capture as-is"
    # (needs_fit False, scale 1.0) — a guard must never destroy the render it
    # protects.
    assert compute_capture_fit(0, 0, 0, 0) == (False, 1.0, 0, 0)
    assert compute_capture_fit(-5, 100, 100, 100) == (False, 1.0, 0, 0)
    assert compute_capture_fit(None, None, None, None) == (False, 1.0, 0, 0)


def test_target_dims_are_floored_at_one_pixel():
    # An extreme aspect ratio must not floor a target dimension to 0px.
    needs_fit, scale, tw, th = compute_capture_fit(60000, 3, 100, 100)
    assert needs_fit is True
    assert tw == CAPTURE_MAX_DIMENSION_PX
    assert th >= 1


# --- D-249 (gfx-sweep G-HOST-SIZING): the SHARED host-viewport crop/downscale
# behind graphviz, mermaid, packet and circuitikz. These bind the abstract
# geometry maths above to the concrete cross-engine spec scenarios triage
# recorded for D-249, so the group's fix is guarded at the exact shapes it was
# filed for. All import the real helper, so they FAIL (ImportError) against the
# pre-fix tree where compute_capture_fit did not exist — the guaranteed
# fail-without / pass-with direction; a test that passed unpatched cannot exist
# for a symbol the unpatched code lacks.


def test_d249_wide_graphviz_landscape_scaled_down_not_clipped():
    # graphviz-w2-03/04 style: a ~3580px-wide landscape rendered into a
    # ~1230px window. Pre-fix this was captured as the visible left sliver
    # (right side clipped away). It is under the 6000 ceiling, so the fix must
    # UNCLIP and capture the whole width at natural size (scale 1.0), not shrink
    # it — the caller's _CAPTURE_FIT_JS then forces the SVG past its
    # max-width:100% plugin cap so the full extent is inside the shot.
    needs_fit, scale, tw, th = compute_capture_fit(3580, 900, 1230, 900)
    assert needs_fit is True
    assert scale == 1.0
    assert (tw, th) == (3580, 900)


def test_d249_huge_mermaid_state_graph_downscaled_under_ceiling():
    # mermaid-w2-* oversize state graph whose natural layout blows past the
    # capture ceiling on the long axis: pre-fix Chromium failed the capture
    # outright (D-165). Fix must scale the long axis down onto the ceiling,
    # aspect preserved, so the whole diagram is captured (small but complete).
    needs_fit, scale, tw, th = compute_capture_fit(9000, 4500, 1230, 960)
    assert needs_fit is True
    assert scale == CAPTURE_MAX_DIMENSION_PX / 9000.0
    assert tw == CAPTURE_MAX_DIMENSION_PX
    assert th == int(4500 * scale)
    assert max(tw, th) <= CAPTURE_MAX_DIMENSION_PX


def test_d249_tall_packet_canvas_unclipped_at_natural_size():
    # packet-w2-* tall canvas: taller than the shown window, under the ceiling.
    # Pre-fix the bottom rows were cropped (D-219). Fix must unclip the full
    # height at natural size without any downscale.
    needs_fit, scale, tw, th = compute_capture_fit(1000, 3200, 1000, 720)
    assert needs_fit is True
    assert scale == 1.0
    assert (tw, th) == (1000, 3200)


def test_d249_undersize_packet_grid_upscaled_to_legibility_floor():
    # packet "undersize-canvas": a small authored surface inside the far-larger
    # bounded window renders sub-pixel text. Fix must upscale toward the
    # legibility floor (scale>1), the missing branch pre-fix.
    needs_fit, scale, tw, th = compute_capture_fit(270, 180, 1230, 960)
    assert needs_fit is True
    assert scale > 1.0
    assert max(tw, th) == CAPTURE_MIN_DIMENSION_PX
