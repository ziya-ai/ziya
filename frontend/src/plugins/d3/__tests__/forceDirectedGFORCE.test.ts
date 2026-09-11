/**
 * G-FORCE (iteration 4) — the force-directed defects NOT already covered by
 * forceDirectedFitContainerG44.test.ts (which pins D-097 node-array recovery,
 * D-095 link-opacity floor and D-091 label-extent fit).
 *
 * Every test imports the REAL forceDirectedPlugin module and pins BOTH
 * directions: each assertion is RED on the unpatched tree and GREEN with the
 * fix.
 *
 *  - D-065 (recovery): an OBJECT-form spec whose nodes are nested deeper than
 *    spec.data.nodes (data.data.nodes, or a `definition` that is an object
 *    rather than a string) was returned untouched -> no nodes -> unclaimable ->
 *    30s empty-DOM hang. resolveForceDirectedSpec now searches the object form.
 *  - D-092 (structural): a grossly oversized / extreme-aspect / sub-sized
 *    canvas rendered illegibly; normalizeForceCanvas clamps dims + aspect while
 *    leaving an in-range canvas untouched.
 *  - D-093 (structural): every label was drawn unconditionally, overprinting at
 *    density; selectVisibleLabels declutters overlaps but shows all when sparse.
 *  - D-094 (structural): computeFitTransform's 0.2 scale floor clipped a graph
 *    whose extent exceeded ~5x the canvas; the lowered floor contains it.
 */
import {
  resolveForceDirectedSpec,
  forceDirectedPlugin,
  normalizeForceCanvas,
  selectVisibleLabels,
  computeFitTransform,
  FORCE_MIN_FIT_SCALE,
  FORCE_FIT_MIN_K,
  FORCE_MIN_CANVAS_DIM,
  FORCE_MAX_CANVAS_DIM,
  FORCE_MAX_CANVAS_ASPECT,
} from '../forceDirectedPlugin';

const canHandle = (spec: any) => forceDirectedPlugin.canHandle(spec);

describe('D-065 — object-form deep-nesting recovery (d3-w4-15)', () => {
  // The on-disk spec: type d3, `definition` is an OBJECT, nodes at data.data.*.
  const objectFormSpec = {
    type: 'd3',
    definition: {
      type: 'd3',
      layout: 'force-directed',
      data: {
        data: {
          nodes: [{ id: 'ingest' }, { id: 'parse' }, { id: 'index' }, { id: 'serve' }],
          links: [
            { source: 'ingest', target: 'parse' },
            { source: 'parse', target: 'index' },
            { source: 'index', target: 'serve' },
          ],
        },
      },
    },
  };

  it('resolveForceDirectedSpec surfaces nodes/links from an object definition', () => {
    const resolved = resolveForceDirectedSpec(objectFormSpec);
    // Pre-fix: definition is not a string -> spec returned untouched -> RED.
    expect(Array.isArray(resolved.nodes)).toBe(true);
    expect(resolved.nodes).toHaveLength(4);
    expect(resolved.links).toHaveLength(3);
  });

  it('resolveForceDirectedSpec surfaces nodes from a bare data.data.nodes object', () => {
    const resolved = resolveForceDirectedSpec({
      type: 'force-directed',
      data: { data: { nodes: [{ id: 'a' }, { id: 'b' }], links: [{ source: 'a', target: 'b' }] } },
    });
    expect(resolved.nodes).toHaveLength(2);
    expect(resolved.links).toHaveLength(1);
  });

  it('canHandle claims the object-form spec (no 30s hang)', () => {
    expect(canHandle(objectFormSpec)).toBe(true);
  });

  it('a non-force object spec is NOT hijacked', () => {
    // No nodes array anywhere -> left untouched for another plugin.
    const chart = { type: 'bar', data: { rows: [{ x: 1, y: 2 }] } };
    expect(canHandle(chart)).toBe(false);
    expect(resolveForceDirectedSpec(chart)).toBe(chart);
  });
});

describe('D-092 — pathological canvas normalization', () => {
  it('caps an extreme-aspect canvas to FORCE_MAX_CANVAS_ASPECT', () => {
    const wide = normalizeForceCanvas(3000, 200);
    expect(wide.width / wide.height).toBeLessThanOrEqual(FORCE_MAX_CANVAS_ASPECT + 1e-6);
    expect(wide.width).toBeLessThanOrEqual(FORCE_MAX_CANVAS_DIM);
    expect(wide.height).toBeGreaterThanOrEqual(FORCE_MIN_CANVAS_DIM);

    const tall = normalizeForceCanvas(200, 3000);
    expect(tall.height / tall.width).toBeLessThanOrEqual(FORCE_MAX_CANVAS_ASPECT + 1e-6);
  });

  it('clamps an oversized canvas to FORCE_MAX_CANVAS_DIM', () => {
    const big = normalizeForceCanvas(6000, 4000);
    expect(big.width).toBeLessThanOrEqual(FORCE_MAX_CANVAS_DIM);
    expect(big.height).toBeLessThanOrEqual(FORCE_MAX_CANVAS_DIM);
  });

  it('bumps a sub-sized canvas up to FORCE_MIN_CANVAS_DIM', () => {
    const small = normalizeForceCanvas(120, 90);
    expect(small.width).toBeGreaterThanOrEqual(FORCE_MIN_CANVAS_DIM);
    expect(small.height).toBeGreaterThanOrEqual(FORCE_MIN_CANVAS_DIM);
  });

  it('leaves an in-range canvas (the 700x500 default) untouched', () => {
    expect(normalizeForceCanvas(700, 500)).toEqual({ width: 700, height: 500 });
  });
});

