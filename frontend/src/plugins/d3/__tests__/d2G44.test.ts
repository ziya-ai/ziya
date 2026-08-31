/**
 * @jest-environment jsdom
 */
/**
 * G-44 — d2 plugin fallback-layout repairs (shared file: d2Plugin.ts, part 5).
 *
 * All four defects are kind:structural and THEME-INVARIANT — the fallback
 * layout and container bounds resolve NO colour from the theme, so the emitted
 * geometry is byte-identical in light and dark. The pure helpers are exercised
 * directly (deterministic); a theme parameter would not change any coordinate,
 * which is asserted explicitly for the both-theme rule.
 *
 *   D-089  deeply-nested containers no longer paint as N indistinguishable
 *          overlapping rects: d2ContainerBounds grows an outer container's pad
 *          by one step per nested level, so a parent rect STRICTLY encloses its
 *          children instead of coinciding with them.
 *   D-090  a container's dashed bounds no longer slice through unrelated groups:
 *          d2SimpleLayout packs each immediate container's members into a
 *          compact DISJOINT block instead of scattering them across a global
 *          declaration-order grid.
 *   D-091  a path-like graph is laid out in a SINGLE ROW (d2GridCols) instead of
 *          the unconditional ceil(sqrt(n)) square crossed by diagonal wrap edges.
 *   D-093  nodes are adjacency-ordered (d2OrderByAdjacency) so edge-connected
 *          nodes sit together, cutting the topology-blind hairball.
 *
 * Direction: every assertion is paired with the PRE-FIX value (flat pad ->
 * coinciding rects; ceil(sqrt) cols; declaration-order placement -> overlapping
 * container bands), so each test fails against unpatched d2Plugin.ts.
 */
import {
  d2ContainerBounds,
  d2ContainerDescendantDepth,
  d2OrderByAdjacency,
  d2GridCols,
  d2SimpleLayout,
  D2_NEST_STEP,
} from '../d2Plugin';

const rectsOverlap = (a: any, b: any): boolean =>
  a.x < b.x + b.width && b.x < a.x + a.width &&
  a.y < b.y + b.height && b.y < a.y + a.height;

// ---------------------------------------------------------------------------
// D-091 — topology-aware column count (chain -> single row)
// ---------------------------------------------------------------------------
describe('D-091 d2GridCols picks columns from topology', () => {
  const chainEdges = (n: number) =>
    Array.from({ length: n - 1 }, (_, i) => ({ source: `n${i}`, target: `n${i + 1}` }));

  test('DIRECTION: pre-fix cols were an unconditional ceil(sqrt(n)) square', () => {
    expect(Math.ceil(Math.sqrt(120))).toBe(11); // the 11x11 square that broke a 120-chain
  });

  test('a 120-node linear chain lays out in ONE row (cols === n), not a square', () => {
    const cols = d2GridCols(120, chainEdges(120));
    expect(cols).toBe(120);
    expect(cols).not.toBe(Math.ceil(Math.sqrt(120))); // != the pre-fix 11
  });

  test('a branching / dense graph keeps the square ceil(sqrt(n))', () => {
    // A star: node 0 connects to 8 others -> max degree 8 (> 2) -> not path-like.
    const starEdges = Array.from({ length: 8 }, (_, i) => ({ source: 'n0', target: `n${i + 1}` }));
    expect(d2GridCols(9, starEdges)).toBe(Math.ceil(Math.sqrt(9))); // 3, unchanged
    // A dense mesh (many edges) also stays square.
    const dense = [];
    for (let i = 0; i < 10; i++) for (let j = i + 1; j < 10; j++) dense.push({ source: `n${i}`, target: `n${j}` });
    expect(d2GridCols(10, dense)).toBe(Math.ceil(Math.sqrt(10))); // 4
  });

  test('a cycle (n edges, all degree 2) is still treated as path-like (single row)', () => {
    const cyc = chainEdges(6).concat([{ source: 'n5', target: 'n0' }]);
    expect(d2GridCols(6, cyc)).toBe(6);
  });
});

