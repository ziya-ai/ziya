"""Regression tests for content_extent_from_boxes (gfx-sweep group G-0050a2,
defects D-117 / D-119 "viewport-crop-content-lost").

Several engines place content OUTSIDE their declared SVG viewBox, and the
pre-fix headless capture measured / forced the viewBox ALONE, so the overflow
was screenshotted CROPPED instead of scaled-to-fit:

  * a 340-char graphviz node label overflowing to negative x   (graphviz-w2-06)
  * a size="60,60!" ratio=fill forced upscale pushing nodes off (graphviz-w2-08)
  * a 60-field record whose vertical stack runs off the bottom  (graphviz-w2-13)
  * a 120-node circo ring whose extent exceeds the box          (graphviz-w2-14)

``content_extent_from_boxes`` is the pure geometry at the centre of the fix: it
returns the UNION of the declared viewBox and the rendered content bounding box
so the capture path never crops. The identical arithmetic is mirrored in
``_CAPTURE_MEASURE_JS`` / ``_CAPTURE_FIT_JS``, which apply the union as the SVG's
new viewBox (origin shifted to reveal negative-coordinate overflow) before the
screenshot.

The helper is NEW, so importing it fails against the pre-fix code (ImportError):
a test that would pass against the unpatched tree cannot exist — the fail-without
/ pass-with direction is guaranteed. Structural (geometry) defect, theme-
independent; both-theme render verification is deferred to the shared
build+render stage.
"""
from __future__ import annotations

from app.services.diagram_renderer import (
    content_extent_from_boxes,
    _CAPTURE_MEASURE_JS,
    _CAPTURE_FIT_JS,
)


def test_content_within_viewbox_returns_viewbox_unchanged():
    # Content fully inside the declared box: union == viewBox, so a diagram
    # that does not overflow is measured/captured exactly as before.
    assert content_extent_from_boxes(0, 0, 200, 120, 10, 10, 50, 50) == (0, 0, 200, 120)


def test_left_overflow_shifts_origin_negative_and_widens():
    # graphviz-w2-06: a long label overflows to negative x. The union origin
    # must move negative (so the fit JS's viewBox reveals it) and the width
    # must grow to include it — never clip it to the box's [0, 100] range.
    min_x, min_y, w, h = content_extent_from_boxes(0, 0, 100, 100, -40, 0, 90, 100)
    assert min_x == -40
    assert w == 140  # from x=-40 to x=100
    assert (min_y, h) == (0, 100)


def test_top_overflow_and_bottom_overflow_both_captured():
    # graphviz-w2-08 (nodes pushed above the box) + w2-13 (record runs off the
    # bottom): union must extend in both vertical directions.
    min_x, min_y, w, h = content_extent_from_boxes(0, 0, 300, 200, 0, -50, 300, 400)
    assert min_y == -50
    assert h == 400  # from y=-50 to y=350 (max of 200 and -50+400)


def test_ring_larger_than_viewbox_uses_content_extent():
    # graphviz-w2-14: a 120-node circo ring whose true extent exceeds the
    # declared box (only a quadrant fit before). The union must report the
    # larger content extent so the whole ring is captured.
    _, _, w, h = content_extent_from_boxes(0, 0, 400, 400, 0, 0, 900, 900)
    assert (w, h) == (900, 900)


def test_missing_viewbox_falls_back_to_bbox():
    assert content_extent_from_boxes(0, 0, 0, 0, 5, 5, 30, 40) == (5, 5, 30, 40)


def test_missing_bbox_falls_back_to_viewbox():
    assert content_extent_from_boxes(0, 0, 120, 80, 0, 0, 0, 0) == (0, 0, 120, 80)


def test_both_degenerate_is_zero_noop():
    # Both boxes zero-area -> zeros; the caller treats that as "capture as-is".
    assert content_extent_from_boxes(0, 0, 0, 0, 0, 0, 0, 0) == (0, 0, 0, 0)


def test_invalid_inputs_are_safe():
    # Non-numeric measurements must not raise; they degrade to the other box or
    # to a no-op, mirroring the defensive posture of compute_capture_fit.
    assert content_extent_from_boxes("x", None, "y", None, 5, 5, 30, 40) == (5, 5, 30, 40)


def test_capture_js_applies_union_extent():
    # The runtime application must consult getBBox (not the viewBox alone) in
    # BOTH the measure and fit steps, and the fit step must rewrite the SVG
    # viewBox to the union origin/extent. A regression that drops the getBBox
    # union (reverting to viewBox-only) would re-crop overflow content.
    assert "getBBox" in _CAPTURE_MEASURE_JS
    assert "getBBox" in _CAPTURE_FIT_JS
    assert "setAttribute('viewBox'" in _CAPTURE_FIT_JS


if __name__ == "__main__":
    import sys
    import subprocess

    sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", __file__]))
