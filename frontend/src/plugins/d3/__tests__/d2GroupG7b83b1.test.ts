/**
 * G-7b83b1 — d2Plugin.ts wave-1/wave-4 recovery (shared file: d2Plugin.ts).
 *
 * This group's six backlog defects (D-059 theme, D-060/D-061/D-063/D-064/D-066
 * structural) all trace to the SAME file, frontend/src/plugins/d3/d2Plugin.ts.
 * Their fixes are already resident in source; this file is the group-level guard
 * that pins each backlog id to the concrete code path that repairs it, so a
 * future edit to d2Plugin.ts that regresses any of them fails here.
 *
 * DIRECTION (fails against unpatched HEAD): every symbol imported below —
 * trimEdgeToNodes, d2ContainerBounds, buildElkNodeLabels, d2ReadableTextOn,
 * D2_DARK_BG/D2_LIGHT_BG — was introduced by these fixes and does not exist in
 * the pre-fix d2Plugin.ts, so the import fails to compile against HEAD. Each
 * test additionally documents the pre-fix mangling it prevents.
 *
 * THEME CONTRACT (D-059): the contrast assertions are PAIRED across both themes
 * — the previously-broken DARK direction is asserted fixed AND the LIGHT
 * direction is asserted still-correct — so the fix cannot be a swap-one-constant
 * that repairs dark by breaking light.
 */
import {
  D2Parser,
  trimEdgeToNodes,
  d2ContainerBounds,
  buildElkNodeLabels,
  d2ReadableTextOn,
  D2_DARK_BG,
  D2_LIGHT_BG,
} from '../d2Plugin';
import { contrastRatio } from '../chartTheme';

const parse = (def: string) => new D2Parser().parse(def);
const DARK_TEXT = '#ffffff'; // d2ThemeColors(true).text
const LIGHT_TEXT = '#000000'; // d2ThemeColors(false).text

// ---------------------------------------------------------------------------
// D-059 (theme, high) — recovered-fill-unconditional-theme-text-illegible:dark
//   nodeTextFill returned the theme text constant (#ffffff in dark) for any
//   node without an explicit font-color, so white drowned on the light-ish
//   fills the w4 specs (d2-w4-03/07/08/09) emit. The fix chooses #000/#fff by
//   WCAG contrast against the RESOLVED fill (d2ReadableTextOn).
// ---------------------------------------------------------------------------
describe('D-059 node label colour is chosen by contrast against the resolved fill', () => {
  // The fills triage measured white-on-fill below the 4.5 floor in dark. Use
  // HEX values (contrastRatio() in chartTheme parses hex, not CSS names).
  const w4Fills: Array<[string, string]> = [
    ['papayawhip', '#ffefd5'],
    ['cyan', '#00ffff'],
    ['cornflowerblue', '#6495ed'],
    ['magenta', '#ff00ff'],
    ['dodgerish', '#4287f5'],
    ['steelblue', '#4682b4'],
  ];

  test.each(w4Fills)('%s fill: DARK label flips off the illegible white and clears 4.5:1', (_name, hex) => {
    const dark = d2ReadableTextOn(hex, D2_DARK_BG, DARK_TEXT);
    // pre-fix dark result was the theme white; prove it was below the floor.
    expect(contrastRatio('#ffffff', hex)).toBeLessThan(4.5);
    // post-fix chosen colour clears the WCAG text floor on the same fill.
    expect(contrastRatio(dark, hex)).toBeGreaterThanOrEqual(4.5);
  });

  test.each(w4Fills)('%s fill: LIGHT label stays legible (fix is not a dark-only swap)', (_name, hex) => {
    const light = d2ReadableTextOn(hex, D2_LIGHT_BG, LIGHT_TEXT);
    expect(contrastRatio(light, hex)).toBeGreaterThanOrEqual(4.5);
  });

  test('unstyled theme fills keep the theme text constant (byte-identical)', () => {
    expect(d2ReadableTextOn('#303f9f', D2_DARK_BG, DARK_TEXT)).toBe('#ffffff');
    expect(d2ReadableTextOn('#e3f2fd', D2_LIGHT_BG, LIGHT_TEXT)).toBe('#000000');
  });
});

// ---------------------------------------------------------------------------
// D-060 (structural) — arrowheads-hidden-under-node
//   Edges ran centre-to-centre with the marker inside the target box (hidden),
//   and marker gating ignored direction. Fix: trimEdgeToNodes pushes the head
//   OUTSIDE the box; markers are gated on reversed/bidirectional; ELK labels
//   no longer carry the throwing labelManager option.
// ---------------------------------------------------------------------------
describe('D-060 arrowhead is placed outside the target box, markers gated by direction', () => {
  const source = { id: 's', x: 0, y: 0, width: 80, height: 40 };
  const target = { id: 't', x: 200, y: 0, width: 80, height: 40 };

  test('the edge terminates clear of the target rectangle (not at its centre)', () => {
    const g = trimEdgeToNodes(source as any, target as any, 6);
    const targetCentreX = target.x + target.width / 2; // 240
    const targetLeftEdge = target.x; // 200
    // pre-fix x2 was the target centre (240), buried under the node; the head
    // must now sit at/left of the box's left edge + gap, i.e. well before 240.
    expect(g.x2).toBeLessThan(targetCentreX);
    expect(g.x2).toBeLessThanOrEqual(targetLeftEdge + 1);
  });

  test('buildElkNodeLabels emits a bare {text} label with no throwing layoutOptions', () => {
    const labels = buildElkNodeLabels({ label: 'API' });
    expect(labels).toEqual([{ text: 'API' }]);
    expect((labels[0] as any).layoutOptions).toBeUndefined();
    expect(buildElkNodeLabels({})).toEqual([]);
  });

  test('parse records direction flags the renderer gates markers on', () => {
    const { edges } = parse('a -> b\nc <- d\ne <-> f');
    expect(edges.find((x: any) => x.source === 'a')).toMatchObject({ reversed: false, bidirectional: false });
    expect(edges.find((x: any) => x.source === 'c')).toMatchObject({ reversed: true, bidirectional: false });
    expect(edges.find((x: any) => x.source === 'e')).toMatchObject({ bidirectional: true });
  });
});

