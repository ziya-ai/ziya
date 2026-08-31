/**
 * G-41 — chord plugin legibility/theme fixes (D-055, D-060, D-061, D-062).
 *
 * All four defects live in chordPlugin.ts. Each test asserts the DIRECTION of
 * the fix: the pre-fix behaviour would fail the assertion, the post-fix helper
 * passes it. D-055 is a theme defect, so its ribbon-fill contrast is asserted
 * in BOTH themes (the broken theme is now correct AND the other stays correct).
 */
import { contrastRatio, compositeOver } from '../chartTheme';
import {
  chordRibbonOpacity,
  chordRibbonFill,
  chordCanvasSize,
  chordLabelGutter,
  chordLabelMaxChars,
  chordLabelKeepEvery,
  chordRadii,
  resolveChordFill,
} from '../chordPlugin';

const LIGHT_BG = '#ffffff';
const DARK_BG = '#1a1a2e';
const GRAPHICAL_FLOOR = 3;
const PALETTE = [
  '#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f',
  '#edc948', '#b07aa1', '#ff9da7', '#9c755f', '#bab0ac',
];

// ── D-055: low-opacity ribbon below 3:1 (THEME, both themes) ─────────────────
describe('D-055 chordRibbonOpacity — floor the fill-opacity', () => {
  it('honours the 0.7 default and any value at/above the floor (byte-identical)', () => {
    expect(chordRibbonOpacity(undefined)).toBe(0.7);
    expect(chordRibbonOpacity(0.7)).toBe(0.7);
    expect(chordRibbonOpacity(0.85)).toBe(0.85);
  });
  it('raises a sub-floor request (chord-w1-12 used 0.25) to 0.6', () => {
    // Direction: the reported failing spec asked for 0.25; verbatim that is a
    // ~1.1:1 ghost wash. The clamp lifts it to the 0.6 floor.
    expect(chordRibbonOpacity(0.25)).toBe(0.6);
    expect(chordRibbonOpacity(0.1)).toBe(0.6);
  });
  it('clamps above 1', () => {
    expect(chordRibbonOpacity(1.5)).toBe(1);
  });
});

describe('D-055 chordRibbonFill — composited ribbon clears 3:1 in BOTH themes', () => {
  const opacities = [0.6, 0.7];
  for (const bg of [LIGHT_BG, DARK_BG]) {
    for (const a of opacities) {
      it(`every reconciled palette ribbon >= 3:1 composited @${a} on ${bg}`, () => {
        for (let i = 0; i < PALETTE.length; i++) {
          const base = resolveChordFill(undefined, i, bg); // arc reconciled fill
          const fill = chordRibbonFill(base, bg, a);
          const composited = compositeOver(fill, bg, a);
          expect(contrastRatio(composited, bg)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR - 1e-6);
        }
      });
    }
  }

  it('DIRECTION: the un-nudged reconciled fill composited @0.7 is BELOW 3:1 (fix is load-bearing) — light', () => {
    // On white, reconcile-to-3-solid leaves no headroom, so compositing drops
    // every entry below the floor; chordRibbonFill is what restores it.
    const anyBroken = PALETTE.some((_, i) => {
      const base = resolveChordFill(undefined, i, LIGHT_BG);
      return contrastRatio(compositeOver(base, LIGHT_BG, 0.7), LIGHT_BG) < GRAPHICAL_FLOOR;
    });
    expect(anyBroken).toBe(true);
  });

  it('DIRECTION: at the reported 0.25 opacity the raw fill is a ghost (<1.6:1) in BOTH themes', () => {
    const base = resolveChordFill(undefined, 0, LIGHT_BG);
    expect(contrastRatio(compositeOver(base, LIGHT_BG, 0.25), LIGHT_BG)).toBeLessThan(1.6);
    const baseD = resolveChordFill(undefined, 0, DARK_BG);
    expect(contrastRatio(compositeOver(baseD, DARK_BG, 0.25), DARK_BG)).toBeLessThan(2.0);
  });

  it('preserves the arc colour verbatim when its composite already clears the floor (association kept)', () => {
    // Find an entry whose composite @0.7 on dark already passes; it must be
    // returned unchanged (hue/identity preserved, no gratuitous nudge).
    let checked = 0;
    for (let i = 0; i < PALETTE.length; i++) {
      const base = resolveChordFill(undefined, i, DARK_BG);
      if (contrastRatio(compositeOver(base, DARK_BG, 0.7), DARK_BG) >= GRAPHICAL_FLOOR) {
        expect(chordRibbonFill(base, DARK_BG, 0.7)).toBe(base);
        checked++;
      }
    }
    expect(checked).toBeGreaterThan(0);
  });

  it('degrades safely on non-hex input', () => {
    expect(chordRibbonFill('rebeccapurple', LIGHT_BG, 0.7)).toBe('rebeccapurple');
  });
});

