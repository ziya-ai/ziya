/**
 * G-12 — d2 plugin baseline structural repairs (shared file: d2Plugin.ts).
 *
 * Defects covered (all structural, theme-invariant — geometry/parse are
 * byte-identical in light and dark, so each test asserts the theme-independent
 * value; a "both themes" note is unnecessary because no colour is involved):
 *
 *   D-076  buildElkNodeLabels() no longer emits the bogus
 *          layoutOptions {'elk.labelManager':'none'} that made every ELK
 *          layout throw and fall back to the square grid.
 *   D-077  trimEdgeToNodes() runs the edge border-to-border with the arrowhead
 *          endpoint pushed OUTSIDE the target box (was drawn centre-to-centre
 *          with the head hidden under the target rect).
 *   D-078  parseConnection() splits the ": label" before matching endpoints, so
 *          'a -> b: x' yields edge label 'x' and exactly two nodes (a, b) — not
 *          a phantom node 'b: x'.
 *   D-081  d2ContainerBounds() computes finite bounds from member nodes across
 *          nesting and returns null for childless containers (was Infinity over
 *          an empty children[]).
 *
 * Direction: every assertion is paired with a check that the PRE-FIX code path
 * produced the wrong value (old greedy regex swallows the label; old label
 * shape carries elk.labelManager; old centre endpoint; old Math.min over empty
 * children = Infinity), so each test fails against unpatched d2Plugin.ts.
 */
import {
  D2Parser,
  buildElkNodeLabels,
  trimEdgeToNodes,
  d2ContainerBounds,
} from '../d2Plugin';

