/**
 * @jest-environment jsdom
 *
 * G-ef266e / D-287 (oversize-canvas-content-shrunk-labels-subpixel) and its
 * regression twin D-145 (oversize-canvas-content-clipped-labels-subpixel).
 *
 * Mermaid over-allocates the viewBox. The dominant failing case is a viewBox
 * whose HEIGHT is grossly larger than the content: the diagram sits in the top
 * ~15-25% and the rest is empty canvas. When the SVG is scaled to fit the
 * capture container, the real content collapses to a sub-pixel sliver.
 *
 * The pre-fix VIEWBOX-TRIM gate only fired when WIDTH could be reclaimed, so a
 * tight-width / oversize-height canvas was left untrimmed. computeViewBoxTrim
 * now reclaims EITHER axis.
 *
 * DIRECTION: the "oversize height, tight width" case is exactly what the old
 * width-only gate MISSED (asserted here as the reclaimHeightOnly scenario);
 * these assertions fail against the width-only gate and pass with the fix.
 */
import { computeViewBoxTrim } from '../mermaidPlugin';

describe('computeViewBoxTrim (D-287/D-145 oversize-canvas height reclaim)', () => {
    it('trims a tight-width but grossly-oversize-HEIGHT viewBox (the regression case)', () => {
        // Content is a 300x180 diagram; mermaid allocated a 320x2000 viewBox.
        // Width is already tight (320 vs 300+32pad) so the OLD width-only gate
        // would NOT trim — but the height is ~11x the content and must shrink.
        const r = computeViewBoxTrim('0 0 320 2000', { x: 0, y: 0, width: 300, height: 180 });
        expect(r.shouldTrim).toBe(true);
        expect(r.newViewBox).toBe('-16 -16 332 212');
        // Height reclaim is dramatic; width reclaim is below the 10% threshold.
        expect(r.reclaimedHeightPct).toBeGreaterThan(80);
        expect(r.reclaimedWidthPct).toBeLessThan(10);
    });

    it('still trims an oversize-WIDTH viewBox (unchanged behaviour)', () => {
        const r = computeViewBoxTrim('0 0 2000 220', { x: 0, y: 0, width: 200, height: 180 });
        expect(r.shouldTrim).toBe(true);
        expect(r.newViewBox).toBe('-16 -16 232 212');
        expect(r.reclaimedWidthPct).toBeGreaterThan(80);
    });

    it('leaves a viewBox that already fits the content untouched', () => {
        // Content nearly fills the box on both axes: neither axis reclaims 10%.
        const r = computeViewBoxTrim('0 0 340 240', { x: 0, y: 0, width: 300, height: 200 });
        expect(r.shouldTrim).toBe(false);
        expect(r.newViewBox).toBeNull();
    });

    it('is defensive against missing/degenerate inputs', () => {
        expect(computeViewBoxTrim(null, { x: 0, y: 0, width: 10, height: 10 }).shouldTrim).toBe(false);
        expect(computeViewBoxTrim('0 0 100 100', { x: 0, y: 0, width: 0, height: 0 }).shouldTrim).toBe(false);
        expect(computeViewBoxTrim('bad', { x: 0, y: 0, width: 10, height: 10 }).shouldTrim).toBe(false);
    });
});
