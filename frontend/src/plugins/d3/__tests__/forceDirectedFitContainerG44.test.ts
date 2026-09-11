/**
 * D-049 / G-44 — force-directed fit-transform, link-opacity floor, and
 * container node-array recovery.
 *
 * These tests import the REAL forceDirectedPlugin module (not a
 * re-implementation) and pin BOTH directions: each assertion fails on the
 * unpatched tree and passes with the fix.
 *
 * Three independent sub-mechanisms of the consolidated D-049 defect are covered:
 *
 *  1. findLinkArray + findGraphContainer node-array aliases (recovery, HIGH,
 *     force-directed-w4-09): the node list lives under a vega-lite-style
 *     `data.values` and the links at the parsed root. Pre-fix findGraphContainer
 *     keyed ONLY on `obj.nodes`, so the graph was never found -> spec
 *     unclaimable -> 30s empty-DOM hang. `findLinkArray` did not exist pre-fix.
 *
 *  2. floorLinkOpacity (contrast/structural, force-directed-w3-12): a caller
 *     `style.linkOpacity` of 0.08 composited the edges to ~1.05-1.27:1 — the
 *     actual information (edges) vanished in BOTH themes while only arrowheads
 *     read. `floorLinkOpacity` did not exist pre-fix. Asserted in BOTH themes,
 *     and a legible low/normal opacity is left verbatim (no gratuitous override).
 *
 *  3. labelRightExtent (structural, HIGH, force-directed-w2-11/w4-06/w3-05/…):
 *     computeFitTransform was fed ONLY node-disc points; labels drawn rightward
 *     with no width term were omitted from the fit box, clipping long labels at
 *     the right edge and pulling the true content centre off the disc-only bbox.
 *     `labelRightExtent` did not exist pre-fix.
 */
import {
  resolveForceColors,
  resolveForceDirectedSpec,
  forceDirectedPlugin,
  findGraphContainer,
  findLinkArray,
  floorLinkOpacity,
  labelRightExtent,
  computeFitTransform,
} from '../forceDirectedPlugin';
import {
  contrastRatio,
  compositeOver,
  FORCE_DARK_BG,
  FORCE_LIGHT_BG,
} from '../forceDirectedPlugin';

const canHandle = (spec: any) => forceDirectedPlugin.canHandle(spec);

describe('D-049 — container node-array alias recovery (w4-09)', () => {
  const w4_09Definition = JSON.stringify({
    $schema: 'https://ziya.dev/schema/force-directed/v2.json',
    type: 'force-directed',
    layout: 'force',
    data: {
      values: [
        { id: 'api', group: 0 },
        { id: 'auth', group: 1 },
        { id: 'db', group: 2 },
        { id: 'cache', group: 2 },
        { id: 'queue', group: 1 },
        { id: 'worker', group: 0 },
      ],
    },
    encoding: { link: { field: 'links' } },
    links: [
      { source: 'api', target: 'auth' },
      { source: 'api', target: 'cache' },
      { source: 'auth', target: 'db' },
      { source: 'api', target: 'queue' },
      { source: 'queue', target: 'worker' },
      { source: 'worker', target: 'db' },
    ],
  });

  it('findGraphContainer finds a node array under data.values (aliased)', () => {
    const parsed = {
      data: { values: [{ id: 'a' }, { id: 'b' }] },
      links: [{ source: 'a', target: 'b' }],
    };
    const found = findGraphContainer(parsed);
    // Pre-fix: undefined (keyed only on `nodes`) -> RED here.
    expect(found).toBeDefined();
    expect(found!.nodes).toHaveLength(2);
  });

  it('findGraphContainer still finds `vertices` and prefers `nodes`', () => {
    expect(findGraphContainer({ vertices: [{ id: 'x' }] })!.nodes).toHaveLength(1);
    const both = { nodes: [{ id: 'n1' }], values: [{ id: 'v1' }, { id: 'v2' }] };
    expect(findGraphContainer(both)!.nodes[0].id).toBe('n1'); // `nodes` wins
  });

  it('findLinkArray reaches a links array in a sibling container', () => {
    const links = findLinkArray({ data: { values: [] }, links: [{ source: 'a', target: 'b' }] });
    expect(links).toHaveLength(1);
    expect(findLinkArray({ edges: [{ source: 'a', target: 'b' }] })).toHaveLength(1);
  });

  it('resolveForceDirectedSpec recovers nodes (data.values) AND links (root)', () => {
    const resolved = resolveForceDirectedSpec({ type: 'force-directed', definition: w4_09Definition });
    expect(Array.isArray(resolved.nodes)).toBe(true);
    expect(resolved.nodes).toHaveLength(6);
    // Links live at the parsed root, NOT in the node container — must not be dropped.
    expect(Array.isArray(resolved.links)).toBe(true);
    expect(resolved.links).toHaveLength(6);
  });

  it('canHandle claims the recovered w4-09 spec (no more 30s hang)', () => {
    // Pre-fix: findGraphContainer returns undefined -> spec unrecovered ->
    // canHandle false -> no plugin claims it -> empty-DOM timeout. RED here.
    expect(canHandle({ type: 'force-directed', definition: w4_09Definition })).toBe(true);
  });
});