// ---------------------------------------------------------------------------
// D-061 (structural) — edge-label-parsed-as-node
//   `a -> b: x` created a phantom node named "b: x" and no edge label. Fix:
//   split on the connector and strip the ": label" suffix into the edge label.
// ---------------------------------------------------------------------------
describe('D-061 `a -> b: x` yields 2 nodes + a labelled edge, not a "b: x" node', () => {
  test('the colon label lands on the edge, endpoints stay clean', () => {
    const { nodes, edges } = parse('a -> b: x');
    expect(nodes.map((n: any) => n.id).sort()).toEqual(['a', 'b']);
    expect(nodes.some((n: any) => /:/.test(n.id) || /:/.test(n.label))).toBe(false);
    expect(edges).toHaveLength(1);
    expect(edges[0].label).toBe('x');
  });
});

// ---------------------------------------------------------------------------
// D-063 (structural) — nested-container-infinity-rect
//   Empty children[] made Math.min/max yield Infinity, emitting hard SVG
//   errors. Fix: a container with no laid-out member returns null (skipped).
// ---------------------------------------------------------------------------
describe('D-063 a container with no laid-out member yields null, never an Infinity rect', () => {
  test('empty container -> null; populated container -> finite bounds', () => {
    const containers = [{ id: 'empty', parent: null }, { id: 'full', parent: null }];
    const node = { id: 'n', x: 100, y: 100, width: 80, height: 40, container: 'full' };
    expect(d2ContainerBounds({ id: 'empty' } as any, [node] as any, containers as any)).toBeNull();
    const b = d2ContainerBounds({ id: 'full' } as any, [node] as any, containers as any)!;
    expect(b).not.toBeNull();
    for (const v of [b.x, b.y, b.width, b.height]) expect(Number.isFinite(v)).toBe(true);
    expect(b.width).toBeGreaterThan(0);
    expect(b.height).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// D-064 (structural) — container-unlabeled-overbounded-and-plaid-at-scale
//   Containers were never labelled and their bounds ignored grouping. Fix:
//   bounds walk the membership chain; nested containers get a per-level inset so
//   an outer rect strictly encloses its inner rect (no coincident plaid).
// ---------------------------------------------------------------------------
describe('D-064 nested container bounds are grouping-aware and strictly enclosing', () => {
  test('outer container rect strictly encloses the inner one sharing a leaf', () => {
    const containers = [
      { id: 'outer', parent: null },
      { id: 'inner', parent: 'outer' },
    ];
    const leaf = { id: 'leaf', x: 120, y: 120, width: 80, height: 40, container: 'inner' };
    const inner = d2ContainerBounds(containers[1] as any, [leaf] as any, containers as any)!;
    const outer = d2ContainerBounds(containers[0] as any, [leaf] as any, containers as any)!;
    // Outer must be strictly larger on every side, not coincident (the plaid bug).
    expect(outer.x).toBeLessThan(inner.x);
    expect(outer.y).toBeLessThan(inner.y);
    expect(outer.x + outer.width).toBeGreaterThan(inner.x + inner.width);
    expect(outer.y + outer.height).toBeGreaterThan(inner.y + inner.height);
  });

  test('a container carries a label the renderer draws (label || id, never anonymous)', () => {
    // A plain `X {` (no colon) is a container; `X: Label {` is a table-node body.
    const { containers } = parse('grp {\n  a: A\n}');
    const grp = containers.find((c: any) => c.id === 'grp');
    expect(grp).toBeDefined();
    // The render draws `container.label || container.id`, so the label is never
    // empty — pre-fix containers emitted no <text> at all (anonymous groups).
    expect(grp.label || grp.id).toBe('grp');
  });
});

// ---------------------------------------------------------------------------
// D-066 (structural) — styling-parsed-as-nodes
//   `x.style.fill: v` / nested `style { }` fell through to node parsing and
//   became a box labelled with the hex string. Fix: styles are parsed onto
//   node.style; nodeFill reads them.
// ---------------------------------------------------------------------------
describe('D-066 style directives land on node.style, not phantom hex-labelled boxes', () => {
  test('inline and dotted style both apply, no colour node survives', () => {
    const { nodes } = parse('x: X {fill: blue}\ny.style.fill: red');
    expect(nodes.find((n: any) => n.id === 'x').style.fill).toBe('blue');
    expect(nodes.find((n: any) => n.id === 'y').style.fill).toBe('red');
    // no phantom node whose label is a bare colour value
    expect(nodes.some((n: any) => /^(blue|red|#[0-9a-f]{3,6})$/i.test(n.label))).toBe(false);
  });
});
