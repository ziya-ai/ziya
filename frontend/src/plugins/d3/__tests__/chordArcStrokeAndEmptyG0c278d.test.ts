/**
 * G-0c278d — chord plugin D-341 (arc stroke swamps hue at high N) and D-343
 * (all-zero matrix collapses to an overlapping label pile).
 *
 * D-341: the arc separator stroke was a hardcoded 1px regardless of arc width,
 * so at N=300 (arc footprint ~4px) the neutral stroke covered ~50% of each arc
 * and the per-arc HUE — the only channel distinguishing hundreds of nodes — was
 * lost (worst fill-vs-stroke 1.64 light / 1.03 dark). `chordArcStrokeWidth`
 * scales the stroke down to a thin delimiter (~10% of the arc footprint) as the
 * ring crowds, while leaving wide arcs at the historical 1px. Structural /
 * theme-independent: it changes the WIDTH, so it fixes BOTH themes at once.
 *
 * D-343: a degenerate all-zero matrix gave every d3.chord group a zero angular
 * span, piling all arcs+labels at 12 o'clock. `chordTotalFlow` detects the zero
 * total and `chordEvenLayoutMatrix` lays the arcs out on an even ring instead.
 *
 * DIRECTION: `chordArcStrokeWidth`, `chordTotalFlow` and `chordEvenLayoutMatrix`
 * are NEW exports absent on the pre-fix tree (this file will not compile against
 * it), and the assertions pin the new behaviour.
 */
import {
  chordArcStrokeWidth,
  chordTotalFlow,
  chordEvenLayoutMatrix,
} from '../chordPlugin';

describe('D-341 — arc separator stroke scales down as the ring crowds', () => {
  // outerRadius ~240 for a 600px canvas with the 60px gutter.
  const R = 240;

  it('wide arcs (small N) keep the historical 1px stroke — byte-identical', () => {
    // N=6 -> arc footprint ~251px, N=40 -> ~37px: both clamp to 1px.
    expect(chordArcStrokeWidth(6, R)).toBe(1);
    expect(chordArcStrokeWidth(40, R)).toBe(1);
    // N=150 is still readable pre-fix -> footprint ~10px -> stays ~1px.
    expect(chordArcStrokeWidth(150, R)).toBe(1);
  });

  it('crowded rings (high N) get a thinner stroke so the hue survives', () => {
    // N=300: pre-fix this was a fixed 1px swamping a ~5px footprint. Now < 1px.
    expect(chordArcStrokeWidth(300, R)).toBeLessThan(1);
    expect(chordArcStrokeWidth(300, R)).toBeGreaterThan(0);
    // Denser still is thinner but never vanishes (floored at 0.2px).
    expect(chordArcStrokeWidth(1000, R)).toBeGreaterThanOrEqual(0.2);
    expect(chordArcStrokeWidth(1000, R)).toBeLessThan(chordArcStrokeWidth(300, R));
  });

  it('degenerate inputs fall back to 1px', () => {
    expect(chordArcStrokeWidth(0, R)).toBe(1);
    expect(chordArcStrokeWidth(300, 0)).toBe(1);
  });
});

describe('D-343 — zero-flow matrix is detected and laid out on an even ring', () => {
  it('chordTotalFlow sums cells and is 0 only for an all-zero matrix', () => {
    expect(chordTotalFlow([[0, 0, 0], [0, 0, 0], [0, 0, 0]])).toBe(0);
    expect(chordTotalFlow([[0, 5], [3, 0]])).toBe(8);
    // Non-finite cells are ignored, not propagated as NaN.
    expect(chordTotalFlow([[0, Number.NaN], [Infinity, 0]])).toBe(0);
    expect(chordTotalFlow([] as number[][])).toBe(0);
  });

  it('chordEvenLayoutMatrix gives every group an equal (diagonal) span, zero off-diagonal', () => {
    const m = chordEvenLayoutMatrix(3);
    expect(m).toHaveLength(3);
    // Equal diagonal -> equal group angles -> arcs (and labels) spread evenly.
    expect(m).toEqual([[1, 0, 0], [0, 1, 0], [0, 0, 1]]);
    // Off-diagonal all zero -> only self-chords, which the render path drops.
    const total = chordTotalFlow(m);
    expect(total).toBe(3);
    expect(chordEvenLayoutMatrix(0)).toEqual([]);
  });
});