describe('D-049 — link-opacity floor (w3-12), BOTH themes', () => {
  const themes: Array<{ name: string; isDark: boolean; bg: string }> = [
    { name: 'dark', isDark: true, bg: FORCE_DARK_BG },
    { name: 'light', isDark: false, bg: FORCE_LIGHT_BG },
  ];

  it('floorLinkOpacity raises an invisible 0.08 to clear 3:1, keeps 0.9 verbatim', () => {
    // dark canvas, nudged stroke = white; light canvas, nudged stroke = black.
    const rDark = floorLinkOpacity('#ffffff', FORCE_DARK_BG, 0.08);
    const rLight = floorLinkOpacity('#000000', FORCE_LIGHT_BG, 0.08);
    expect(rDark).toBeGreaterThan(0.08);
    expect(rLight).toBeGreaterThan(0.08);
    expect(contrastRatio(compositeOver('#ffffff', FORCE_DARK_BG, rDark), FORCE_DARK_BG)).toBeGreaterThanOrEqual(3);
    expect(contrastRatio(compositeOver('#000000', FORCE_LIGHT_BG, rLight), FORCE_LIGHT_BG)).toBeGreaterThanOrEqual(3);
    // A legible opacity is never overridden.
    expect(floorLinkOpacity('#ffffff', FORCE_DARK_BG, 0.9)).toBe(0.9);
    expect(floorLinkOpacity('#000000', FORCE_LIGHT_BG, 0.9)).toBe(0.9);
  });

  themes.forEach(({ name, isDark, bg }) => {
    it(`resolveForceColors floors a 0.08 caller opacity so edges read on the ${name} canvas`, () => {
      const c = resolveForceColors(isDark, { linkOpacity: 0.08 });
      // Pre-fix: linkOpacity === 0.08 verbatim -> composited edge ~1.05-1.27:1 -> RED.
      expect(c.linkOpacity).toBeGreaterThan(0.08);
      const ratio = contrastRatio(compositeOver(c.linkStroke, c.effectiveBg, c.linkOpacity), c.effectiveBg);
      expect(ratio).toBeGreaterThanOrEqual(3);
    });

    it(`resolveForceColors leaves a legible default opacity untouched on the ${name} canvas`, () => {
      const c = resolveForceColors(isDark, {});
      expect(c.linkOpacity).toBe(0.9); // default, already legible
    });
  });
});

describe('D-049 — label extent reserved in the fit box (w2-11/w4-06/…)', () => {
  it('labelRightExtent reserves room to the right of the disc for the label', () => {
    // r + gap + len*fontSize*0.6 ; e.g. 8 + 4 + 10*10*0.6 = 72
    expect(labelRightExtent(10, 8, 10)).toBeCloseTo(72, 5);
    // Always strictly past the disc rim so a label can never sit outside the box.
    expect(labelRightExtent(6, 8, 10)).toBeGreaterThan(8);
    // Degenerate inputs do not throw / go non-finite.
    expect(Number.isFinite(labelRightExtent(NaN as any, NaN as any, NaN as any))).toBe(true);
  });

  it('including the label extent keeps a long label inside the fitted viewport', () => {
    const width = 400, height = 300, r = 10;
    // Disc-only fit (the pre-fix behaviour) vs disc+label fit (the fix): the
    // label's on-screen right edge must land inside the frame once we reserve it.
    const ext = labelRightExtent(24, r, 10); // r + 4 + 24*10*0.6 = 158
    const withLabel = computeFitTransform(
      [{ x: 0, y: 0, r }, { x: ext, y: 0, r: 0 }],
      width,
      height,
    );
    const labelRightOnScreen = withLabel.k * ext + withLabel.x;
    expect(labelRightOnScreen).toBeLessThanOrEqual(width);
    expect(labelRightOnScreen).toBeGreaterThanOrEqual(0);
  });
});
