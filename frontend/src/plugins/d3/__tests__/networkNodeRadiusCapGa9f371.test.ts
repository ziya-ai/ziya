/**
 * Fix group G-76d0a4 (run a9f37149) — network coincident-node / perimeter-pileup
 * structural class, frontend/src/plugins/d3/networkDiagram.ts.
 *
 * Root cause confirmed against source: the layout stage
 * (fitNodePositionsToViewport / decollideCoincidentNodes / computeGridLayout)
 * only moves node CENTRES, but the render drew every circle at its authored
 * `size` (`d.size || 10`). On a dense or undersized canvas — network-w2-09 packs
 * 40 nodes onto an 80x60 viewBox — fitting the centres into the interior still
 * leaves each r=10 disc overlapping its neighbours into one opaque blob with the
 * edges and labels buried (D-440, and the <=80 dense half of D-179 / D-447).
 *
 * `effectiveNodeRadius(size, minGap)` caps the DRAWN radius at half the
 * nearest-neighbour centre gap so adjacent discs at worst touch. These
 * assertions FAIL against the pre-fix source: the helper did not exist and the
 * circle radius ignored the final spacing, so `min(size, minGap*0.5)` was never
 * applied and a crammed layout overlapped.
 */
import {
  effectiveNodeRadius,
  minNearestNeighborGap,
  fitNodePositionsToViewport,
  NETWORK_DEFAULT_NODE_SIZE,
} from '../networkDiagram';

describe('effectiveNodeRadius — cap drawn radius to half the nearest-neighbour gap', () => {
  it('shrinks radius so discs never overlap on a tight layout', () => {
    const minGap = 6; // centres only 6px apart
    // r=10 discs at 6px spacing overlap badly; cap must bring r to <= 3.
    expect(effectiveNodeRadius(10, minGap)).toBeCloseTo(3, 6);
    expect(effectiveNodeRadius(10, minGap)).toBeLessThanOrEqual(minGap / 2 + 1e-9);
  });

  it('never enlarges a node and leaves a well-spaced graph unchanged', () => {
    // minGap >> 2*size: comfortably spaced, radius must be identical.
    expect(effectiveNodeRadius(10, 200)).toBe(10);
    expect(effectiveNodeRadius(8, 100)).toBe(8);
  });

  it('applies a small floor so a node never vanishes entirely', () => {
    const r = effectiveNodeRadius(10, 0.4);
    expect(r).toBeGreaterThan(0);
    expect(r).toBeGreaterThanOrEqual(1.5 - 1e-9);
  });

  it('falls back to the authored/default size when spacing is unknown', () => {
    expect(effectiveNodeRadius(10, Infinity)).toBe(10);
    expect(effectiveNodeRadius(undefined as any, 5)).toBeLessThanOrEqual(NETWORK_DEFAULT_NODE_SIZE);
    expect(effectiveNodeRadius(0, Infinity)).toBe(NETWORK_DEFAULT_NODE_SIZE);
  });

  it('network-w2-09 pipeline: fitted 40-node layout on 80x60 draws non-overlapping discs', () => {
    // Reproduce the post-layout crammed geometry: 40 nodes ejected by the force
    // sim then pulled back by fitNodePositionsToViewport into an 80x60 canvas.
    const W = 80, H = 60;
    const nodes = Array.from({ length: 40 }, (_, i) => ({
      id: i,
      // spread far off-canvas the way forceManyBody(-200)+collide(r=14) would
      x: (i % 8) * 300 - 1200,
      y: Math.floor(i / 8) * 300 - 750,
      size: 10,
    }));
    fitNodePositionsToViewport(nodes, W, H);
    const minGap = minNearestNeighborGap(nodes);
    // Every drawn disc pair must be separable: sum of capped radii <= centre gap.
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const gap = Math.hypot(nodes[i].x - nodes[j].x, nodes[i].y - nodes[j].y);
        const ri = effectiveNodeRadius(nodes[i].size, minGap);
        const rj = effectiveNodeRadius(nodes[j].size, minGap);
        expect(ri + rj).toBeLessThanOrEqual(gap + 1e-6);
      }
    }
    // And the drawn radius genuinely shrank below the authored size (proves the
    // cap engaged — the pre-fix render would have kept r=10 here).
    expect(effectiveNodeRadius(10, minGap)).toBeLessThan(10);
  });
});
