/**
 * G-57 / D-267 — authored-height-inflated-to-min.
 *
 * The Vega-Lite plugin ran, after width resolution and height defaulting:
 *
 *     if (vegaSpec.height && vegaSpec.height < 250) { vegaSpec.height = 300; }
 *
 * That floor was applied UNCONDITIONALLY, so an explicitly authored small
 * height — a sparkline / wide-and-short aspect (e.g. 40, 28) — was silently
 * inflated to 300px, killing the requested aspect ratio. It is correct only
 * for a height the plugin itself DEFAULTED (a short container yields ~240px,
 * a squashed plot).
 *
 * applyHeightFloor() encodes the corrected rule: honour an authored height
 * verbatim; keep the floor for a defaulted one. These tests exercise the real
 * exported helper, and each includes the DIRECTION check that the old
 * unconditional clamp would have failed (authored 40/28 -> 300).
 *
 * D-267 is kind:structural — the helper has no theme input and the sizing
 * decision is byte-identical in light and dark, so a both-theme render
 * assertion is not meaningful here; the pure-function contract is the fix.
 */
import { applyHeightFloor } from '../vegaSizing';

describe('applyHeightFloor (D-267)', () => {
  describe('authored heights are honoured verbatim', () => {
    it.each([40, 28, 60, 120, 150, 249])(
      'preserves an authored small height %ipx (old clamp inflated it to 300)',
      (h) => {
        // Direction: the pre-fix unconditional `h < 250 -> 300` would have
        // returned 300 here. The fix must return the authored value.
        expect(applyHeightFloor(h, /* wasAuthored */ true)).toBe(h);
        expect(applyHeightFloor(h, true)).not.toBe(300);
      },
    );

    it('preserves an authored height at/above the floor unchanged', () => {
      expect(applyHeightFloor(250, true)).toBe(250);
      expect(applyHeightFloor(400, true)).toBe(400);
      expect(applyHeightFloor(2400, true)).toBe(2400);
    });
  });

  describe('defaulted heights keep the small-value floor', () => {
    it.each([40, 100, 240, 249])(
      'floors a DEFAULTED small height %ipx to 300 (legit floor preserved)',
      (h) => {
        expect(applyHeightFloor(h, /* wasAuthored */ false)).toBe(300);
      },
    );

    it('leaves a defaulted height at/above the floor unchanged', () => {
      // Byte-identical to the old behaviour for the >=250 branch.
      expect(applyHeightFloor(250, false)).toBe(250);
      expect(applyHeightFloor(500, false)).toBe(500);
    });
  });

  it('is a no-op reassignment for a normal defaulted height (>=250)', () => {
    // The plugin now always assigns `height = applyHeightFloor(height, ...)`;
    // for a common defaulted 400 that must be identity, not a mutation.
    for (const h of [300, 400, 480, 500]) {
      expect(applyHeightFloor(h, false)).toBe(h);
    }
  });

  it('respects a custom floor/target when supplied', () => {
    expect(applyHeightFloor(80, false, 100, 120)).toBe(120);
    expect(applyHeightFloor(80, true, 100, 120)).toBe(80);
    expect(applyHeightFloor(150, false, 100, 120)).toBe(150);
  });
});
