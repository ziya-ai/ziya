/**
 * D-201 wide-grid-downscaled-text-illegible — ruler tick decimation.
 *
 * packet-w2-03 uses bitWidth 512 (PACKET_MAX_BIT_WIDTH). The natural grid is
 * ~8k px; the server capture-fit downscales it under the 6000px ceiling, at
 * which a per-bit ruler rendered all 512 three-digit numbers into sub-pixel
 * smears. `rulerTickStride` picks a power-of-two stride so a label always fits
 * its (strided) cell and never collides with its neighbour. A uniform downscale
 * shrinks cell and font together, so a fit decided at natural scale holds at any
 * capture scale.
 *
 * Each case asserts DIRECTION: it first shows the pre-fix per-bit ruler cannot
 * fit the label in one cell, THEN that the stride opens enough room — so a test
 * that would pass against the old always-stride-1 ruler cannot masquerade as a
 * fix. Pure helper (no DOM / no d3), matching the other packet unit tests.
 */
import { rulerTickStride } from '../packetPlugin';
import { defaultLayout } from '../../../utils/d3Plugins/packetPlugin';

// Same monospace advance the helper uses, mirrored here to prove fit.
const TICK_CHAR_W = 7.2;
const labelWidthPx = (bits: number) =>
  String(Math.max(0, bits - 1)).length * TICK_CHAR_W;

describe('rulerTickStride — legible ruler at any bit width (D-201)', () => {
  it('is a no-op (stride 1) for ordinary bit widths — ruler byte-identical', () => {
    // BIT_W: 8b->56, 16b->36, 32b->24. A 2-digit label (~14.4px) fits every one.
    expect(rulerTickStride(8, defaultLayout(8).BIT_W)).toBe(1);
    expect(rulerTickStride(16, defaultLayout(16).BIT_W)).toBe(1);
    expect(rulerTickStride(32, defaultLayout(32).BIT_W)).toBe(1);
  });

  it('decimates the 512-bit ruler so 3-digit numbers stop overlapping (w2-03)', () => {
    const bitW = defaultLayout(512).BIT_W; // 16px for bits > 32
    const labelW = labelWidthPx(512);      // 3 digits ≈ 21.6px

    // Control: pre-fix, every bit is labelled, but a 21.6px label does NOT fit
    // a 16px cell — adjacent ruler numbers collided into an illegible smear.
    expect(labelW).toBeGreaterThan(bitW);

    const stride = rulerTickStride(512, bitW);
    // A power-of-two stride > 1 is chosen …
    expect(stride).toBeGreaterThan(1);
    expect(Number.isInteger(Math.log2(stride))).toBe(true);
    // … and it opens enough room: label now fits its strided cell block, so
    // drawn numbers never overlap (the property that survives uniform downscale).
    expect(stride * bitW).toBeGreaterThanOrEqual(labelW);
  });

  it('scales the stride with digit count / cell narrowness, always fitting', () => {
    for (const bits of [64, 128, 256, 512]) {
      const bitW = defaultLayout(bits).BIT_W;
      const stride = rulerTickStride(bits, bitW);
      expect(stride * bitW).toBeGreaterThanOrEqual(labelWidthPx(bits));
    }
  });

  it('is defensive against degenerate inputs', () => {
    expect(rulerTickStride(0, 16)).toBe(1);
    expect(rulerTickStride(NaN as unknown as number, 16)).toBe(1);
    expect(rulerTickStride(32, 0)).toBeGreaterThanOrEqual(1);
  });
});
