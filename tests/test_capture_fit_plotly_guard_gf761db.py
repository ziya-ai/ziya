"""Regression tests for gfx-sweep group G-f761db (plotly capture-fit clip).

D-301 (plotly-w2-10 / -w2-11, extreme author aspect ratios) and D-304
(plotly-w2-02, a 40-entry legend-grown figure) both survived their earlier
targeted fixes — render-div min-height honouring layout.height (D-301) and
title.automargin (D-304) — and still reconciled *still-broken*.

The residual cause is in the SHARED capture-fit step: whenever
``compute_capture_fit`` reports ``needs_fit`` (which it does for extreme-aspect
and legend-grown figures whose natural surface exceeds the bounded capture
window), ``_CAPTURE_FIT_JS`` rewrites ``c.querySelector('svg')``'s
viewBox/width/height to the getBBox union. That is the correct unclip for a
SINGLE-root-SVG engine (graphviz / mermaid / vega), but a Plotly figure is a
stack of several absolutely-positioned ``.main-svg`` layers plus WebGL
canvases; resizing only the first layer desynchronises the stack and clips the
title / axis — re-breaking the layout the D-301/D-304 fixes had corrected.

``capture_should_rewrite_svg`` closes the gap: for a Plotly figure (or any
container with more than one ``<svg>``) it returns ``False`` so the fit step
SKIPS the per-layer geometry rewrite and relies only on the layer-agnostic
container-level unclip + scale transform, which reveals the clipped figure
without distorting it. Single-SVG engines keep the historic rewrite.

These tests import the REAL helper and assert the DIRECTION that was previously
wrong. Structural / theme-independent (capture geometry precedes painting), so
both-theme render verification is deferred to the build+render stage.
"""
from __future__ import annotations

from app.services.diagram_renderer import (
    capture_should_rewrite_svg,
    _CAPTURE_FIT_JS,
    _CAPTURE_MEASURE_JS,
)


def test_plotly_measure_reports_svg_stack():
    # The measure step must surface the two signals the guard depends on.
    assert "isPlotly" in _CAPTURE_MEASURE_JS
    assert "svgCount" in _CAPTURE_MEASURE_JS
    assert ".js-plotly-plot" in _CAPTURE_MEASURE_JS


def test_guard_skips_rewrite_for_plotly_figure():
    # D-301/D-304: a Plotly figure must NOT have its stacked SVG layers resized.
    measure = {
        "naturalWidth": 4000, "naturalHeight": 260,
        "renderedWidth": 1280, "renderedHeight": 720,
        "svgCount": 6, "isPlotly": True,
    }
    assert capture_should_rewrite_svg(measure) is False


def test_guard_skips_rewrite_for_any_multi_svg_container():
    # Defensive: more than one <svg> means a stacked/composite surface even if
    # the plotly class marker is absent — still unsafe to rewrite just the first.
    measure = {"svgCount": 3, "isPlotly": False}
    assert capture_should_rewrite_svg(measure) is False


def test_guard_allows_rewrite_for_single_svg_engine():
    # graphviz / mermaid / vega: exactly one root SVG, historic rewrite kept.
    measure = {
        "naturalWidth": 3200, "naturalHeight": 3200,
        "renderedWidth": 1280, "renderedHeight": 1280,
        "svgCount": 1, "isPlotly": False,
    }
    assert capture_should_rewrite_svg(measure) is True


def test_guard_defaults_to_rewrite_when_signals_missing():
    # Backward-compatible: an old measurement without the new fields keeps the
    # historic single-SVG rewrite path (never silently skips a graphviz unclip).
    assert capture_should_rewrite_svg({}) is True
    assert capture_should_rewrite_svg(None) is True
    assert capture_should_rewrite_svg({"svgCount": "bad"}) is True


def test_fit_js_honours_the_rewrite_flag():
    # The fit step must actually gate the SVG geometry rewrite on the flag, and
    # still accept a bare-scale arg for backward compatibility.
    assert "rewriteSvg" in _CAPTURE_FIT_JS
    assert "svg && rewriteSvg" in _CAPTURE_FIT_JS
