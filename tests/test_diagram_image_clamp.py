"""Regression tests for clamp_png_dimensions (diagram renderer Issue 42 defect 3).

A rendered diagram whose NATURAL layout is enormous (deep top-to-bottom
inheritance chain, very wide method identifier, huge grid) produces a
full-element screenshot whose width or height exceeds the downstream vision
pipeline's hard 8000px cap. Before the fix the renderer returned those bytes
verbatim and the whole turn aborted with a ValidationException
("image dimensions exceed max allowed size: 8000 pixels").

clamp_png_dimensions downscales any such raster (preserving aspect ratio) so no
dimension exceeds the cap, while returning within-cap images byte-identical.

These tests import the REAL helper from app.services.diagram_renderer (not a
local re-implementation) so they detect drift in the shipped logic. They FAIL
against the pre-fix code because clamp_png_dimensions did not exist there
(ImportError) and the screenshot bytes were returned unclamped.
"""
from __future__ import annotations

import io

import pytest

from app.services.diagram_renderer import (
    IMAGE_MAX_DIMENSION_PX,
    clamp_png_dimensions,
)

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def _png(width: int, height: int) -> bytes:
    """Encode a solid PNG of the given pixel dimensions."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (123, 200, 50)).save(buf, format="PNG")
    return buf.getvalue()


def _dims(png_bytes: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(png_bytes)) as img:
        return img.size


def test_default_cap_is_8000() -> None:
    # The cap must match the downstream vision-pipeline limit exactly.
    assert IMAGE_MAX_DIMENSION_PX == 8000


def test_oversize_height_is_downscaled_to_cap() -> None:
    # A pathologically TALL raster (the deep TB inheritance-chain case).
    src = _png(400, 12000)
    out = clamp_png_dimensions(src)
    w, h = _dims(out)
    assert h == 8000  # largest dim pinned exactly to the cap
    assert w <= 8000
    # Aspect ratio preserved: 400/12000 == w/8000 -> w ~= 266
    assert w == max(1, int(400 * (8000 / 12000)))


def test_oversize_width_is_downscaled_to_cap() -> None:
    # A pathologically WIDE raster (the 300-char method-identifier case).
    src = _png(15000, 300)
    out = clamp_png_dimensions(src)
    w, h = _dims(out)
    assert w == 8000
    assert h <= 8000
    assert h == max(1, int(300 * (8000 / 15000)))


def test_both_dims_oversize_downscaled_and_capped() -> None:
    src = _png(20000, 16000)
    out = clamp_png_dimensions(src)
    w, h = _dims(out)
    assert w <= 8000 and h <= 8000
    # The larger original dim (width) governs the scale; it hits the cap.
    assert max(w, h) == 8000


def test_within_cap_returned_byte_identical() -> None:
    # GUARD: an image already within the cap must NOT be re-encoded/resized —
    # proves the fix is a gap-fill, not a blanket recompression of every render.
    src = _png(1280, 960)
    out = clamp_png_dimensions(src)
    assert out is src or out == src
    assert _dims(out) == (1280, 960)


def test_exactly_at_cap_is_untouched() -> None:
    # Boundary: exactly 8000 in one axis is allowed (cap is inclusive).
    src = _png(8000, 500)
    out = clamp_png_dimensions(src)
    assert out == src
    assert _dims(out) == (8000, 500)


def test_one_over_cap_is_downscaled() -> None:
    # Boundary: 8001 must be brought down to 8000.
    src = _png(8001, 500)
    out = clamp_png_dimensions(src)
    w, h = _dims(out)
    assert w == 8000
    assert h <= 8000


def test_custom_cap_respected() -> None:
    src = _png(4000, 1000)
    out = clamp_png_dimensions(src, max_dim=2000)
    w, h = _dims(out)
    assert w == 2000
    assert h == 500


def test_non_png_bytes_returned_unchanged() -> None:
    # DEFENSIVE: a guard must never destroy the payload it protects.
    junk = b"this is not a PNG at all, just some bytes"
    assert clamp_png_dimensions(junk) is junk


def test_empty_or_tiny_bytes_returned_unchanged() -> None:
    assert clamp_png_dimensions(b"") == b""
    assert clamp_png_dimensions(b"\x89PNG") == b"\x89PNG"


def test_invalid_max_dim_returns_original() -> None:
    src = _png(9000, 200)
    assert clamp_png_dimensions(src, max_dim=0) is src
    assert clamp_png_dimensions(src, max_dim=-100) is src


def test_downscaled_output_is_valid_png() -> None:
    # The emitted bytes must decode as a real PNG (not a corrupted blob).
    out = clamp_png_dimensions(_png(10000, 10000))
    with Image.open(io.BytesIO(out)) as img:
        assert img.format == "PNG"
        assert max(img.size) == 8000


def test_idempotent_on_already_clamped_output() -> None:
    once = clamp_png_dimensions(_png(12000, 400))
    twice = clamp_png_dimensions(once)
    assert _dims(once) == _dims(twice)
    # Second pass is within cap -> byte-identical no-op.
    assert twice == once
