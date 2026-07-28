/**
 * Regression test for Issue 9 (packet renderer): field bit-widths were fed
 * straight into SVG geometry (`fw = bits * BIT_W`, `fx = base + off * BIT_W`)
 * with no validation. A negative bit-width produced `<rect width="-448">`
 * (SVG rejects negative lengths → silent blank cell), and a huge bit-width
 * (e.g. 1e308) overflowed `bits * BIT_W` to Infinity → `<rect width="Infinity">`
 * plus a poisoned shared bit-offset accumulator that bled sibling fields
 * off-canvas.
 *
 * The fix extracts `sanitizeFieldBits()` (pure, DOM-free) which clamps the
 * degenerate/overflowing cases before they reach any SVG attribute. This test
 * imports the REAL shipped helper (not a copy) and pins both the happy path
 * and the guard cases.
 */
import {
  sanitizeFieldBits,
  PACKET_MAX_FIELD_BITS,
  defaultLayout,
} from '../../../utils/d3Plugins/packetPlugin';

describe('sanitizeFieldBits (packet Issue 9 geometry guard)', () => {
  // ── Happy path: well-formed field widths pass through UNCHANGED ──────────
  // (guards against the fix becoming a lossy catch-all)
  it.each([1, 2, 4, 8, 16, 32, 64, 128, 1000])(
    'leaves a legitimate positive integer width (%i) unchanged',
    (bits) => {
      expect(sanitizeFieldBits(bits)).toBe(bits);
    },
  );

  it('leaves the exact cap value unchanged', () => {
    expect(sanitizeFieldBits(PACKET_MAX_FIELD_BITS)).toBe(PACKET_MAX_FIELD_BITS);
  });

  // ── Negative → 0 (SVG forbids negative width; pre-fix rendered "-448") ───
  it('coerces a negative bit-width to 0', () => {
    expect(sanitizeFieldBits(-8)).toBe(0);
  });

  // ── Zero stays 0 (legitimately an invisible zero-width marker) ───────────
  it('keeps a zero bit-width at 0', () => {
    expect(sanitizeFieldBits(0)).toBe(0);
  });

  // ── Non-finite → 0 (pre-fix produced x/width="Infinity"/"NaN") ───────────
  it.each([
    ['Infinity', Infinity],
    ['-Infinity', -Infinity],
    ['NaN', NaN],
  ])('coerces %s to 0', (_label, value) => {
    expect(sanitizeFieldBits(value)).toBe(0);
  });

  // ── null / undefined / non-numeric → 0 (Number(null)=0, Number('x')=NaN) ─
  it.each([
    ['null', null],
    ['undefined', undefined],
    ['a non-numeric string', 'abc'],
  ])('coerces %s to 0', (_label, value) => {
    expect(sanitizeFieldBits(value as unknown)).toBe(0);
  });

  // ── Huge finite value → clamped to the cap ───────────────────────────────
  it('clamps an astronomically large bit-width to the cap', () => {
    expect(sanitizeFieldBits(1e308)).toBe(PACKET_MAX_FIELD_BITS);
    expect(sanitizeFieldBits(1_000_000)).toBe(PACKET_MAX_FIELD_BITS);
  });

  // ── The real reason for the cap: bits*BIT_W must stay finite & safe ──────
  it('guarantees clamped width * BIT_W stays a finite safe integer', () => {
    // BIT_W is at most 56 (the bitWidth<=8 branch of defaultLayout).
    const maxBitW = Math.max(
      defaultLayout(8).BIT_W,
      defaultLayout(16).BIT_W,
      defaultLayout(32).BIT_W,
      defaultLayout(64).BIT_W,
    );
    const worstPx = sanitizeFieldBits(Number.MAX_VALUE) * maxBitW;
    expect(Number.isFinite(worstPx)).toBe(true);
    expect(worstPx).toBeLessThanOrEqual(Number.MAX_SAFE_INTEGER);
    // Pre-fix, Number.MAX_VALUE * 56 overflowed to Infinity:
    expect(Number.MAX_VALUE * maxBitW).toBe(Infinity);
  });

  // ── End-to-end arithmetic the render loop performs, proven never invalid ─
  it('never yields a negative or non-finite rect width for adversarial inputs', () => {
    const BIT_W = defaultLayout(8).BIT_W;
    const adversarial = [-8, 0, 1e308, Infinity, NaN, null, -1, 1_000_000];
    for (const raw of adversarial) {
      const fw = sanitizeFieldBits(raw as unknown) * BIT_W;
      expect(Number.isFinite(fw)).toBe(true);
      expect(fw).toBeGreaterThanOrEqual(0);
    }
  });
});
