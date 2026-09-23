/**
 * D-201 wide-grid-downscaled-text-illegible — grid-WIDTH legibility bound.
 *
 * The prior remedy (rulerTickStride, packetRulerDecimationG201.test.ts) stops
 * the 512 ruler numbers from OVERLAPPING, but it cannot make them legible: the
 * packet grid was sized up to PACKET_MAX_GRID_PX = 6000 (exactly the capture
 * ceiling), so a bitWidth-512 spec produced a ~6200px surface. The judging /
 * vision stage downscales that long edge to ~1568px (≈0.25x), and a uniform
 * downscale shrinks EVERY font — title, section labels, field names, ruler
 * numbers — by the same factor, so an 11px label lands at ~2.8px: sub-pixel
 * smear. Decimation is powerless against absolute font size.
 *
 * The durable fix bounds the natural grid to a LEGIBLE width so the captured
 * surface survives the vision downscale near 1:1. These cases assert DIRECTION:
 * each one FAILS at the old 6000 ceiling and passes at the legible ceiling, and
 * the no-op case proves ordinary packets are byte-identical.
 *
 * Pure geometry (computeDimensions / the exported ceiling); no DOM, no d3.
 */
import {
  computeDimensions,
  computeGridMetrics,
  PACKET_MAX_GRID_PX,
} from '../../../utils/d3Plugins/packetPlugin';

// packet-w2-03: bitWidth 512 (PACKET_MAX_BIT_WIDTH); rows sum to 512 bits.
const wideSpec: any = {
  type: 'packet',
  title: 'bitWidth 512 (cap)',
  bitWidth: 512,
  sections: [
    {
      label: 'Wide',
      color: 'metadata',
      rows: [
        { fields: [['q0', 128], ['q1', 128], ['q2', 128], ['q3', 128]] },
        { fields: [['h0', 256], ['h1', 256]] },
      ],
    },
  ],
};

// An ordinary 32-bit packet — must be untouched by the ceiling change.
const ordinarySpec: any = {
  type: 'packet',
  title: 'ordinary',
  bitWidth: 32,
  sections: [{ label: 'Hdr', color: 'metadata', rows: [{ fields: [['a', 16], ['b', 16]] }] }],
};

// The long edge the vision/judge stage resizes a captured raster's longest
// dimension to before reading it. The whole defect is that a ~6200px surface
// collapses to this, shrinking text ~4x.
const VISION_LONG_EDGE = 1568;
// Smallest on-screen font (px, post-downscale) that still reads as a numeral.
const LEGIBLE_FONT_FLOOR = 6;
// The field-label font the plugin authors (bold Segoe UI 11px baseline).
const FIELD_FONT_PX = 11;

describe('packet grid-width legibility bound (D-201)', () => {
  it('caps the grid ceiling at a legible width, not the capture ceiling (6000)', () => {
    // Direction: 6000 is exactly CAPTURE_MAX_DIMENSION_PX and is the value that
    // let the 512-bit surface reach ~6200px. A legible ceiling is far smaller.
    expect(PACKET_MAX_GRID_PX).toBeLessThanOrEqual(1600);
    expect(PACKET_MAX_GRID_PX).toBeGreaterThan(0);
    // GRID_W of the extreme spec is held to the ceiling (never 512*16 = 8192).
    const { GRID_W } = computeGridMetrics(wideSpec);
    expect(GRID_W).toBeLessThanOrEqual(PACKET_MAX_GRID_PX);
  });

  it('keeps the bitWidth-512 surface narrow enough to survive vision downscale', () => {
    const { width } = computeDimensions(wideSpec);
    // At the old 6000 ceiling this was ~6230px; the legible ceiling holds it
    // well under 2000px. This bound is crossed by the fix.
    expect(width).toBeLessThan(2000);

    // The real acceptance criterion: after the vision stage resizes the long
    // edge to VISION_LONG_EDGE, the field font must remain legible. At width
    // ~6230 the scale is ~0.25 → 11px → ~2.8px (fails); at ~1800 the scale is
    // ~0.87 → ~9.6px (passes).
    const visionScale = Math.min(1, VISION_LONG_EDGE / width);
    const effectiveFontPx = FIELD_FONT_PX * visionScale;
    expect(effectiveFontPx).toBeGreaterThanOrEqual(LEGIBLE_FONT_FLOOR);
  });

  it('is a strict no-op for an ordinary 32-bit packet (byte-identical width)', () => {
    // 32-bit BIT_W is 24 → GRID_W 768, far below any legible ceiling, so the
    // ceiling change cannot move it: GRID_W is what the ceiling clamps, and it
    // is untouched, so the whole surface (grid + label column + gutters) is
    // byte-identical to before the fix.
    const { GRID_W } = computeGridMetrics(ordinarySpec);
    expect(GRID_W).toBe(32 * 24); // 768 — well under PACKET_MAX_GRID_PX
    const { width } = computeDimensions(ordinarySpec);
    expect(width).toBeLessThan(PACKET_MAX_GRID_PX); // ordinary packet never approaches the ceiling
  });
});