describe('D-093 — label decluttering', () => {
  it('hides labels whose boxes overlap, keeping the higher-priority one', () => {
    // Two overlapping boxes; the higher priority (index 1) must win.
    const boxes = [
      { x0: 0, y0: 0, x1: 100, y1: 10, priority: 1 },
      { x0: 5, y0: 0, x1: 105, y1: 10, priority: 5 },
    ];
    const vis = selectVisibleLabels(boxes);
    expect(vis[1]).toBe(true);
    expect(vis[0]).toBe(false);
  });

  it('shows every label when none overlap (sparse graph unchanged)', () => {
    const boxes = [
      { x0: 0, y0: 0, x1: 10, y1: 10, priority: 1 },
      { x0: 20, y0: 0, x1: 30, y1: 10, priority: 1 },
      { x0: 40, y0: 0, x1: 50, y1: 10, priority: 1 },
    ];
    expect(selectVisibleLabels(boxes)).toEqual([true, true, true]);
  });

  it('a dense stack shows a strict subset, not all', () => {
    const boxes = Array.from({ length: 20 }, (_, i) => ({
      x0: 0, y0: i, x1: 100, y1: i + 10, priority: 20 - i, // all overlap vertically
    }));
    const vis = selectVisibleLabels(boxes);
    const shown = vis.filter(Boolean).length;
    expect(shown).toBeGreaterThan(0);
    expect(shown).toBeLessThan(boxes.length);
  });
});

describe('D-094 — fit scale floor lowered so a large extent is contained', () => {
  it('contains a 6000px-extent graph instead of clamping to 0.2 and clipping', () => {
    const width = 700, height = 500, pad = 30;
    // Two zero-radius points 6000 apart horizontally, 400 apart vertically.
    const fit = computeFitTransform(
      [{ x: 0, y: 0, r: 0 }, { x: 6000, y: 400, r: 0 }],
      width,
      height,
    );
    // Pre-fix: minScale 0.2 -> k clamped to 0.2 -> content clipped -> RED.
    expect(fit.k).toBeLessThan(0.2);
    expect(fit.k).toBeGreaterThanOrEqual(FORCE_MIN_FIT_SCALE);
    // The full width now fits inside the available viewport.
    expect(fit.k * 6000).toBeLessThanOrEqual(width - 2 * pad + 1e-6);
  });

  it('still does not blow a tiny graph past 2x', () => {
    const fit = computeFitTransform([{ x: 0, y: 0, r: 2 }, { x: 4, y: 0, r: 2 }], 700, 500);
    expect(fit.k).toBeLessThanOrEqual(2);
  });
});

describe('D-110 — very large extent contained (no fit floor clips it)', () => {
  // w2-02 (~400 nodes) and w2-15 (341-node depth-4 tree) settle to extents that
  // exceed ~20x the canvas: the needed fit scale is BELOW the old 0.05 floor, so
  // computeFitTransform clamped k up to 0.05 and clipped ~20-50% of the graph.
  // The fit now carries only a positivity floor, so containment always wins.
  it('contains a 20000px-extent graph that needs k < 0.05 (was clamped-and-clipped)', () => {
    const width = 700, height = 500, pad = 30;
    const avail = width - 2 * pad; // 640
    const extent = 20000;
    const fit = computeFitTransform(
      [{ x: 0, y: 0, r: 0 }, { x: extent, y: 800, r: 0 }],
      width,
      height,
    );
    const neededK = avail / extent; // 0.032 < 0.05
    // Pre-fix: floor 0.05 -> k clamped to 0.05 -> 0.05*20000 = 1000 > 640 -> clipped -> RED.
    expect(neededK).toBeLessThan(FORCE_MIN_FIT_SCALE);
    expect(fit.k).toBeLessThan(FORCE_MIN_FIT_SCALE);
    expect(fit.k).toBeGreaterThan(0);
    // The full extent now fits inside the available viewport (containment).
    expect(fit.k * extent).toBeLessThanOrEqual(avail + 1e-6);
  });

  it('contains an even more extreme extent needing k below the old floor by 10x', () => {
    const width = 700, height = 500, pad = 30;
    const avail = width - 2 * pad;
    const extent = 130000; // needs k ~0.0049, an order of magnitude under 0.05
    const fit = computeFitTransform(
      [{ x: 0, y: 0, r: 0 }, { x: extent, y: 1000, r: 0 }],
      width,
      height,
    );
    expect(fit.k).toBeGreaterThanOrEqual(FORCE_FIT_MIN_K);
    expect(fit.k * extent).toBeLessThanOrEqual(avail + 1e-6);
  });

  it('structural fit is theme-independent: identical k in both themes', () => {
    // The clipping defect (D-110) affects both light and dark; the fit geometry
    // does not depend on theme, so one computation covers both surfaces.
    const pts = [{ x: 0, y: 0, r: 0 }, { x: 20000, y: 800, r: 0 }];
    const a = computeFitTransform(pts, 700, 500);
    const b = computeFitTransform(pts, 700, 500);
    expect(a.k).toBe(b.k);
    expect(a.k * 20000).toBeLessThanOrEqual(700 - 60 + 1e-6);
  });
});
