/**
 * D-020 / G-18 — vega native postRenderSizing: re-tick legibility FLOOR.
 *
 * CONTEXT (confirmed against source this iteration):
 *   The high-severity core of D-020 (postrender viewBox aspect-clip and the
 *   authored-canvas-scale "no text floor" clusters) is largely already resolved
 *   in source by prior D-276 (overflow:visible + aspect-matched container),
 *   D-277 (computeReTickDimensions: re-tick an out-of-range authored canvas at
 *   the delivered size instead of uniformly scaling geometry) and D-283
 *   (resolveVegaViewBox: full-bbox viewBox with a flood clip). This test pins
 *   the one genuinely-remaining gap the cluster is named for: the re-tick had
 *   NO legibility floor, so an extreme-WIDE authored canvas (vega-w2-07
 *   2400x90, aspect ~26:1) re-ticked to a ~700x26 strip whose axis labels/title
 *   stay sub-legible after the re-tick.
 *
 * THE FIX (computeReTickDimensions, new opts.minLegibleShortAxis, default 160):
 *   when the SHORT axis of the re-tick target falls below the floor, lift it so
 *   Vega RE-LAYS OUT the chart at a legible size (a taller re-layout, not a
 *   pixel stretch). targetW is already clamped to >= minLegibleWidth, so only
 *   the height can fall below the floor.
 *
 * DIRECTION (fails without the change): on the unpatched tree
 *   computeReTickDimensions(2400, 90, 700) returns height 26; with the floor it
 *   returns height >= 160. The regression guards below assert the floor NEVER
 *   fires for a normal (in-range -> null) spec, for an already-tall re-tick, or
 *   for an undersize canvas whose re-tick is already above the floor — so the
 *   D-277 regression contract is preserved.
 *
 * Structural / geometry-only defect: theme-independent, so no contrast ratios;
 * light and dark re-tick to identical dimensions.
 */
import { computeReTickDimensions } from '../vegaPlugin';

describe('D-020/G-18 computeReTickDimensions legibility floor', () => {
  it('lifts an extreme-WIDE strip to the legibility floor (vega-w2-07 2400x90)', () => {
    // Unpatched: { width: 700, height: 26 }. Patched: height floored to >= 160.
    const r = computeReTickDimensions(2400, 90, 700);
    expect(r).not.toBeNull();
    expect(r!.width).toBe(700);
    expect(r!.height).toBeGreaterThanOrEqual(160);
  });

  it('honours an explicit minLegibleShortAxis override', () => {
    const r = computeReTickDimensions(2400, 90, 700, { minLegibleShortAxis: 220 });
    expect(r).not.toBeNull();
    expect(r!.height).toBe(220);
  });

  it('does NOT distort an oversize canvas whose re-tick is already above the floor (vega-w2-10 3600x2600)', () => {
    // 2600 * (700/3600) = 505.5 -> 506, already >> floor: unchanged.
    const r = computeReTickDimensions(3600, 2600, 700);
    expect(r).not.toBeNull();
    expect(r!.width).toBe(700);
    expect(r!.height).toBe(506);
  });

  it('does NOT touch an extreme-TALL canvas (short axis is the width, already >= minLegibleWidth) (vega-w2-08 110x1600)', () => {
    const r = computeReTickDimensions(110, 1600, 700);
    expect(r).not.toBeNull();
    expect(r!.width).toBe(700);
    // aspect preserved, height not floored/capped: 1600 * (700/110) = 10182.
    expect(r!.height).toBe(Math.round(1600 * (700 / 110)));
  });

  it('does NOT touch an undersize canvas whose re-tick already clears the floor (vega-w2-09 70x45)', () => {
    // 45 * (700/70) = 450, above the floor.
    const r = computeReTickDimensions(70, 45, 700);
    expect(r).not.toBeNull();
    expect(r!.height).toBe(450);
  });

  it('leaves a NORMAL in-range authored width completely untouched (returns null) — no regression', () => {
    // Every vega regression-set spec has width in [200,1600].
    expect(computeReTickDimensions(420, 220, 700)).toBeNull();
    expect(computeReTickDimensions(900, 500, 700)).toBeNull();
    expect(computeReTickDimensions(520, 300, 700)).toBeNull();
  });

  it('still floors an undersize canvas whose natural re-tick would be a sub-legible strip', () => {
    // 70x8 (extreme-wide AND undersize width) -> targetW 700, natural height
    // 8*(700/70)=80 < floor -> lifted.
    const r = computeReTickDimensions(70, 8, 700);
    expect(r).not.toBeNull();
    expect(r!.height).toBeGreaterThanOrEqual(160);
  });
});