// ── D-062: radius from min dimension wastes an extreme aspect ─────────────────
describe('D-062 chordCanvasSize — cap extreme aspect, no-op for square-ish', () => {
  it('leaves a normal square-ish canvas unchanged (byte-identical)', () => {
    expect(chordCanvasSize(600, 600)).toEqual({ width: 600, height: 600 });
    expect(chordCanvasSize(1400, 600)).toEqual({ width: 1400, height: 600 }); // 2.33 <= 2.5
    expect(chordCanvasSize(800, 400)).toEqual({ width: 800, height: 400 });   // 2.0
  });
  it('caps the wide chord-w2-12 canvas so the ring is not marooned', () => {
    // 1800x200 (aspect 9) -> width capped to 200*2.5 = 500, height kept.
    expect(chordCanvasSize(1800, 200)).toEqual({ width: 500, height: 200 });
    // Direction: pre-fix the SVG stayed 1800 wide -> massive downscale.
    expect(chordCanvasSize(1800, 200).width).toBeLessThan(1800);
  });
  it('caps a tall extreme aspect on the height axis', () => {
    expect(chordCanvasSize(200, 1800)).toEqual({ width: 200, height: 500 });
  });
});

// ── D-060: long labels clipped by the fixed 60px gutter ──────────────────────
describe('D-060 chordLabelGutter / chordLabelMaxChars — grow gutter, truncate overflow', () => {
  it('short-label diagrams keep the historical 60px gutter (byte-identical)', () => {
    const names = ['A', 'B', 'Cat', 'Dog', 'Echo']; // <=7 chars
    expect(chordLabelGutter(names, 11, 600)).toBe(60);
    // and chordRadii with that gutter reproduces the legacy radii.
    expect(chordRadii(600, 600, chordLabelGutter(names, 11, 600)))
      .toEqual({ outerRadius: 240, innerRadius: 222 });
  });
  it('grows the gutter for long labels, capped at 30% of the smaller dim', () => {
    const long = 'Northwest Regional Distribution Center Alpha'; // 44 chars
    const g = chordLabelGutter([long], 11, 600);
    expect(g).toBeGreaterThan(60);              // grew
    expect(g).toBeLessThanOrEqual(600 * 0.30);  // capped so the ring survives
  });
  it('truncates a label that still exceeds the capped gutter (no asymmetric clip)', () => {
    const long = 'X'.repeat(60);
    const g = chordLabelGutter([long], 11, 600);
    const maxChars = chordLabelMaxChars(g, 11);
    expect(maxChars).toBeGreaterThan(0);
    expect(maxChars).toBeLessThan(60); // 60-char label WILL be ellipsised
  });
  it('chordRadii default gutter is still 60 (D-059 backward-compat)', () => {
    expect(chordRadii(600, 600)).toEqual({ outerRadius: 240, innerRadius: 222 });
    expect(chordRadii(100, 100)).toEqual({ outerRadius: 10, innerRadius: 1 });
  });
});

// ── D-061: label crowding illegible at density ───────────────────────────────
describe('D-061 chordLabelKeepEvery — thin labels above a density threshold', () => {
  it('keeps every label at normal density (small N) — unchanged', () => {
    expect(chordLabelKeepEvery(6, 240, 11)).toBe(1);
    expect(chordLabelKeepEvery(20, 240, 11)).toBe(1);
  });
  it('keeps all labels at the N=80 legible onset but thins at N=100 (matches triage)', () => {
    // outerRadius ~240 at a default 600 canvas.
    expect(chordLabelKeepEvery(80, 240, 11)).toBe(1);
    expect(chordLabelKeepEvery(100, 240, 11)).toBeGreaterThanOrEqual(2);
  });
  it('thins harder as N grows', () => {
    expect(chordLabelKeepEvery(300, 240, 11))
      .toBeGreaterThan(chordLabelKeepEvery(100, 240, 11));
  });
});
