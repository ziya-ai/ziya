/**
 * G-988a6d — regression lock for the legacy force-directed / d3 defect cluster
 * (D-079, D-081, D-107, D-108, D-109, D-114).
 *
 * These six legacy defects (first_seen legacy-2026-09-01) were each superseded
 * by a later, more general sibling fix already present in source and cited in
 * forceDirectedPlugin.ts / chartTheme.ts:
 *   D-079  named-colour-bypasses-contrast-guard        ← D-001 (named→hex reconciliation)
 *   D-081  object-form-graph-nesting-not-recovered     ← D-065 (object-form findGraphContainer)
 *   D-114  container-shape-unrecognised (values/verts) ← D-097 (node-array aliases)
 *   D-107  fit-pass-clips-graph-that-fits              ← D-091 (label bounds in fit box)
 *   D-108  pathological-canvas-blank-or-illegible      ← D-092 (normalizeForceCanvas)
 *   D-109  no-label-decluttering-at-scale              ← D-093 (selectVisibleLabels)
 *
 * This suite pins each legacy SPEC PATH concretely so a future refactor that
 * unwinds any sibling fix re-breaks a named test. Every assertion is written so
 * that the pre-fix code would FAIL it (direction is stated inline). The theme
 * defect (D-079) is asserted in BOTH themes.
 */
import {
  resolveForceDirectedSpec,
  findGraphContainer,
  resolveNodeFill,
  forceFitPoints,
  computeFitTransform,
  labelRightExtent,
  normalizeForceCanvas,
  selectVisibleLabels,
  FORCE_LIGHT_BG,
  FORCE_DARK_BG,
  FORCE_MIN_CANVAS_DIM,
  FORCE_MAX_CANVAS_DIM,
  FORCE_MAX_CANVAS_ASPECT,
} from '../forceDirectedPlugin';
import { classifyColor, contrastRatio, namedColorToHex } from '../chartTheme';

/** Resolve a returned fill (name or hex) to #rrggbb for contrast math. */
function toHex(out: string): string | null {
  return classifyColor(out)?.hex ?? namedColorToHex(out) ?? null;
}

// ── D-079 — named node fills reconciled per theme (spec d3-w4-12) ────────────
describe('D-079 named node-fill keywords are contrast-reconciled per theme', () => {
  // The five node colours from d3-w4-12. Raw ratios (measured): gold 1.40:1 on
  // white; darkslategray 1.80:1 and rebeccapurple 1.92:1 on the dark canvas —
  // all below the 3:1 graphical floor and INVERTING by theme.
  const NAMES = ['rebeccapurple', 'tomato', 'darkslategray', 'gold', 'seagreen'];

  it('every node fill clears 3:1 on the LIGHT canvas (gold no longer 1.40:1)', () => {
    for (const name of NAMES) {
      const out = resolveNodeFill({ color: name }, {}, FORCE_LIGHT_BG);
      const hex = toHex(out)!;
      expect(hex).toBeTruthy();
      expect(contrastRatio(hex, FORCE_LIGHT_BG)).toBeGreaterThanOrEqual(3);
    }
    // Direction: pre-fix the bare 'gold' reached the canvas verbatim and failed.
    expect(contrastRatio(namedColorToHex('gold')!, FORCE_LIGHT_BG)).toBeLessThan(3);
  });

  it('every node fill clears 3:1 on the DARK canvas (darkslategray/rebeccapurple lifted)', () => {
    for (const name of NAMES) {
      const out = resolveNodeFill({ color: name }, {}, FORCE_DARK_BG);
      const hex = toHex(out)!;
      expect(hex).toBeTruthy();
      expect(contrastRatio(hex, FORCE_DARK_BG)).toBeGreaterThanOrEqual(3);
    }
    // Direction: pre-fix these two named fills were painted verbatim below floor.
    expect(contrastRatio(namedColorToHex('darkslategray')!, FORCE_DARK_BG)).toBeLessThan(3);
    expect(contrastRatio(namedColorToHex('rebeccapurple')!, FORCE_DARK_BG)).toBeLessThan(3);
  });
});

