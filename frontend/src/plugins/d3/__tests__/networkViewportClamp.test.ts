/**
 * Regression test for Issue 31 (network renderer): SILENT DATA LOSS from nodes
 * ejected outside the SVG viewport.
 *
 * ANOMALY: an adversarial network spec with a huge hub `size:1e12`, several
 * dangling ghost edges, and isolated/tree nodes rendered with catastrophic
 * silent data loss — `tree_child_b`, all three `isolated_no_edges_*`, and the
 * leaf/dup/orphan nodes were absent from the canvas. Root cause: the force
 * layout applies `forceManyBody(-200)` repulsion with no bounding force, so any
 * node with NO surviving link (its only edges were dangling refs dropped by
 * `sanitizeNetworkGraph`, or it was declared isolated) is repelled far outside
 * `[0,w]x[0,h]` with nothing to pull it back, and is then silently CLIPPED by
 * the SVG viewBox. Separately, a large clamped hub whose centre sits at y <
 * radius has its top clipped off the canvas edge.
 *
 * FIX: `clampNodePositionsToViewport(nodes,w,h)` pins every node's centre so its
 * full radius stays inside the viewport, applied AFTER the simulation and
 * BEFORE drawing. Fixes the whole class: connected, disconnected, and
 * NaN/Infinity-ejected nodes.
 *
 * Imports the REAL shipped module; would FAIL against pre-fix source (which had
 * no `clampNodePositionsToViewport` export at all).
 */
import {
  clampNodePositionsToViewport,
  NETWORK_DEFAULT_NODE_SIZE,
} from '../networkDiagram';

describe('Issue 31 — clampNodePositionsToViewport (no node clipped off-canvas)', () => {
  const W = 600;
  const H = 400;

  it('pulls an ejected disconnected node (far off-canvas) back inside the viewport', () => {
    // A node repelled to (99999, -50000) — the exact anomaly (isolated node
    // ejected by forceManyBody with no link to anchor it).
    const nodes = [{ id: 'ejected', size: 8, x: 99999, y: -50000 }];
    clampNodePositionsToViewport(nodes, W, H);
    const n = nodes[0];
    // Its full circle (r=8) must be inside [0,600]x[0,400].
    expect(n.x - 8).toBeGreaterThanOrEqual(0);
    expect(n.x + 8).toBeLessThanOrEqual(W);
    expect(n.y - 8).toBeGreaterThanOrEqual(0);
    expect(n.y + 8).toBeLessThanOrEqual(H);
  });

  it('keeps a large hub fully on-canvas (top edge no longer clipped)', () => {
    // Hub with a canvas-relative radius (60) whose centre the simulation left
    // near the top edge (y=10 < radius) — pre-fix this clipped the top.
    const nodes = [{ id: 'hub', size: 60, x: 300, y: 10 }];
    clampNodePositionsToViewport(nodes, W, H);
    const n = nodes[0];
    expect(n.y - 60).toBeGreaterThanOrEqual(0); // top edge fully visible
    expect(n.y + 60).toBeLessThanOrEqual(H);
    expect(n.x - 60).toBeGreaterThanOrEqual(0);
    expect(n.x + 60).toBeLessThanOrEqual(W);
  });

  it('recenters a node with non-finite coordinates (NaN/Infinity from a poisoned tick)', () => {
    const nodes = [
      { id: 'nan', size: 8, x: NaN, y: 100 },
      { id: 'inf', size: 8, x: Infinity, y: -Infinity },
    ];
    clampNodePositionsToViewport(nodes, W, H);
    for (const n of nodes) {
      expect(Number.isFinite(n.x)).toBe(true);
      expect(Number.isFinite(n.y)).toBe(true);
      expect(n.x).toBeGreaterThanOrEqual(0);
      expect(n.x).toBeLessThanOrEqual(W);
      expect(n.y).toBeGreaterThanOrEqual(0);
      expect(n.y).toBeLessThanOrEqual(H);
    }
  });

  it('uses the default radius when size is missing/degenerate', () => {
    const nodes = [
      { id: 'nosize', x: -100, y: -100 },
      { id: 'zero', size: 0, x: -100, y: -100 },
    ];
    clampNodePositionsToViewport(nodes, W, H);
    for (const n of nodes) {
      // Clamped so a default-radius circle fits.
      expect(n.x).toBeGreaterThanOrEqual(NETWORK_DEFAULT_NODE_SIZE);
      expect(n.y).toBeGreaterThanOrEqual(NETWORK_DEFAULT_NODE_SIZE);
    }
  });

  it('every node in a mixed graph (ejected + on-screen + off-screen) is left visible', () => {
    // Mirrors the anomaly: one clustered node in-bounds, the rest ejected.
    const nodes = [
      { id: 'in', size: 8, x: 300, y: 200 },
      { id: 'iso1', size: 8, x: 1e6, y: 1e6 },
      { id: 'iso2', size: 8, x: -1e6, y: 5e5 },
      { id: 'child_b', size: 8, x: 800, y: 800 },
      { id: 'leaf', size: 8, x: -20, y: 200 },
    ];
    clampNodePositionsToViewport(nodes, W, H);
    for (const n of nodes) {
      expect(n.x - 8).toBeGreaterThanOrEqual(0);
      expect(n.x + 8).toBeLessThanOrEqual(W);
      expect(n.y - 8).toBeGreaterThanOrEqual(0);
      expect(n.y + 8).toBeLessThanOrEqual(H);
    }
    // None were dropped.
    expect(nodes).toHaveLength(5);
  });

  // GUARD: a node already inside the viewport must NOT be moved (not a catch-all
  // that recentres everything).
  it('leaves an already-in-bounds node untouched', () => {
    const nodes = [{ id: 'ok', size: 10, x: 250, y: 180 }];
    clampNodePositionsToViewport(nodes, W, H);
    expect(nodes[0].x).toBe(250);
    expect(nodes[0].y).toBe(180);
  });

  it('a node larger than the canvas keeps its centre inside (partially visible, not gone)', () => {
    // Radius bigger than half the canvas cannot fully fit; the centre must still
    // be inside so it is centrally/partially visible rather than off-screen.
    const nodes = [{ id: 'giant', size: 5000, x: 99999, y: 99999 }];
    clampNodePositionsToViewport(nodes, W, H);
    expect(nodes[0].x).toBeGreaterThanOrEqual(0);
    expect(nodes[0].x).toBeLessThanOrEqual(W);
    expect(nodes[0].y).toBeGreaterThanOrEqual(0);
    expect(nodes[0].y).toBeLessThanOrEqual(H);
  });

  it('falls back to a 600x400 canvas for degenerate width/height', () => {
    const nodes = [{ id: 'a', size: 8, x: 1e6, y: 1e6 }];
    clampNodePositionsToViewport(nodes, 0, -5);
    expect(nodes[0].x).toBeLessThanOrEqual(600);
    expect(nodes[0].y).toBeLessThanOrEqual(400);
  });

  it('tolerates non-array / non-object entries without throwing', () => {
    expect(clampNodePositionsToViewport(undefined as any, W, H)).toEqual([]);
    const mixed = [null, 42, { id: 'a', size: 8, x: -99, y: -99 }] as any[];
    clampNodePositionsToViewport(mixed, W, H);
    expect(mixed[2].x).toBeGreaterThanOrEqual(8);
    expect(mixed[2].y).toBeGreaterThanOrEqual(8);
  });
});
