/**
 * G-04 — force-directed theme + fit-to-extent regression tests.
 *
 * Covers the four defects worked as one group (shared root cause: the plugin
 * resolved colours from a raw isDarkMode flag and never fit the settled layout):
 *   D-016 computeFitTransform — settled extent fits the fixed viewBox
 *   D-017 default link stroke composited clears the 3:1 graphical floor
 *   D-018 label/node colours reconciled against the EFFECTIVE canvas
 *   D-019 no self-painted background (inherit the page surface, no seam)
 *
 * Each theme defect asserts BOTH themes. Direction: the helpers are NEW, and the
 * OLD hardcoded constants are shown to FAIL the same floors these now clear, so
 * the tests fail against the pre-fix behaviour rather than certifying it.
 */
import { contrastRatio, compositeOver } from '../chartTheme';
import {
  resolveForceColors,
  readableStroke,
  computeFitTransform,
  FORCE_DARK_BG,
  FORCE_LIGHT_BG,
} from '../forceDirectedPlugin';

describe('resolveForceColors — D-019 background (both themes)', () => {
  it('never self-paints a background when the caller pins none (inherits page)', () => {
    expect(resolveForceColors(true).paintBg).toBeNull();
    expect(resolveForceColors(false).paintBg).toBeNull();
  });

  it('does NOT emit the old #1a1a2e dark fill that split the panel', () => {
    const dark = resolveForceColors(true);
    expect(dark.effectiveBg.toLowerCase()).not.toBe('#1a1a2e');
    expect(dark.effectiveBg.toLowerCase()).toBe(FORCE_DARK_BG);
    // the old fill sat at ~1.06:1 against the ~#212121 page — a seam, by design gone
    expect(contrastRatio('#1a1a2e', FORCE_DARK_BG)).toBeLessThan(1.3);
  });

  it('paints and resolves contrast against a caller-pinned background', () => {
    // light panel pinned under dark theme (the "light island" case)
    const r = resolveForceColors(true, { background: '#f7f7f7' });
    expect(r.paintBg).toBe('#f7f7f7');
    expect(r.effectiveBg).toBe('#f7f7f7');
    expect(r.darkCanvas).toBe(false); // foreground must follow the RESOLVED bg
  });
});

describe('resolveForceColors — D-017 link stroke (both themes)', () => {
  it('default link edge clears 3:1 composited on both themes', () => {
    for (const isDark of [true, false]) {
      const { linkStroke, linkOpacity, effectiveBg } = resolveForceColors(isDark);
      const composite = compositeOver(linkStroke, effectiveBg, linkOpacity);
      expect(contrastRatio(composite, effectiveBg)).toBeGreaterThanOrEqual(3);
    }
  });

  it('direction: the OLD defaults (#999/#555 @0.6) were BELOW the floor', () => {
    expect(contrastRatio(compositeOver('#999999', FORCE_LIGHT_BG, 0.6), FORCE_LIGHT_BG)).toBeLessThan(3);
    expect(contrastRatio(compositeOver('#555555', FORCE_DARK_BG, 0.6), FORCE_DARK_BG)).toBeLessThan(3);
  });

  it('honours an explicit low opacity by darkening/lightening the stroke to compensate', () => {
    const r = resolveForceColors(true, { linkOpacity: 0.4 });
    expect(r.linkOpacity).toBe(0.4);
    expect(contrastRatio(compositeOver(r.linkStroke, r.effectiveBg, 0.4), r.effectiveBg)).toBeGreaterThanOrEqual(3);
  });
});

describe('resolveForceColors — D-018 label reconciliation (both themes)', () => {
  it('a light-tuned caller label (#333) is nudged readable in DARK, kept in LIGHT', () => {
    // Broken theme (dark): #333 verbatim on the dark page is ~1.3:1 — must be fixed.
    const dark = resolveForceColors(true, { labelColor: '#333333' });
    expect(contrastRatio(dark.labelColor, dark.effectiveBg)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio('#333333', FORCE_DARK_BG)).toBeLessThan(4.5); // direction: verbatim was broken
    // Other theme (light): #333 is already fine and must stay usable.
    const light = resolveForceColors(false, { labelColor: '#333333' });
    expect(contrastRatio(light.labelColor, light.effectiveBg)).toBeGreaterThanOrEqual(4.5);
  });

  it('default label colours are readable per-theme', () => {
    const dark = resolveForceColors(true);
    const light = resolveForceColors(false);
    expect(contrastRatio(dark.labelColor, dark.effectiveBg)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(light.labelColor, light.effectiveBg)).toBeGreaterThanOrEqual(4.5);
  });
});

describe('readableStroke', () => {
  it('returns a colour whose composite clears the floor, else best effort', () => {
    const s = readableStroke('#555555', FORCE_DARK_BG, 0.6, '#b0b0b0');
    expect(contrastRatio(compositeOver(s, FORCE_DARK_BG, 0.6), FORCE_DARK_BG)).toBeGreaterThanOrEqual(3);
  });
  it('passes named CSS colours through unchanged', () => {
    expect(readableStroke('crimson', FORCE_LIGHT_BG, 0.9, '#6b6b6b')).toBe('crimson');
  });
});

describe('computeFitTransform — D-016', () => {
  it('scales an oversize/off-centre extent down to fit and re-centres it', () => {
    // A graph spanning 0..2000 in a 700x500 box would be clipped without a fit.
    const pts = [
      { x: 0, y: 0, r: 8 },
      { x: 2000, y: 1500, r: 8 },
      { x: 1000, y: 750, r: 8 },
    ];
    const t = computeFitTransform(pts, 700, 500);
    expect(t.k).toBeLessThan(1);       // scaled down
    expect(t.k).toBeGreaterThanOrEqual(0.2);
    // centre of extent (~1000,750) must map near the viewport centre (350,250)
    expect(t.x + t.k * 1000).toBeCloseTo(350, 0);
    expect(t.y + t.k * 750).toBeCloseTo(250, 0);
  });

  it('does not blow tiny graphs past 2x and clamps within [0.2,2]', () => {
    const t = computeFitTransform([{ x: 350, y: 250, r: 8 }], 700, 500);
    expect(t.k).toBeLessThanOrEqual(2);
    expect(t.k).toBeGreaterThanOrEqual(0.2);
  });

  it('returns identity for no usable points or a degenerate viewport', () => {
    expect(computeFitTransform([], 700, 500)).toEqual({ k: 1, x: 0, y: 0 });
    expect(computeFitTransform([{ x: NaN, y: 0 }], 700, 500)).toEqual({ k: 1, x: 0, y: 0 });
    expect(computeFitTransform([{ x: 0, y: 0 }], 0, 0)).toEqual({ k: 1, x: 0, y: 0 });
  });

  it('ignores non-finite points but fits the finite remainder', () => {
    const t = computeFitTransform(
      [{ x: 0, y: 0, r: 0 }, { x: Infinity, y: 0 }, { x: 600, y: 400, r: 0 }],
      700, 500,
    );
    expect(Number.isFinite(t.k)).toBe(true);
    expect(Number.isFinite(t.x)).toBe(true);
    expect(Number.isFinite(t.y)).toBe(true);
  });
});
