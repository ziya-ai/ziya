/**
 * D-505 "autosize-none-canvas-oversized-underfill" (group G-6d32b0), vega engine.
 *
 * A native-Vega spec whose authored canvas is SQUARE or TALL with a
 * radial/hierarchical/force layout (donut vega-w1-05 340², sunburst w1-06 400²,
 * circle-pack w1-09 420², tidy-tree fan w2-06 420×900, force w2-12 700×560)
 * rendered COMPLETE but at NATIVE pixel size in the top-left corner of a
 * ~1280-wide delivered canvas, leaving 65-93% blank in BOTH themes — while a
 * LANDSCAPE native spec of the same autosize:"none" family (vega-w1-08 480×300,
 * vega-w1-10 460×320) filled correctly.
 *
 * Root cause: postRenderSizing sized the SVG with `width:'100%'`, which resolves
 * against vega-embed's `display:inline-block` `.vega-embed` wrapper. That
 * wrapper shrink-wraps to the SVG's own viewBox width, so the percentage width
 * folds back to the native size — a self-referential cycle that only collapses
 * for a spec whose intrinsic size is smaller than the container. The earlier
 * autosize:none→pad rewrite (D-505 first attempt) made Vega lay the view out
 * but did NOT break this cycle, so the underfill persisted (still-broken).
 *
 * Fix: `computeVegaFillSize` resolves an EXPLICIT container-width px size
 * (aspect preserved) that postRenderSizing assigns to the SVG, so the wrapper
 * grows to the SVG instead of the SVG collapsing to the wrapper.
 *
 * DIRECTION: `computeVegaFillSize` does not exist on the pre-fix tree, so the
 * import + assertions fail to compile/run without the change and pass with it.
 *
 * Sizing is theme-independent (pure geometry, no colour touched), so a single
 * structural assertion covers BOTH themes — stated explicitly per the contract.
 */
import { computeVegaFillSize } from '../vegaPlugin';

const CONTAINER_W = 1280;

describe('D-505 — native-Vega SVG fills the container width regardless of aspect', () => {
  test('a SQUARE donut/pack canvas fills the container width (the defect case)', () => {
    // vega-w1-05 donut 340², vega-w1-06 sunburst 400², vega-w1-09 pack 420².
    for (const side of [340, 400, 420]) {
      const fill = computeVegaFillSize(CONTAINER_W, side, side);
      expect(fill).not.toBeNull();
      // The SVG must fill the container width, NOT sit at its native size.
      expect(fill!.width).toBe(CONTAINER_W);
      expect(fill!.width).toBeGreaterThan(side);
      // A square canvas stays square once enlarged.
      expect(fill!.height).toBe(CONTAINER_W);
    }
  });

  test('a TALL fan / force canvas fills width and keeps its aspect', () => {
    // vega-w2-06 tidy-tree fan 420×900, vega-w2-12 force 700×560.
    const fan = computeVegaFillSize(CONTAINER_W, 420, 900);
    expect(fan).not.toBeNull();
    expect(fan!.width).toBe(CONTAINER_W);
    expect(fan!.height).toBe(Math.round(CONTAINER_W * (900 / 420)));

    const force = computeVegaFillSize(CONTAINER_W, 700, 560);
    expect(force).not.toBeNull();
    expect(force!.width).toBe(CONTAINER_W);
    expect(force!.height).toBe(Math.round(CONTAINER_W * (560 / 700)));
  });

  test('no regression: a LANDSCAPE spec that already filled is unchanged', () => {
    // vega-w1-08 480×300 (aspect 0.625) — width:100% + height:auto already
    // produced containerW × containerW·aspect; the explicit px size matches it.
    const fill = computeVegaFillSize(CONTAINER_W, 480, 300);
    expect(fill).not.toBeNull();
    expect(fill!.width).toBe(CONTAINER_W);
    expect(fill!.height).toBe(Math.round(CONTAINER_W * (300 / 480))); // 800
  });

  test('degenerate measurements fall back (null) so the 100%/auto path stays', () => {
    expect(computeVegaFillSize(0, 340, 340)).toBeNull();
    expect(computeVegaFillSize(1280, 0, 340)).toBeNull();
    expect(computeVegaFillSize(1280, 340, 0)).toBeNull();
    expect(computeVegaFillSize(-5, 340, 340)).toBeNull();
  });
});