// ── D-081 — object-form graph nested one level too deep (spec d3-w4-15) ───────
describe('D-081 object-form spec with data.data.{nodes,links} is recovered', () => {
  const spec = {
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

  it('surfaces all 4 nodes and 3 links from the object form (no string definition)', () => {
    const resolved = resolveForceDirectedSpec(spec);
    // Direction: the old definition-string-only recovery returned the spec
    // untouched (nodes buried at data.data.nodes) → zero nodes → 30s hang.
    expect(Array.isArray(resolved.nodes)).toBe(true);
    expect(resolved.nodes).toHaveLength(4);
    expect(Array.isArray(resolved.links)).toBe(true);
    expect(resolved.links).toHaveLength(3);
    expect(resolved.nodes.map((n: any) => n.id)).toEqual(['ingest', 'parse', 'index', 'serve']);
  });
});

// ── D-114 — alternately-named node arrays (vega-lite values / vertices) ───────
describe('D-114 findGraphContainer recognises values/vertices node arrays', () => {
  it('finds a vega-lite-style data.values node array with root-level links', () => {
    const parsed = {
      data: { values: [{ id: 'a' }, { id: 'b' }] },
      links: [{ source: 'a', target: 'b' }],
    };
    const found = findGraphContainer(parsed);
    // Direction: pre-fix findGraphContainer keyed ONLY on `nodes`, so a
    // `values` array was never found → unclaimable → silent 30s timeout.
    expect(found).toBeTruthy();
    expect(found!.nodes).toHaveLength(2);
  });

  it('finds a `vertices` node array', () => {
    const found = findGraphContainer({ vertices: [{ id: 'x' }], edges: [] });
    expect(found).toBeTruthy();
    expect(found!.nodes).toHaveLength(1);
  });

  it('resolveForceDirectedSpec back-fills root links for a data.values graph', () => {
    const resolved = resolveForceDirectedSpec({
      type: 'force-directed',
      data: { values: [{ id: 'a' }, { id: 'b' }] },
      links: [{ source: 'a', target: 'b' }],
    });
    expect(resolved.nodes).toHaveLength(2);
    expect(resolved.links).toHaveLength(1);
  });
});

// ── D-107 — the label bounding box is part of the fit extent ──────────────────
describe('D-107 fit box includes node labels so a graph that fits is not clipped', () => {
  it('forceFitPoints emits a point at the far end of each label', () => {
    const nodes = [{ x: 100, y: 100, id: 'n', label: 'a-very-long-node-label' }];
    const radiusOf = () => 10;
    const pts = forceFitPoints(nodes, radiusOf, 12);
    const maxX = Math.max(...pts.map((p) => p.x + (p.r || 0)));
    const discOnlyMaxX = 100 + 10 + 4; // node centre + r + gap, no label term
    // Direction: fitting only the disc gave discOnlyMaxX; the label extends well
    // past it, so pre-fix the label clipped at the canvas edge.
    expect(maxX).toBeGreaterThan(discOnlyMaxX);
    expect(maxX).toBeGreaterThanOrEqual(100 + labelRightExtent(22, 10, 12) - 1);
  });

  it('a compact graph with a right-edge long label lands entirely inside the canvas', () => {
    const width = 760;
    const height = 520;
    const fontSize = 12;
    // A node near the right edge whose label would run off-canvas if unfit.
    const nodes = [
      { x: 120, y: 260, id: 'a', label: 'A' },
      { x: 700, y: 260, id: 'b', label: 'rightmost-long-label-node' },
    ];
    const radiusOf = () => 12;
    const pts = forceFitPoints(nodes, radiusOf, fontSize);
    const fit = computeFitTransform(pts, width, height);
    // Every fit point, transformed, must land within [0,width]×[0,height].
    for (const p of pts) {
      const sx = fit.x + fit.k * p.x;
      const sy = fit.y + fit.k * p.y;
      expect(sx).toBeGreaterThanOrEqual(-1);
      expect(sx).toBeLessThanOrEqual(width + 1);
      expect(sy).toBeGreaterThanOrEqual(-1);
      expect(sy).toBeLessThanOrEqual(height + 1);
    }
  });
});

// ── D-108 — pathological canvas dimensions are normalised into a legible range ─
describe('D-108 normalizeForceCanvas rescues pathological canvas dimensions', () => {
  it('clamps an oversized 6000×4000 canvas into the legible range', () => {
    const { width, height } = normalizeForceCanvas(6000, 4000);
    expect(width).toBeLessThanOrEqual(FORCE_MAX_CANVAS_DIM);
    expect(height).toBeLessThanOrEqual(FORCE_MAX_CANVAS_DIM);
  });

  it('caps an extreme 3000×200 (15:1) aspect ratio', () => {
    const { width, height } = normalizeForceCanvas(3000, 200);
    expect(Math.max(width, height) / Math.min(width, height)).toBeLessThanOrEqual(
      FORCE_MAX_CANVAS_ASPECT + 1e-6,
    );
  });

  it('caps an extreme 200×3000 (1:15) aspect ratio', () => {
    const { width, height } = normalizeForceCanvas(200, 3000);
    expect(Math.max(width, height) / Math.min(width, height)).toBeLessThanOrEqual(
      FORCE_MAX_CANVAS_ASPECT + 1e-6,
    );
  });

  it('raises a sub-sized 120×90 canvas to the minimum dimension', () => {
    const { width, height } = normalizeForceCanvas(120, 90);
    expect(width).toBeGreaterThanOrEqual(FORCE_MIN_CANVAS_DIM);
    expect(height).toBeGreaterThanOrEqual(FORCE_MIN_CANVAS_DIM);
  });

  it('leaves the in-range 700×500 default untouched', () => {
    expect(normalizeForceCanvas(700, 500)).toEqual({ width: 700, height: 500 });
  });
});

// ── D-109 — label decluttering hides overlapping labels at density ────────────
describe('D-109 selectVisibleLabels declutters overlapping labels', () => {
  it('hides labels whose boxes overlap an already-kept higher-priority label', () => {
    // Three heavily overlapping boxes at the same spot with distinct priorities.
    const boxes = [
      { x0: 0, y0: 0, x1: 100, y1: 20, priority: 3 },
      { x0: 5, y0: 2, x1: 105, y1: 22, priority: 2 },
      { x0: 10, y0: 4, x1: 110, y1: 24, priority: 1 },
    ];
    const vis = selectVisibleLabels(boxes);
    // Direction: pre-fix every label was drawn unconditionally (all true) →
    // overprinted mat. Now only the highest-priority box survives.
    expect(vis.filter(Boolean)).toHaveLength(1);
    expect(vis[0]).toBe(true); // priority 3 kept
  });

  it('shows every label when none overlap (sparse graph unchanged)', () => {
    const boxes = [
      { x0: 0, y0: 0, x1: 40, y1: 20, priority: 1 },
      { x0: 100, y0: 0, x1: 140, y1: 20, priority: 1 },
      { x0: 200, y0: 0, x1: 240, y1: 20, priority: 1 },
    ];
    expect(selectVisibleLabels(boxes)).toEqual([true, true, true]);
  });
});
