/**
 * Fix group G-8b89de — network layout/label structural defects, all in
 * frontend/src/plugins/d3/networkDiagram.ts. Each block below fails against the
 * pre-fix source (the helper it imports did not exist, and the render used the
 * un-clamped/over-boosted geometry these helpers replace).
 *
 * D-440 fitNodePositionsToViewport  — dense/undersized force graph ejected then
 *                                     perimeter-piled -> scale-to-fit into view.
 * D-441 labelHaloWidth              — wide extreme (w2-07): boosted-font halos
 *                                     tile into a solid band erasing topology.
 * D-443 nodeLabelDy                 — label clipped above the viewBox top when a
 *                                     node sits near y=0 -> drop it below.
 * D-445 groupCaptionY               — group caption overprints top member label
 *                                     -> caption above the rect.
 * D-446 capFontToNodeSpacing        — tall extreme (w2-08): boosted font taller
 *                                     than node pitch mashes labels.
 * D-447 decollideCoincidentNodes    — coincident authored coords (w3-05) drawn
 *                                     on top of each other -> spread apart.
 */
import {
  fitNodePositionsToViewport,
  labelHaloWidth,
  nodeLabelDy,
  groupCaptionY,
  capFontToNodeSpacing,
  minNearestNeighborGap,
  decollideCoincidentNodes,
  NETWORK_DEFAULT_NODE_SIZE,
} from '../networkDiagram';

describe('D-447 decollideCoincidentNodes — spread coincident authored coords', () => {
  it('separates five nodes authored at the same point (network-w3-05)', () => {
    const nodes = Array.from({ length: 5 }, (_, i) => ({ id: `p${i}`, x: 180, y: 120 }));
    decollideCoincidentNodes(nodes, 600, 320, 24);
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const d = Math.hypot(nodes[i].x - nodes[j].x, nodes[i].y - nodes[j].y);
        expect(d).toBeGreaterThanOrEqual(24 - 1e-6);
      }
    }
    // stays near the authored cluster
    for (const n of nodes) {
      expect(Math.hypot(n.x - 180, n.y - 120)).toBeLessThan(200);
    }
  });

  it('leaves already-separated nodes essentially where they were', () => {
    const nodes = [{ id: 'a', x: 100, y: 100 }, { id: 'b', x: 300, y: 100 }];
    decollideCoincidentNodes(nodes, 600, 320, 24);
    expect(nodes[0]).toMatchObject({ x: 100, y: 100 });
    expect(nodes[1]).toMatchObject({ x: 300, y: 100 });
  });
});

describe('D-440 fitNodePositionsToViewport — pull an ejected layout back in', () => {
  it('scales an off-canvas over-large layout to fit inside the viewport', () => {
    const W = 700, H = 400;
    // Force output ejected far off-canvas (a huge bbox), the D-440 pileup input.
    const nodes = [
      { id: 0, x: -900, y: -500, size: 8 },
      { id: 1, x: 1600, y: 900, size: 8 },
      { id: 2, x: 350, y: 200, size: 8 },
    ];
    fitNodePositionsToViewport(nodes, W, H);
    for (const n of nodes) {
      expect(n.x).toBeGreaterThanOrEqual(0);
      expect(n.x).toBeLessThanOrEqual(W);
      expect(n.y).toBeGreaterThanOrEqual(0);
      expect(n.y).toBeLessThanOrEqual(H);
    }
  });

  it('is a no-op for a layout that already fits inside the viewport', () => {
    const nodes = [
      { id: 0, x: 100, y: 100, size: 8 },
      { id: 1, x: 300, y: 200, size: 8 },
      { id: 2, x: 500, y: 300, size: 8 },
    ];
    const before = nodes.map(n => ({ x: n.x, y: n.y }));
    fitNodePositionsToViewport(nodes, 700, 400);
    nodes.forEach((n, i) => {
      expect(n.x).toBeCloseTo(before[i].x, 6);
      expect(n.y).toBeCloseTo(before[i].y, 6);
    });
  });
});