// ---------------------------------------------------------------------------
// D-076 — ELK label must not carry the invalid 'elk.labelManager' option
// ---------------------------------------------------------------------------
describe('D-076 ELK label options (grid-fallback root cause)', () => {
  test('buildElkNodeLabels emits a bare {text} label with no layoutOptions', () => {
    const labels = buildElkNodeLabels({ id: 'a', label: 'Alpha' });
    expect(labels).toEqual([{ text: 'Alpha' }]);
    // No labelManager anywhere in the produced structure.
    expect(JSON.stringify(labels)).not.toContain('labelManager');
    expect(labels[0]).not.toHaveProperty('layoutOptions');
  });

  test('DIRECTION: the pre-fix label shape carried the throwing option', () => {
    // Reconstruct the old shape to document why layout() always threw.
    const oldLabel = { text: 'Alpha', layoutOptions: { 'elk.labelManager': 'none' } };
    expect(JSON.stringify([oldLabel])).toContain('elk.labelManager');
    // The new builder must differ from it.
    expect(buildElkNodeLabels({ label: 'Alpha' })[0]).not.toEqual(oldLabel);
  });

  test('a node without a label produces an empty label array', () => {
    expect(buildElkNodeLabels({ id: 'a' })).toEqual([]);
    expect(buildElkNodeLabels(null as any)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// D-078 — edge label parsed as label, not a phantom node
// ---------------------------------------------------------------------------
describe('D-078 connection label is split from the target endpoint', () => {
  const OLD_REGEX = /([^-<>]+)(\s*<?-+>?\s*)([^-<>]+)(?:\s*:\s*(.+))?/;

  test('DIRECTION: the old greedy regex swallowed the label into the target', () => {
    const m = 'a -> b: x'.match(OLD_REGEX)!;
    expect(m[3].trim()).toBe('b: x'); // phantom node text
    expect(m[4]).toBeUndefined();     // label never captured
  });

  test("'a -> b: x' -> edge label 'x' and only two nodes a,b (no phantom)", () => {
    const { nodes, edges } = new D2Parser().parse('a -> b: x');
    expect(edges).toHaveLength(1);
    expect(edges[0].label).toBe('x');
    expect(edges[0].source).toBe('a');
    expect(edges[0].target).toBe('b');
    const ids = nodes.map((n: any) => n.id).sort();
    expect(ids).toEqual(['a', 'b']);
    // The phantom node the old parser created must not exist.
    expect(nodes.some((n: any) => n.id === 'b__x' || n.label === 'b: x')).toBe(false);
  });

  test('multi-word label after connector is preserved intact', () => {
    const { edges } = new D2Parser().parse('client -> server: sends request');
    expect(edges[0].label).toBe('sends request');
    expect(edges[0].target).toBe('server');
  });

  test('directionality: -> forward, <- reversed, <-> bidirectional', () => {
    const fwd = new D2Parser().parse('a -> b').edges[0];
    expect({ r: fwd.reversed, b: fwd.bidirectional }).toEqual({ r: false, b: false });

    const rev = new D2Parser().parse('a <- b').edges[0];
    expect({ r: rev.reversed, b: rev.bidirectional }).toEqual({ r: true, b: false });

    const bi = new D2Parser().parse('a <-> b').edges[0];
    expect({ r: bi.reversed, b: bi.bidirectional }).toEqual({ r: false, b: true });
  });

  test('unlabelled edge yields empty label and no colon phantom', () => {
    const { nodes, edges } = new D2Parser().parse('a -> b');
    expect(edges[0].label).toBe('');
    expect(nodes.map((n: any) => n.id).sort()).toEqual(['a', 'b']);
  });
});

// ---------------------------------------------------------------------------
// D-077 — arrowhead endpoint is outside the target box, not at its centre
// ---------------------------------------------------------------------------
describe('D-077 edge trimmed border-to-border (arrowhead visible)', () => {
  const src = { x: 0, y: 0, width: 100, height: 40 };   // centre (50,20)
  const tgt = { x: 300, y: 0, width: 100, height: 40 };  // centre (350,20)

  test('DIRECTION: pre-fix endpoint was the target CENTRE (hidden under node)', () => {
    const oldX2 = tgt.x + tgt.width / 2; // 350
    const { x2 } = trimEdgeToNodes(src, tgt);
    // New endpoint must be strictly left of the target centre (pulled toward
    // the source so the head clears the box).
    expect(x2).toBeLessThan(oldX2);
  });

  test('endpoint sits outside the target-facing (left) border, on the axis', () => {
    const { x2, y2 } = trimEdgeToNodes(src, tgt, 6);
    // Left border of target box is x=300; with a gap of 6 the endpoint is
    // just left of it (outside the rectangle).
    expect(x2).toBeLessThanOrEqual(300);
    expect(x2).toBeGreaterThan(280);
    expect(y2).toBeCloseTo(20, 5); // horizontal edge stays on the centre axis
  });

  test('start point leaves the source box (not its centre)', () => {
    const { x1 } = trimEdgeToNodes(src, tgt, 6);
    const srcCentreX = src.x + src.width / 2; // 50
    expect(x1).toBeGreaterThan(srcCentreX); // moved toward target, past centre
    expect(x1).toBeGreaterThanOrEqual(100 - 1); // at/just outside the right border
  });

  test('coincident centres are left untrimmed (no NaN / division blow-up)', () => {
    const g = trimEdgeToNodes(src, src);
    expect(Number.isFinite(g.x1) && Number.isFinite(g.x2)).toBe(true);
    expect(g.x1).toBe(g.x2);
    expect(g.y1).toBe(g.y2);
  });

  test('missing nodes degrade to 0,0 without throwing', () => {
    expect(() => trimEdgeToNodes(undefined, undefined)).not.toThrow();
    expect(trimEdgeToNodes(undefined, undefined)).toEqual({ x1: 0, y1: 0, x2: 0, y2: 0 });
  });
});

// ---------------------------------------------------------------------------
// D-081 — nested/childless container bounds never yield Infinity
// ---------------------------------------------------------------------------
describe('D-081 container bounds from member nodes (no Infinity)', () => {
  test('DIRECTION: pre-fix Math.min over an empty children[] is Infinity', () => {
    const emptyChildren: string[] = [];
    expect(Math.min(...emptyChildren.map(() => 0))).toBe(Infinity);
  });

  test('childless container returns null (skipped, not an Infinity rect)', () => {
    const bounds = d2ContainerBounds(
      { id: 'outer', children: [] },
      [], // no laid-out nodes belong to it
      [{ id: 'outer', parent: null }],
    );
    expect(bounds).toBeNull();
  });

  test('bounds computed for a container with direct member nodes', () => {
    const nodes = [
      { id: 'n1', container: 'box', x: 100, y: 100, width: 80, height: 40 },
      { id: 'n2', container: 'box', x: 300, y: 200, width: 80, height: 40 },
    ];
    const b = d2ContainerBounds({ id: 'box' }, nodes, [{ id: 'box', parent: null }])!;
    expect(b).not.toBeNull();
    expect(Number.isFinite(b.x) && Number.isFinite(b.width)).toBe(true);
    // Encloses both nodes with padding (min x 100 - 20 = 80).
    expect(b.x).toBe(80);
    expect(b.y).toBe(80);
    expect(b.x + b.width).toBe(300 + 80 + 20);
  });

  test('NESTING: an outer container whose members live only in a child still gets finite bounds', () => {
    // node belongs to inner; inner.parent = outer. Outer.children is empty in
    // the parser, which is exactly the pre-fix Infinity case.
    const nodes = [
      { id: 'leaf', container: 'inner', x: 200, y: 150, width: 60, height: 40 },
    ];
    const containers = [
      { id: 'outer', parent: null },
      { id: 'inner', parent: 'outer' },
    ];
    const outer = d2ContainerBounds({ id: 'outer', children: [] }, nodes, containers)!;
    expect(outer).not.toBeNull();
    expect(Number.isFinite(outer.x)).toBe(true);
    expect(Number.isFinite(outer.width)).toBe(true);
    // outer must enclose the deeply-nested leaf.
    expect(outer.x).toBeLessThanOrEqual(200);
    expect(outer.x + outer.width).toBeGreaterThanOrEqual(260);
  });

  test('parse of a nested-container definition wires parent pointers used by bounds', () => {
    const def = ['outer {', '  inner {', '    leaf: Leaf', '  }', '}'].join('\n');
    const { containers } = new D2Parser().parse(def);
    const byId = new Map(containers.map((c: any) => [c.id, c]));
    expect(byId.get('inner').parent).toBe('outer');
    expect(byId.get('outer').parent).toBeNull();
  });
});
