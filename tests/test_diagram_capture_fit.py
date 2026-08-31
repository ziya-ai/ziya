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


def test_never_upscales_small_content():
    # A tiny diagram must never be enlarged: scale stays 1.0 and there is no fit.
    needs_fit, scale, tw, th = compute_capture_fit(120, 90, 120, 90)
    assert scale == 1.0
    assert needs_fit is False


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