describe('D-443 nodeLabelDy — never clip a label above the viewBox top', () => {
  const FONT = 14;
  it('keeps a comfortably-placed label above the node (unchanged behaviour)', () => {
    // Node with ample headroom: label stays above at -(size)-5.
    const dy = nodeLabelDy({ y: 300, size: 10 }, FONT);
    expect(dy).toBe(-(10) - 5);
  });

  it('drops the label below the node when there is no headroom above (w2-02/w2-03)', () => {
    // Grid hub near the top: baseline y = 25 - 26 - 5 < 0 would clip the caption.
    const y = 25, size = 26;
    const dy = nodeLabelDy({ y, size }, FONT);
    // baseline must land below with the glyph fully in view (dy positive).
    expect(dy).toBeGreaterThan(0);
    // and the label's ascenders must not cross y=0.
    const baseline = y + dy;
    expect(baseline - FONT * 0.8).toBeGreaterThanOrEqual(0);
  });

  it('an authored top-row node (y=18) is not clipped', () => {
    const dy = nodeLabelDy({ y: 18, size: 10 }, FONT);
    const baseline = 18 + dy;
    expect(baseline - FONT * 0.8).toBeGreaterThanOrEqual(0);
  });
});

describe('D-445 groupCaptionY — caption above the rect, clear of member labels', () => {
  it('places the caption above the rect top edge (not the old rect.y+font+2)', () => {
    const rectY = 120, font = 14;
    const y = groupCaptionY(rectY, font);
    expect(y).toBeLessThan(rectY); // above the top border
    // the OLD placement (rectY + font + 2) overprinted members; ensure we moved off it
    expect(y).toBeLessThan(rectY + font + 2);
  });

  it('clamps so the caption ascenders do not clip above y=0 for a top group', () => {
    const font = 14;
    const y = groupCaptionY(1, font); // rect near the very top
    expect(y).toBeGreaterThanOrEqual(font * 0.8 - 1e-6);
  });
});

describe('D-446 capFontToNodeSpacing — font not taller than the node pitch', () => {
  it('caps a boosted font to the node spacing on a tall canvas (w2-08 pitch=50)', () => {
    // 200x3000 canvas boosts fontSize ~60 units; pitch is 50.
    const boosted = 60;
    const gap = 50;
    const capped = capFontToNodeSpacing(boosted, gap);
    expect(capped).toBeLessThan(gap);
    expect(capped).toBeLessThan(boosted);
  });

  it('never enlarges a comfortably-spaced graph', () => {
    expect(capFontToNodeSpacing(12, 200)).toBe(12);
  });

  it('minNearestNeighborGap finds the tightest spacing', () => {
    const nodes = [
      { x: 0, y: 0 }, { x: 50, y: 0 }, { x: 50, y: 40 },
    ];
    expect(minNearestNeighborGap(nodes)).toBeCloseTo(40, 6);
    expect(minNearestNeighborGap([{ x: 1, y: 1 }])).toBe(Infinity);
  });
});

describe('D-441 labelHaloWidth — suppress halos that would erase the topology', () => {
  it('drops the halo when MANY boosted labels tile at a tight pitch (w2-07: 90 nodes)', () => {
    // network-w2-07: 90 nodes, 2-char labels boosted to ~28px at a 33px pitch.
    // Halo-padded footprint (>33) merges into a solid band -> suppress (0).
    const halo = labelHaloWidth(28, 33, 2, 90);
    expect(halo).toBe(0);
  });

  it('keeps the halo for a HANDFUL of near neighbours (D-200 preserved)', () => {
    // Same tight pitch, but only 2 nodes: too few to tile into a band, so the
    // D-200 halo must stay on.
    const halo = labelHaloWidth(28, 33, 2, 2);
    expect(halo).toBeGreaterThan(0);
  });

  it('keeps the D-200 halo on a comfortably-spaced dense graph', () => {
    // Many nodes but a wide gap: halos never merge -> normal ~0.18*font halo.
    const halo = labelHaloWidth(12, 120, 8, 90);
    expect(halo).toBeGreaterThan(0);
    expect(halo).toBeCloseTo(Math.max(2, 12 * 0.18), 6);
  });
});

describe('sanity — helpers tolerate degenerate input', () => {
  it('all helpers accept empty/garbage without throwing', () => {
    expect(() => fitNodePositionsToViewport([] as any, 0, 0)).not.toThrow();
    expect(() => decollideCoincidentNodes(null as any, 1, 1)).not.toThrow();
    expect(nodeLabelDy(null, NETWORK_DEFAULT_NODE_SIZE)).toBeDefined();
    expect(labelHaloWidth(12, Infinity, 0)).toBeGreaterThan(0);
  });
});
