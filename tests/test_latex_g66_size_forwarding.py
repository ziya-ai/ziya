"""G-66 / D-006: render_diagram width/height must reach the LaTeX raster.

``render_diagram`` accepts ``width``/``height`` as the only escape hatch for
dense or extreme-aspect diagrams, but the LaTeX dispatch
(``DiagramRenderTool._render_latex_direct``) forwarded only
type/definition/fmt/theme, and ``latex_renderer.render`` had no size
parameters at all -- so ``standalone`` cropped to the natural bounding box and
every diagram rasterised at a fixed ``PNG_DPI`` (150) no matter what the caller
asked for.  The request was silently dropped.

The fix threads width/height from the tool through ``render`` into ``_compile``
and scales the pdf->png resolution so the tight crop is fit INSIDE the
requested pixel box (aspect ratio preserved, clamped to a sane DPI band).

These tests exercise the pure sizing/dispatch logic (no TeX install required);
the end-to-end re-render is verified at the shared build+render stage.

DIRECTION: every assertion below distinguishes the FIX from the bug --
``_raster_dpi``/``_pdf_media_box_points`` did not exist before the fix, the
size-keyed cache key was absent, and the dispatch dropped the arguments.  A
test written against the unpatched code fails (AttributeError / dropped args).
"""

import asyncio
import inspect

from app.services.latex_renderer import (
    LatexRenderer,
    MIN_PNG_DPI,
    MAX_PNG_DPI,
    PNG_DPI,
    latex_renderer,
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


class TestMediaBoxParse:
    def test_parses_integer_mediabox(self, tmp_path):
        assert LatexRenderer._pdf_media_box_points(
            _write_pdf(tmp_path, 200, 100)
        ) == (200.0, 100.0)

    def test_parses_real_mediabox(self, tmp_path):
        p = tmp_path / "doc.pdf"
        p.write_bytes(b"/MediaBox[0 0 123.4 56.7]")
        assert LatexRenderer._pdf_media_box_points(p) == (123.4, 56.7)

    def test_missing_mediabox_returns_none(self, tmp_path):
        p = tmp_path / "doc.pdf"
        p.write_bytes(b"%PDF-1.5\nno media box here\n%%EOF")
        assert LatexRenderer._pdf_media_box_points(p) is None


class TestRasterDpi:
    # ---- direction: no request => the OLD fixed behaviour, unchanged ----
    def test_no_size_request_keeps_fixed_png_dpi(self, tmp_path):
        pdf = _write_pdf(tmp_path, 200, 100)
        assert LatexRenderer._raster_dpi(pdf, None, None) == float(PNG_DPI)

    # ---- the fix: a large box scales the resolution UP (was fixed 150) ----
    def test_large_box_scales_up_and_fits_inside(self, tmp_path):
        pdf = _write_pdf(tmp_path, 200, 100)  # natural pt 200x100
        dpi = LatexRenderer._raster_dpi(pdf, 800, 800)
        # fit-inside picks the smaller ratio: 800*72/200=288 vs 800*72/100=576
        assert dpi == 288.0
        assert dpi > PNG_DPI  # genuinely larger than the old fixed value
        # neither dimension overshoots the requested box
        assert 200 * dpi / 72 <= 800 + 1e-6
        assert 100 * dpi / 72 <= 800 + 1e-6

    # ---- the fix: a tiny box scales DOWN (the documented escape hatch) ----
    def test_small_box_scales_down(self, tmp_path):
        pdf = _write_pdf(tmp_path, 200, 100)
        dpi = LatexRenderer._raster_dpi(pdf, 100, None)  # 100*72/200 = 36
        assert dpi == 36.0
        assert dpi < PNG_DPI

    def test_single_dimension_width_only(self, tmp_path):
        pdf = _write_pdf(tmp_path, 400, 100)
        assert LatexRenderer._raster_dpi(pdf, 400, None) == 72.0

    def test_dpi_floor_clamps_extreme_downscale(self, tmp_path):
        pdf = _write_pdf(tmp_path, 200, 100)
        # 10*72/200 = 3.6 -> clamped to the floor, never a sub-pixel/0px raster
        assert LatexRenderer._raster_dpi(pdf, 10, None) == float(MIN_PNG_DPI)

    def test_dpi_ceiling_clamps_extreme_upscale(self, tmp_path):
        pdf = _write_pdf(tmp_path, 200, 100)
        # 100000*72/200 = 36000 -> clamped to the ceiling (budget/size guard)
        assert LatexRenderer._raster_dpi(pdf, 100000, None) == float(MAX_PNG_DPI)

    def test_unparseable_pdf_degrades_to_fixed_dpi(self, tmp_path):
        p = tmp_path / "doc.pdf"
        p.write_bytes(b"not a pdf")
        # a size WAS requested, but with no MediaBox we degrade rather than raise
        assert LatexRenderer._raster_dpi(p, 800, 600) == float(PNG_DPI)


class TestCacheKeySize:
    def test_key_unchanged_without_size(self):
        base = LatexRenderer._cache_key("doc", "png")
        assert base == LatexRenderer._cache_key("doc", "png", None, None)

    def test_key_differs_by_requested_size(self):
        # Direction: without the fix _cache_key ignored size, so two different
        # requests collided on one cached PNG.  Now they are distinct.
        base = LatexRenderer._cache_key("doc", "png")
        k1 = LatexRenderer._cache_key("doc", "png", 800, 600)
        k2 = LatexRenderer._cache_key("doc", "png", 400, 300)
        assert k1 != base
        assert k1 != k2


class TestSignaturesThreadSize:
    def test_render_accepts_width_height(self):
        params = inspect.signature(LatexRenderer.render).parameters
        assert "width" in params and "height" in params

    def test_compile_accepts_width_height(self):
        params = inspect.signature(LatexRenderer._compile).parameters
        assert "width" in params and "height" in params


class TestDispatchForwardsSize:
    """The tool must forward width/height to the renderer (was dropped)."""

    def test_render_latex_direct_forwards_size(self, monkeypatch):
        from app.mcp.tools.diagram_render import RenderDiagramTool

        captured = {}

        def fake_render(diagram_type, body, fmt="auto", use_cache=True,
                        theme="light", width=None, height=None):
            captured["width"] = width
            captured["height"] = height
            # Short-circuit: a non-install/non-reject failure returns quickly
            # without needing real image bytes.
            from app.services.latex_renderer import RenderResult
            return RenderResult(ok=False, error_kind="compile",
                                error="stub", log_excerpt="")

        monkeypatch.setattr(latex_renderer, "render", fake_render)

        tool = RenderDiagramTool()
        asyncio.run(tool._render_latex_direct(
            "tikz", "\\draw (0,0)--(1,1);", "png", "light", 1280, 240,
        ))

        assert captured == {"width": 1280, "height": 240}
