"""Image comparison for the golden tier — the tolerance lives here.

Three mechanisms that are easy to conflate:

  pixel hash        exact match, no decode of the golden.  Zero tolerance.
                    The fast lane: if the render is byte-for-byte the pixels
                    a judge approved, nothing else needs to run.
  diff fraction     the share of pixels whose colour moved by more than
                    CHANNEL_EPS in some channel.  THIS is where "acceptable
                    error" is defined: sub-pixel anti-aliasing and font
                    hinting move a few hundred pixels a little; a dropped
                    node, a clipped label or a black-on-black theme
                    regression moves many pixels a lot.
  downscaling       NOT used for the comparison.  Box-averaging smears 1-px
                    jitter below CHANNEL_EPS, which sounds helpful, but it
                    smears a missing hairline edge just the same.  Compare
                    at capture resolution; the threshold does the tolerating.

The hash is imported from scripts/gfx_ledger.py so the runner and the ledger
can never disagree about what "the same image" means.
"""

from __future__ import annotations

import io
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import gfx_ledger as ledger  # noqa: E402

pixel_hash = ledger.pixel_hash

# A channel must move by more than this (of 255) for the pixel to count as
# different.  Anti-aliasing coverage changes of one or two shades stay under
# it; a text glyph appearing or vanishing does not.
CHANNEL_EPS = 32

# Default share of differing pixels below which two renders are the same
# diagram.  0.5% of a 1280x960 capture is ~6,100 px: several glyph edges
# re-hinting, comfortably; one 80x80 node vanishing (6,400 px) is not.
DEFAULT_TOLERANCE = 0.005


@dataclass
class Comparison:
    same_size: bool
    fraction: float          # 1.0 when sizes differ
    differing: int
    total: int
    size_a: Tuple[int, int]
    size_b: Tuple[int, int]
    mask_png: Optional[bytes]  # white where pixels differ; None on size mismatch

    def within(self, tolerance: float) -> bool:
        return self.same_size and self.fraction < tolerance


def compare(png_a: bytes, png_b: bytes) -> Comparison:
    """Per-pixel comparison at native resolution.

    A size mismatch is reported as total difference rather than resized
    away: the capture extent IS part of what the golden asserts (the
    viewport-crop and content-lost defect families change it).
    """
    from PIL import Image, ImageChops
    a = Image.open(io.BytesIO(png_a)).convert("RGB")
    b = Image.open(io.BytesIO(png_b)).convert("RGB")
    if a.size != b.size:
        return Comparison(False, 1.0, a.width * a.height, a.width * a.height,
                          a.size, b.size, None)
    diff = ImageChops.difference(a, b)
    r, g, bl = diff.split()
    worst = ImageChops.lighter(r, ImageChops.lighter(g, bl))
    mask = worst.point(lambda v: 255 if v > CHANNEL_EPS else 0)
    hist = mask.histogram()
    differing = hist[255]
    total = a.width * a.height
    buf = io.BytesIO()
    mask.save(buf, format="PNG")
    return Comparison(True, differing / total if total else 0.0, differing, total,
                      a.size, b.size, buf.getvalue())


def heatmap(png_golden: bytes, mask_png: bytes) -> bytes:
    """The golden, dimmed, with differing pixels painted red — for the judge."""
    from PIL import Image
    base = Image.open(io.BytesIO(png_golden)).convert("RGB")
    mask = Image.open(io.BytesIO(mask_png)).convert("L")
    dim = Image.blend(base, Image.new("RGB", base.size, (128, 128, 128)), 0.5)
    red = Image.new("RGB", base.size, (255, 0, 0))
    out = Image.composite(red, dim, mask)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()
