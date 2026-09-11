"""Regression tests for gfx-sweep group G-5b9ba1 (vega-lite tall / composite clip).

Two of the group's defects are structural capture-clip cases in the SAME family
already resolved for mermaid/packet by the fit-to-viewport path:

* D-256 (vega-lite-w2-07): authored height 2400 with 120 horizontal bars. The
  full-height canvas is painted but only a ~990px window in the MIDDLE was
  captured — the bounded ``#diagram-render-container`` clips a diagram taller
  than the shown window unless the capture path unclips it first.
* D-257 (vega-lite-w2-09): a vconcat>hconcat>layer composite whose assembled
  view overflows its container at BOTH ends (overall title + row-1 leaf titles
  off the top, bottom row's x tick labels + axis title off the bottom;
  worst_measured_ratio 2.17). The overflow lies OUTSIDE the declared viewBox,
  so measuring the viewBox alone would screenshot it cropped.

Both are decided by the pure geometry helpers ``compute_capture_fit`` (is the
natural layout larger than the shown window?) and ``content_extent_from_boxes``
(the union of declared viewBox and rendered content, so out-of-box overflow is
measured rather than cropped). These import the REAL helpers, so they exercise
the actual capture decision the shared build+render stage runs. Structural /
theme-independent: geometry precedes painting, so both-theme render verification
is deferred to that stage.
"""
from __future__ import annotations

from app.services.diagram_renderer import (
    CAPTURE_MAX_DIMENSION_PX,
    compute_capture_fit,
    content_extent_from_boxes,
)


def test_d256_tall_canvas_is_unclipped_not_windowed():
    # vega-lite-w2-07: natural 1140x2400, but the bounded container only shows a
    # ~990px window. The natural height (2400) is under the 6000 capture ceiling,
    # so the correct outcome is: flag needs_fit (so the DOM is unclipped) and
    # capture the WHOLE 2400px at natural size (scale 1.0) rather than the middle
    # window. Against a capture path that screenshots the shown window as-is,
    # rows 000-036 and 086-119 are lost; this pins that they are not.
    natural_w, natural_h = 1140, 2400
    shown_w, shown_h = 1140, 990
    assert natural_h < CAPTURE_MAX_DIMENSION_PX
    needs_fit, scale, tw, th = compute_capture_fit(
        natural_w, natural_h, shown_w, shown_h
    )
    assert needs_fit is True
    assert scale == 1.0
    # Full height captured, not the ~990px middle window.
    assert th == natural_h
    assert th > shown_h


def test_d257_composite_both_ends_overflow_extent_is_union_not_cropped():
    # vega-lite-w2-09: the declared viewBox is the plot area, but the overall
    # title sits above it (negative y) and the bottom axis furniture below it,
    # so the assembled content runs from y=-40 to y=1060 while the viewBox is
    # only (0,0,900,900). Measuring the viewBox alone crops both ends; the union
    # must span the full extent AND move the origin negative so the top title is
    # brought inside the captured area.
    vb_x, vb_y, vb_w, vb_h = 0.0, 0.0, 900.0, 900.0
    # content bbox overflows top (y=-40) and bottom (down to y=1060).
    bb_x, bb_y, bb_w, bb_h = 0.0, -40.0, 900.0, 1100.0
    min_x, min_y, ext_w, ext_h = content_extent_from_boxes(
        vb_x, vb_y, vb_w, vb_h, bb_x, bb_y, bb_w, bb_h
    )
    # Origin moves up to reveal the clipped-off top title.
    assert min_y == -40
    assert min_x == 0
    # Extent covers the full assembled view (top overflow + plot + bottom
    # furniture), i.e. -40 .. 1060 == 1100px, taller than the declared box.
    assert ext_h == 1100
    assert ext_h > vb_h
    assert ext_w == 900

    # And that union extent, larger than the ~900px shown window, must trigger
    # the unclip-and-capture path (worst_measured_ratio ~2.17 -> here 1100/900).
    needs_fit, scale, _tw, th = compute_capture_fit(ext_w, ext_h, 900, 900)
    assert needs_fit is True
    assert th == ext_h  # whole composite captured, both ends included
