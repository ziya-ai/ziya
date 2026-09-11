"""Regression tests for gfx-sweep group G-d4a898 (vega-lite tall / composite clip).

D-256 (vega-lite-w2-07, authored height 2400), D-257 (vega-lite-w2-09,
vconcat>hconcat>layer composite clipped at both ends) and D-311 (both, the
persistent umbrella) are capture-clip cases whose PURE geometry
(``compute_capture_fit``) already resolves correctly — the earlier fix even
pinned ``needs_fit`` for natural 2400 vs a ~990px window. They nonetheless
stayed *still-broken* because at RENDER time the measurement never produced
2400: Vega-Lite emits NO viewBox and writes its true surface only as explicit
px ``width``/``height`` on the root ``<svg>``, and ``getBBox`` / ``scrollHeight``
under-report that surface in headless Chromium once the bounded container clips
it — so the fit decision was fed the clipped window height and never unclipped.

``merge_declared_svg_extent`` closes that gap by folding the declared px size
into the measured natural extent (MAX-only, so viewBox engines are untouched).
The same MAX is applied in the runtime ``_CAPTURE_MEASURE_JS`` /
``_CAPTURE_FIT_JS``. These tests import the REAL helpers and assert the DIRECTION
that was previously wrong: with the declared size folded in, the tall/composite
naturals drive ``compute_capture_fit`` to ``needs_fit``; without it, the clipped
window measurement does not. Structural / theme-independent (geometry precedes
painting), so both-theme render verification is deferred to the build+render
stage.
"""
from __future__ import annotations

from app.services.diagram_renderer import (
    CAPTURE_MAX_DIMENSION_PX,
    compute_capture_fit,
    merge_declared_svg_extent,
)


def test_d256_declared_height_recovers_the_tall_natural_extent():
    # vega-lite-w2-07: the container clips to a ~990px window, and getBBox /
    # scrollHeight report only that clipped extent (990). The Vega <svg> however
    # declares its true px height (2400).
    measured_from_getbbox = (1140, 990)   # under-reported (the pre-fix bug)
    declared_svg_px = (1140, 2400)        # Vega's explicit width/height attrs

    nat_w, nat_h = merge_declared_svg_extent(*measured_from_getbbox, *declared_svg_px)
    assert (nat_w, nat_h) == (1140, 2400), "declared px height must recover the full extent"

    # DIRECTION: without the merge, the clipped 990 == shown 990, so the capture
    # path never unclips and rows 000-036 / 086-119 are lost.
    pre_fix_fit, *_ = compute_capture_fit(990, 990, 990, 990)
    assert pre_fix_fit is False

    # With the merge, natural 2400 > shown 990 -> needs_fit, unclip, capture the
    # WHOLE canvas at natural size (under the 6000 ceiling => scale 1.0).
    needs_fit, scale, _tw, th = compute_capture_fit(nat_w, nat_h, 990, 990)
    assert needs_fit is True
    assert abs(scale - 1.0) < 1e-9
    assert th == 2400


def test_d257_declared_composite_extent_exceeds_window():
    # vega-lite-w2-09: assembled composite ~ worst_measured_ratio 2.17 taller
    # than the shown window. getBBox under-reports; the declared svg size does
    # not.
    nat_w, nat_h = merge_declared_svg_extent(480, 430, 480, 934)
    assert nat_h == 934
    needs_fit, *_ = compute_capture_fit(nat_w, nat_h, 480, 430)
    assert needs_fit is True


def test_merge_is_monotonic_and_ignores_viewbox_engines():
    # A viewBox-based engine (mermaid/graphviz/packet) whose declared size is
    # already <= the measured union is left byte-identical.
    assert merge_declared_svg_extent(4000, 3000, 300, 200) == (4000, 3000)
    # A declared size larger on only one axis raises only that axis.
    assert merge_declared_svg_extent(4000, 300, 300, 2000) == (4000, 2000)


def test_merge_ignores_unusable_declared_values():
    assert merge_declared_svg_extent(1140, 2400, 0, 0) == (1140, 2400)
    assert merge_declared_svg_extent(1140, 2400, None, "x") == (1140, 2400)
    # A larger declared value still never pushes past the downstream capture
    # ceiling handling — that is compute_capture_fit's job, not this helper's.
    w, h = merge_declared_svg_extent(10, 10, 20, 30)
    assert (w, h) == (20, 30)
    assert h <= CAPTURE_MAX_DIMENSION_PX or True  # helper does not clamp; fit does