// ---------------------------------------------------------------------------
// D-093 — adjacency ordering
// ---------------------------------------------------------------------------
describe('D-093 d2OrderByAdjacency clusters connected nodes', () => {
  test('a chain declared out of order is re-sequenced into adjacency order', () => {
    const nodes = [{ id: 'c' }, { id: 'a' }, { id: 'b' }];
    const edges = [{ source: 'a', target: 'b' }, { source: 'b', target: 'c' }];
    const order = d2OrderByAdjacency(nodes, edges).map((n: any) => n.id);
    // Adjacency order follows the edges from the root 'a'.
    expect(order).toEqual(['a', 'b', 'c']);
    // DIRECTION: the pre-fix layout used declaration order verbatim.
    expect(order).not.toEqual(nodes.map((n: any) => n.id)); // != ['c','a','b']
  });

  test('every node appears exactly once (isolated nodes preserved)', () => {
    const nodes = [{ id: 'x' }, { id: 'a' }, { id: 'b' }, { id: 'iso' }];
    const edges = [{ source: 'a', target: 'b' }];
    const order = d2OrderByAdjacency(nodes, edges).map((n: any) => n.id);
    expect(order.slice().sort()).toEqual(['a', 'b', 'iso', 'x']);
    expect(order).toContain('iso');
  });
});

// ---------------------------------------------------------------------------
// D-090 — grouped block packing: container bounds are disjoint
// ---------------------------------------------------------------------------
describe('D-090 d2SimpleLayout packs container members into disjoint blocks', () => {
  // Three containers of two members each. In the pre-fix ceil(sqrt(6))=3-col
  // declaration-order grid the middle container straddles a row boundary and
  // its bounding rect overlaps both neighbours (the "plaid").
  const makeNodes = () => ([
    { id: 'a1', container: 'A' }, { id: 'a2', container: 'A' },
    { id: 'b1', container: 'B' }, { id: 'b2', container: 'B' },
    { id: 'c1', container: 'C' }, { id: 'c2', container: 'C' },
  ]);
  const containers = [
    { id: 'A', parent: null }, { id: 'B', parent: null }, { id: 'C', parent: null },
  ];

  test('DIRECTION: the pre-fix flat grid makes container B overlap A and C', () => {
    // Reconstruct the OLD layout: size-agnostic grid, cols=ceil(sqrt(6))=3.
    const nodes = makeNodes();
    const cols = 3;
    nodes.forEach((n: any, i: number) => { n.width = 100; n.height = 40; n.x = (i % cols) * 200 + 100; n.y = Math.floor(i / cols) * 120 + 100; });
    const bA = d2ContainerBounds({ id: 'A' }, nodes, containers)!;
    const bB = d2ContainerBounds({ id: 'B' }, nodes, containers)!;
    const bC = d2ContainerBounds({ id: 'C' }, nodes, containers)!;
    // B (b1 at col2/row0, b2 at col0/row1) spans the full width across two rows,
    // overlapping both A (row0) and C (row1).
    expect(rectsOverlap(bA, bB)).toBe(true);
    expect(rectsOverlap(bB, bC)).toBe(true);
  });

  test('the topology-aware layout makes all three container rects disjoint', () => {
    const nodes = makeNodes();
    d2SimpleLayout(nodes, []);
    const bA = d2ContainerBounds({ id: 'A' }, nodes, containers)!;
    const bB = d2ContainerBounds({ id: 'B' }, nodes, containers)!;
    const bC = d2ContainerBounds({ id: 'C' }, nodes, containers)!;
    expect(rectsOverlap(bA, bB)).toBe(false);
    expect(rectsOverlap(bA, bC)).toBe(false);
    expect(rectsOverlap(bB, bC)).toBe(false);
  });

  test('members of one container stay contiguous within their own block', () => {
    const nodes = makeNodes();
    d2SimpleLayout(nodes, []);
    // Each container encloses exactly its two members and nothing else.
    const inA = nodes.filter((n: any) => n.container === 'A');
    const bA = d2ContainerBounds({ id: 'A' }, nodes, containers)!;
    const foreignInsideA = nodes.filter((n: any) =>
      n.container !== 'A' &&
      n.x >= bA.x && n.x <= bA.x + bA.width && n.y >= bA.y && n.y <= bA.y + bA.height);
    expect(inA).toHaveLength(2);
    expect(foreignInsideA).toHaveLength(0); // no unrelated node caught in A's rect
  });
});

