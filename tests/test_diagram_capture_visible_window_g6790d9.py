"""G-6790d9 (D-256 / D-311): tall / composite Vega-Lite charts captured clipped.

Root cause (differs from the prior G-5b9ba1 / G-d4a898 attempts, which chased an
"under-reported natural height"): a tall Vega-Lite chart is measured CORRECTLY —
Vega emits a viewBox and a declared px height, so ``naturalHeight`` is the full
~2442px. The defect was in the *rendered* (shown) size. The vega sizingConfig
gives ``#diagram-render-container`` ``height:auto``, so its own layout box grows
to the full 2442; ``compute_capture_fit`` then saw ``rendered == natural`` and
reported ``clipped == False``, so the unclip never fired — even though the
harness root (``#diagram-render-root``: ``height:100vh``, ``overflow:hidden``,
flex ``align-items:center``) crops the container to a ~960px centred paint window
that is all a Playwright element screenshot can capture.

``intersect_visible_extent`` computes the ACTUALLY VISIBLE window as the
intersection of the container box with its clipping ancestors + viewport, which
is what ``_CAPTURE_MEASURE_JS`` now reports as ``renderedWidth/Height``. These
tests fail before the fix (the helper does not exist) and pin the geometry that
makes ``needs_fit`` fire for the clipped tall chart while leaving fitting
diagrams byte-identical.
"""

from app.services.diagram_renderer import (
    compute_capture_fit,
    intersect_visible_extent,
)


# ── the geometry helper ─────────────────────────────────────────────────────

def test_tall_container_cropped_to_root_viewport_window():
    """A 754x2442 container centred in a 1280x960 overflow:hidden root is only
    visible for ~960px vertically (the width fits, so it is unchanged)."""
    # Centred: left = (1280-754)/2 = 263, top = (960-2442)/2 = -741.
    vis_w, vis_h = intersect_visible_extent(
        263.0, -741.0, 754.0, 2442.0,
        clips=[(0.0, 0.0, 1280.0, 960.0)],
    )
    assert vis_w == 754
    assert vis_h == 960  # cropped from 2442 -> the visible band


def test_fitting_container_visible_window_equals_box():
    """When nothing crops the container (it sits fully inside the root), the
    visible window equals the container box — the monotonic no-op case that
    keeps a fitting diagram's capture byte-identical."""
    # 900x700 sits fully inside the 1280x960 root at (190, 130).
    vis_w, vis_h = intersect_visible_extent(
        190.0, 130.0, 900.0, 700.0,
        clips=[(0.0, 0.0, 1280.0, 960.0)],
    )
    assert (vis_w, vis_h) == (900, 700)


def test_no_clips_returns_container_box():
    assert intersect_visible_extent(0, 0, 512, 384, clips=None) == (512, 384)
    assert intersect_visible_extent(0, 0, 512, 384, clips=[]) == (512, 384)


def test_degenerate_container_is_noop():
    assert intersect_visible_extent(0, 0, 0, 500, clips=[(0, 0, 100, 100)]) == (0, 0)


# ── the fix ties the visible window to the fit decision ─────────────────────

def test_tall_vega_triggers_fit_only_with_visible_window():
    """The whole point: feeding the container's own (grown) box makes
    compute_capture_fit see no clip; feeding the true visible window makes it
    fire ``needs_fit`` so the existing unclip runs and the full chart is
    captured."""
    natural_w, natural_h = 754, 2442

    # BEFORE the fix: renderedHeight was the container's own layout box, which
    # for a height:auto vega container equals the natural height -> no clip.
    needs_fit_old, _s, _tw, _th = compute_capture_fit(
        natural_w, natural_h, natural_w, natural_h,
    )
    assert needs_fit_old is False  # the latent bug

    # AFTER the fix: renderedHeight is the visible (ancestor-clipped) window.
    _vw, vis_h = intersect_visible_extent(
        263.0, -741.0, float(natural_w), float(natural_h),
        clips=[(0.0, 0.0, 1280.0, 960.0)],
    )
    needs_fit_new, scale, _tw2, _th2 = compute_capture_fit(
        natural_w, natural_h, natural_w, vis_h,
    )
    assert needs_fit_new is True
    # Under the 6000px ceiling, so it unclips and captures at natural size.
    assert scale == 1.0


def test_fitting_vega_still_no_fit():
    """A chart that fits the viewport is unaffected: the visible window equals
    its box, so needs_fit stays False and the capture path is unchanged."""
    vis_w, vis_h = intersect_visible_extent(
        190.0, 130.0, 900.0, 700.0, clips=[(0.0, 0.0, 1280.0, 960.0)],
    )
    needs_fit, scale, _tw, _th = compute_capture_fit(900, 700, vis_w, vis_h)
    assert needs_fit is False
    assert scale == 1.0
