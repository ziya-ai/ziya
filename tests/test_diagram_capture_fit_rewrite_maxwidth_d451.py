"""Regression test for D-451 (wide packet SVG captured as a blank strip),
gfx-sweep group G-09c878.

A packet SVG carries an explicit ``width=<natural px>`` AND ``max-width:100%``.
Inside the bounded, overflow-clipped capture container a grid WIDER than the
container is DISPLAYED shrunk (a bitWidth-512 grid: declared ~6250px shown at
~1280px). ``_CAPTURE_FIT_JS`` unclips the SVG and sizes it to its content
UNION via ``fit_rewrite_svg_px_extent(union, cur_vb, ref)`` — but it took the
px-per-user-unit reference from the SVG's on-screen ``getBoundingClientRect``
width. For the shrunk packet SVG that reference (~1280) is BELOW the viewBox
width (~6250), so the ratio came out < 1 and the just-unclipped SVG was sized
back DOWN to the container width, then the container ``scale`` was layered on
top — captured as the blank / illegible strip the sweep saw (PNG 105-1974 B).

The fix feeds the helper the SVG's DECLARED px width too and uses
``max(client, declared)``: the max-width shrink (declared > client) is ignored
so the SVG is rewritten to its natural width, while an intentional on-screen
UPSCALE (client > declared — point-based graphviz, D-401) is preserved.
``declared_px_w`` defaults to 0, so every historic client-only call is
byte-identical.
"""

from app.services.diagram_renderer import fit_rewrite_svg_px_extent


# packet-w2-03: bitWidth 512 -> viewBox 0 0 6250 250, width attr 6250px, but
# max-width:100% displays it at the ~1280px container width.
VB_W = 6250.0
UNION = (6250.0, 250.0)      # union == viewBox (px-unit engine)
SHOWN_CLIENT_W = 1280.0      # on-screen width after max-width:100% shrink
DECLARED_W = 6250.0          # svg width attribute (natural px)


def test_maxwidth_shrunk_svg_is_rewritten_to_natural_width():
    """With the declared px width supplied, ratio == 1 and the SVG is sized to
    its natural 6250px — the surface compute_capture_fit's scale then brings to
    the 6000px ceiling — NOT crammed into the shrunk container width."""
    px_w, px_h = fit_rewrite_svg_px_extent(
        UNION[0], UNION[1], VB_W, SHOWN_CLIENT_W, DECLARED_W
    )
    assert (round(px_w), round(px_h)) == (6250, 250)


def test_direction_client_only_collapses_to_container_width():
    """DIRECTION: the pre-fix behaviour (client width only, no declared) sized
    the SVG to the shrunk ~1280px — under a fifth of natural — which is the
    blank/illegible-strip mechanism. The fix must NOT return that."""
    pre_fix_w, _ = fit_rewrite_svg_px_extent(UNION[0], UNION[1], VB_W, SHOWN_CLIENT_W)
    assert round(pre_fix_w) == 1280            # the bug: collapsed to container
    post_w, _ = fit_rewrite_svg_px_extent(
        UNION[0], UNION[1], VB_W, SHOWN_CLIENT_W, DECLARED_W
    )
    assert post_w > pre_fix_w
    assert round(post_w) == 6250


def test_graphviz_onscreen_upscale_preserved():
    """A point-based graphviz drawing shown LARGER than its declared px size
    (client > declared) keeps the on-screen scale — max() picks the client
    width, so the D-401 pt->px correction is unchanged."""
    # 300 unit (pt) viewBox rendered on-screen at 1200px (ratio 4.0); declared
    # px width from the pt attribute is ~400px (< client).
    px_w, px_h = fit_rewrite_svg_px_extent(320.0, 60.0, 300.0, 1200.0, 400.0)
    assert (round(px_w), round(px_h)) == (1280, 240)   # 320*4, 60*4 — as D-401


def test_default_declared_is_byte_identical():
    """Omitting declared_px_w reproduces the historic client-only ratio exactly
    (px-unit engine, ratio 1)."""
    assert fit_rewrite_svg_px_extent(800.0, 600.0, 800.0, 800.0) == (800.0, 600.0)
    assert fit_rewrite_svg_px_extent(800.0, 600.0, 800.0, 800.0, 0.0) == (800.0, 600.0)


def test_declared_only_when_no_client_box():
    """When the SVG has no measurable on-screen box (client 0) but a declared
    width, the declared width still drives the ratio."""
    px_w, _ = fit_rewrite_svg_px_extent(6250.0, 250.0, 6250.0, 0.0, 6250.0)
    assert round(px_w) == 6250
