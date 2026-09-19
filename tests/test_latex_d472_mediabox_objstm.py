r"""D-472 / G-1722c9 -- explicit width/height must be honoured when pdfTeX
compresses the page dict into an object stream (tikz-cd-w2-08/09).

Backstory
---------
TeX Live 2026 pdfTeX writes the page object -- and its ``/MediaBox`` -- inside a
compressed cross-reference object stream (``/ObjStm``).  ``LatexRenderer._pdf_
media_box_points`` scans the raw PDF bytes for a plaintext ``/MediaBox`` array,
finds none, and returns ``None``; ``_raster_dpi`` then falls back to the fixed
natural-size DPI and SILENTLY IGNORES an explicit width/height request (a
4000x3000 ask rasterises at the natural ~150 dpi).

Fix (``LatexProfile.build_document``): on the PDF (raster) path emit
``\pdfobjcompresslevel=0`` so the page dict stays an uncompressed top-level
object and ``/MediaBox`` is readable again.  The SVG path (DVI driver, never
reads a MediaBox) must NOT get it.

Structural / size fix, theme-independent.  These tests FAIL on the pre-fix
profile: without ``\pdfobjcompresslevel=0`` the MediaBox is unreadable and the
sized render collapses to natural size.
"""
import struct

import pytest

from app.services.latex_profiles import get_profile
from app.services.latex_renderer import LatexRenderer

_BODY = (r"P \arrow[r] \arrow[d] & Q \arrow[r] \arrow[d] & R \arrow[d] \\"
         r"S \arrow[r] & T \arrow[r] & U")


def _doc(fmt: str) -> str:
    profile = get_profile("tikz-cd")
    assert profile is not None
    return profile.build_document(_BODY, standalone=True, fmt=fmt, theme="light")


def test_objcompress_disabled_on_pdf_path_only():
    pdf_doc = _doc("png")
    svg_doc = _doc("svg")
    assert r"\pdfobjcompresslevel=0" in pdf_doc, "PDF path must disable object streams"
    assert r"\pdfobjcompresslevel=0" not in svg_doc, "SVG path must not emit it"


def _png_size(b: bytes) -> tuple[int, int]:
    assert b[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", b[16:24])


@pytest.mark.timeout(60)
def test_large_size_request_scales_up_from_readable_mediabox():
    r"""A 4000x3000 request on a small diagram scales the raster well above the
    natural ~224x175.  Pre-fix the MediaBox is unreadable, so the render stays
    at natural size and this assertion fails."""
    renderer = LatexRenderer()
    cap = renderer.probe()
    if not cap.available:
        pytest.skip("LaTeX toolchain unavailable")

    natural = renderer.render("tikz-cd", _BODY, fmt="png", theme="light")
    assert natural.ok, natural.error[:120]
    nat_w, nat_h = _png_size(natural.content)

    big = renderer.render("tikz-cd", _BODY, fmt="png", theme="light",
                          width=4000, height=3000)
    assert big.ok, big.error[:120]
    big_w, big_h = _png_size(big.content)
    # The sized render must be substantially larger than natural in both axes.
    assert big_w > nat_w * 2 and big_h > nat_h * 2, (
        f"size request ignored: natural={nat_w}x{nat_h} sized={big_w}x{big_h}")


@pytest.mark.timeout(60)
def test_mediabox_is_readable_after_fix():
    """The MediaBox parser returns a positive natural size for a compiled
    tikz-cd PDF (it returned None while the box was hidden in an ObjStm)."""
    import subprocess, tempfile, os
    from pathlib import Path
    from app.services import latex_renderer as R

    renderer = LatexRenderer()
    cap = renderer.probe()
    if not cap.available:
        pytest.skip("LaTeX toolchain unavailable")
    doc = _doc("png")
    d = tempfile.mkdtemp()
    (Path(d) / "t.tex").write_text(doc, encoding="utf-8")
    env = R._augment_texinputs(dict(os.environ))
    subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "t.tex"],
                   cwd=d, capture_output=True, text=True, timeout=60, env=env)
    pdf = Path(d) / "t.pdf"
    if not pdf.exists():
        pytest.skip("compile produced no PDF in this environment")
    size = LatexRenderer._pdf_media_box_points(pdf)
    assert size is not None and size[0] > 0 and size[1] > 0
