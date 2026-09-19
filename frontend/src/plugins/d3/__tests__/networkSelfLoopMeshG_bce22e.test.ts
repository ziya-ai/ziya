/**
 * Fix group G-bce22e — network self-loop + dense-mesh label defects, both in
 * frontend/src/plugins/d3/networkDiagram.ts.
 *
 * D-448 selfLoopPath      — a self-referencing edge (source === target) was drawn
 *                           as a straight <line> (x1==x2, y1==y2), a zero-length
 *                           invisible segment; authored self-edges vanished
 *                           (network-w3-04). It must be an arc.
 * D-449 labelHaloWidth    — on a dense edge mesh (K25: 25 nodes / 300 edges) the
 *                           interior labels are buried under the low-opacity link
 *                           fan and the thin 0.18*font halo cannot rescue them
 *                           (network-w2-11). The halo must widen with mean degree.
 *
 * Both blocks fail against the pre-fix source: selfLoopPath did not exist, and
 * labelHaloWidth had no edgeCount parameter (so the dense-mesh boost is absent).
 */
import { selfLoopPath, labelHaloWidth } from '../networkDiagram';

describe('D-448 selfLoopPath — self-edge drawn as a visible arc, not a zero-length line', () => {
    it('produces a non-degenerate path with distinct control points', () => {
        const d = selfLoopPath(100, 200, 10);
        expect(typeof d).toBe('string');
        expect(d.length).toBeGreaterThan(0);
        // Parse the numeric coordinates out of the path.
        const nums = (d.match(/-?\d+(\.\d+)?/g) || []).map(Number);
        expect(nums.length).toBeGreaterThanOrEqual(8);
        // The loop must span a real area: not every point coincides (the old
        // degenerate line had start == end and zero extent).
        const xs = nums.filter((_, i) => i % 2 === 0);
        const ys = nums.filter((_, i) => i % 2 === 1);
        const spanX = Math.max(...xs) - Math.min(...xs);
        const spanY = Math.max(...ys) - Math.min(...ys);
        expect(spanX).toBeGreaterThan(0);
        expect(spanY).toBeGreaterThan(0);
    });

    it('clamps the loop apex to the viewBox top for a near-top node', () => {
        // A node whose centre sits just below its own radius (post viewport clamp
        // the top edge is at y >= 0). The loop apex would otherwise bulge well
        // above y=0; it must be clamped to 0 rather than clipped off-canvas.
        const r = 10;
        const d = selfLoopPath(50, r, r); // top edge at y=0
        const ys = (d.match(/-?\d+(\.\d+)?/g) || []).map(Number).filter((_, i) => i % 2 === 1);
        for (const y of ys) expect(y).toBeGreaterThanOrEqual(0);
        // The apex is pulled up toward the top; clamped exactly at 0 here.
        expect(Math.min(...ys)).toBe(0);
    });
});

describe('D-449 labelHaloWidth — widen the halo under a dense edge mesh', () => {
    const font = 14;
    const base = Math.max(2, font * 0.18);

    it('boosts the halo for a near-complete graph (K25: 25 nodes, 300 edges)', () => {
        // minGap large / short labels so the D-441 band suppression does not fire.
        const w = labelHaloWidth(font, 60, 2, 25, 30, 300);
        expect(w).toBeGreaterThan(base * 1.5);
        expect(w).toBeCloseTo(font * 0.35, 5);
    });

    it('leaves a sparse graph unchanged (no dense mesh => thin D-200 halo)', () => {
        const w = labelHaloWidth(font, 60, 2, 25, 30, 12); // mean degree ~1
        expect(w).toBeCloseTo(base, 5);
    });

    it('still suppresses the halo to a band on a high-count wide-label graph', () => {
        // 40 nodes tight gap, long labels -> band merge wins even with edges.
        const w = labelHaloWidth(font, 20, 12, 40, 30, 500);
        expect(w).toBe(0);
    });
});
