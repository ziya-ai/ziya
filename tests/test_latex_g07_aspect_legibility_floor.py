"""G-07 / D-007: wide/tall-aspect glyph-legibility floor for the LaTeX raster.

Background (already fixed under D-006/G-66): ``render_diagram`` width/height
reach ``latex_renderer._raster_dpi``, which fits the tight ``standalone`` crop
INSIDE the requested pixel box.  Residual gap (this defect,
``aspect-ratio-collapses-tick-labels`` / tikz-w2-07): for an extreme-aspect
drawing -- a ~22:1 wide axis of rotated ``\\tiny`` year labels -- fitting the
tight (width) axis alone drops the DPI so low that glyphs rasterise to 4-5px
and no label is readable, even though the roomier (height) axis has resolution
to spare.  The old code let that fit-inside DPI fall all the way to the 12 DPI
non-zero floor (``MIN_PNG_DPI``), which is a "don't emit a 0px image" guard,
NOT a legibility guard.

The fix adds ``MIN_LEGIBLE_DPI``: when BOTH dimensions are supplied (a bounding
box, not an exact size) the fit is lifted to that floor -- capped at what the
roomier axis itself allows so a uniformly-small box is NOT inflated -- and the
constrained axis is allowed to overflow the box.  A single-dimension request is
left untouched (the D-006 honoured-downscale contract), as is the no-request
path.

DIRECTION: this whole module imports ``MIN_LEGIBLE_DPI``, which does not exist
on the unpatched tree, so every test here fails with ImportError against
pre-fix code.  Beyond that, ``test_wide_aspect_box_lifted_to_legibility_floor``
asserts the DPI is the floor (96) where the unpatched fit-inside value was 43.2
-- a behavioural difference, not just a symbol check.  The regression guards
(single-dimension, no-request, in-box, small-box) pin that the floor does NOT
leak into the cases D-006 deliberately honours.

Structural (geometry-only) defect: DPI selection is theme-independent, so there
is no per-theme assertion to make -- the same DPI is used for the dark and
light raster surfaces alike.
"""

from app.services.latex_renderer import (
    LatexRenderer,
    MIN_LEGIBLE_DPI,
    MIN_PNG_DPI,
    MAX_PNG_DPI,
    PNG_DPI,
)


def _write_pdf(tmp_path, w_pt, h_pt):
    """A stub file carrying just the /MediaBox the parser reads."""
    p = tmp_path / "doc.pdf"
    p.write_bytes(
        b"%PDF-1.5\n"
        + f"/MediaBox [0 0 {w_pt} {h_pt}]\n".encode("ascii")
        + b"%%EOF\n"
    )
    return p


class TestLegibilityFloorConstant:
    def test_floor_sits_between_nonzero_floor_and_default(self):
        # A legibility floor is meaningfully above the 12 DPI "non-zero" guard
        # and comfortably below the 150 default, so an ordinary in-box render
        # is never inflated by it.
        assert MIN_PNG_DPI < MIN_LEGIBLE_DPI < PNG_DPI


class TestWideAspectCrushRescued:
    def test_wide_aspect_box_lifted_to_legibility_floor(self, tmp_path):
        # ~22:1 wide crop (tikz-w2-07 shape) fit into a square-ish box.
        #   width axis:  1200*72/2000 = 43.2  (the tight, crushing axis)
        #   height axis: 1200*72/90   = 960.0 (abundant room)
        # Pre-fix: fit-inside picks 43.2 -> \tiny glyphs at ~3px, unreadable.
        # Post-fix: lifted to MIN_LEGIBLE_DPI (96); height overflows the box,
        # which the viewer can scroll -- legible beats in-box-illegible.
        pdf = _write_pdf(tmp_path, 2000, 90)
        dpi = LatexRenderer._raster_dpi(pdf, 1200, 1200)
        assert dpi == float(MIN_LEGIBLE_DPI)
        # The unpatched fit-inside value the bug produced:
        unpatched_fit = min(1200 * 72.0 / 2000, 1200 * 72.0 / 90)
        assert unpatched_fit < MIN_LEGIBLE_DPI  # confirms this WAS a crush
        assert dpi > unpatched_fit              # and that the fix lifted it

    def test_tall_aspect_box_lifted_to_legibility_floor(self, tmp_path):
        # The mirror case: a very tall crop constrained on the height axis.
        #   width axis:  1200*72/90   = 960.0
        #   height axis: 1200*72/2000 = 43.2  (tight, crushing)
        pdf = _write_pdf(tmp_path, 90, 2000)
        dpi = LatexRenderer._raster_dpi(pdf, 1200, 1200)
        assert dpi == float(MIN_LEGIBLE_DPI)


class TestFloorDoesNotInflateHonouredCases:
    def test_in_box_render_above_floor_is_unchanged(self, tmp_path):
        # 200x100 crop into 800x800: fit-inside is 288, already well above the
        # floor, so the floor is a no-op (byte-identical to D-006 behaviour).
        pdf = _write_pdf(tmp_path, 200, 100)
        assert LatexRenderer._raster_dpi(pdf, 800, 800) == 288.0

    def test_uniformly_small_box_not_inflated(self, tmp_path):
        # A deliberate small thumbnail: BOTH axes land at 43.2, so the roomier
        # axis is also 43.2 -> the floor collapses to 43.2 and does NOT lift
        # the request to 96.  A small box stays small.
        pdf = _write_pdf(tmp_path, 200, 160)
        dpi = LatexRenderer._raster_dpi(pdf, 120, 96)
        assert abs(dpi - 43.2) < 1e-6
        assert dpi < MIN_LEGIBLE_DPI

    def test_single_dimension_width_only_not_lifted(self, tmp_path):
        # D-006 contract: a single-dimension request is honoured literally.
        # 100*72/200 = 36 must stay 36 -- the legibility floor must NOT leak
        # into the single-axis case (that would defeat an explicit downscale).
        pdf = _write_pdf(tmp_path, 200, 100)
        dpi = LatexRenderer._raster_dpi(pdf, 100, None)
        assert dpi == 36.0
        assert dpi < MIN_LEGIBLE_DPI

    def test_single_dimension_width_only_exact(self, tmp_path):
        pdf = _write_pdf(tmp_path, 400, 100)
        assert LatexRenderer._raster_dpi(pdf, 400, None) == 72.0

    def test_no_size_request_keeps_fixed_png_dpi(self, tmp_path):
        pdf = _write_pdf(tmp_path, 200, 100)
        assert LatexRenderer._raster_dpi(pdf, None, None) == float(PNG_DPI)

    def test_extreme_downscale_single_dim_still_clamps_to_min(self, tmp_path):
        # A single-dim extreme downscale still clamps to MIN_PNG_DPI (not the
        # legibility floor): 10*72/200 = 3.6 -> 12.
        pdf = _write_pdf(tmp_path, 200, 100)
        assert LatexRenderer._raster_dpi(pdf, 10, None) == float(MIN_PNG_DPI)

    def test_ceiling_still_applies_with_both_dims(self, tmp_path):
        pdf = _write_pdf(tmp_path, 200, 100)
        assert LatexRenderer._raster_dpi(pdf, 100000, 100000) == float(MAX_PNG_DPI)