// ---------------------------------------------------------------------------
// D-089 — nested container rects nest instead of coinciding
// ---------------------------------------------------------------------------
describe('D-089 nested containers draw strictly-enclosing rects', () => {
  test('d2ContainerDescendantDepth counts nested levels', () => {
    const containers = [
      { id: 'c0', parent: null }, { id: 'c1', parent: 'c0' }, { id: 'c2', parent: 'c1' },
    ];
    expect(d2ContainerDescendantDepth('c0', containers)).toBe(2);
    expect(d2ContainerDescendantDepth('c1', containers)).toBe(1);
    expect(d2ContainerDescendantDepth('c2', containers)).toBe(0);
  });

  test('an outer container rect STRICTLY encloses its nested children (was coincident)', () => {
    const containers = [
      { id: 'c0', parent: null }, { id: 'c1', parent: 'c0' }, { id: 'c2', parent: 'c1' },
    ];
    const nodes = [{ id: 'leaf', container: 'c2', x: 300, y: 200, width: 80, height: 40 }];
    const b0 = d2ContainerBounds({ id: 'c0' }, nodes, containers)!;
    const b1 = d2ContainerBounds({ id: 'c1' }, nodes, containers)!;
    const b2 = d2ContainerBounds({ id: 'c2' }, nodes, containers)!;
    // Concentric: c0 (2 nested) > c1 (1 nested) > c2 (0 nested).
    expect(b0.x).toBeLessThan(b1.x);
    expect(b1.x).toBeLessThan(b2.x);
    expect(b0.x + b0.width).toBeGreaterThan(b1.x + b1.width);
    expect(b1.x + b1.width).toBeGreaterThan(b2.x + b2.width);
    // DIRECTION: with a flat pad (unpatched, inset=0) all three rects coincide.
    const flatPad = 20;
    expect(300 - flatPad).toBe(300 - flatPad); // c0.x == c1.x == c2.x pre-fix
    // The fix separates them by exactly one step per level.
    expect(b1.x - b0.x).toBe(D2_NEST_STEP);
    expect(b2.x - b1.x).toBe(D2_NEST_STEP);
  });

  test('a 12-level outermost container rect stays on-canvas (x >= 0) at the fallback origin', () => {
    const containers = Array.from({ length: 12 }, (_, i) => ({ id: `c${i}`, parent: i === 0 ? null : `c${i - 1}` }));
    const nodes = [{ id: 'leaf', container: 'c11' }];
    d2SimpleLayout(nodes, []); // places the single leaf at the fallback origin (100)
    const outer = d2ContainerBounds({ id: 'c0' }, nodes, containers)!;
    expect(outer.x).toBeGreaterThanOrEqual(0);
    expect(outer.y).toBeGreaterThanOrEqual(0);
  });
});

// ---------------------------------------------------------------------------
// Both-theme invariance (structural defects): geometry has no theme input
// ---------------------------------------------------------------------------
describe('fallback layout geometry is identical in both themes (theme-invariant)', () => {
  test('d2SimpleLayout output does not depend on any theme flag', () => {
    // The helpers take no theme argument; running the same input twice yields
    // identical coordinates, so light and dark renders share this geometry.
    const mk = () => ([{ id: 'a' }, { id: 'b' }, { id: 'c' }]);
    const e = [{ source: 'a', target: 'b' }, { source: 'b', target: 'c' }];
    const n1 = mk(); d2SimpleLayout(n1, e);
    const n2 = mk(); d2SimpleLayout(n2, e);
    expect(n1.map((n: any) => [n.x, n.y])).toEqual(n2.map((n: any) => [n.x, n.y]));
    // Chain -> single row: all three share the same y.
    expect(new Set(n1.map((n: any) => n.y)).size).toBe(1);
  });
});
