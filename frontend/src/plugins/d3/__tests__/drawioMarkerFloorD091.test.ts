/**
 * G-cc6859 / D-091 — custom-and-orthogonal-arrowheads-dropped (default/orthogonal half).
 *
 * The drawio plugin pins every edge to endSize=3 assuming fit() magnifies the view, so a
 * marker lands ~6-9px. A WIDE diagram (drawio-w2-11 star, drawio-w2-15 chain) is fit-scaled
 * DOWN instead, so 3*scale(<1) collapses the arrowhead to sub-pixel — "arrowheads dropped".
 * DrawIOEnhancer.scaleDownArrowMarkers only ever SHRANK oversized markers, so a collapsed
 * marker stayed dropped.
 *
 * The fix adds a lower floor: computeMarkerNormalizationScale grows a sub-minMarkerPx marker
 * back up to the floor (scale > 1), while leaving any marker already inside the legible band
 * exactly 1 (no-op) — so no currently-correct spec is touched.
 *
 * DIRECTION: the GROW branch is what the fix adds. Before the floor existed a sub-pixel
 * marker resolved to scale 1 (stayed dropped); after, it resolves to a factor > 1. Marker
 * geometry is theme-independent (stroke/fill in the edge colour), so one assertion set
 * covers both light and dark; pixel sufficiency is a render-stage check.
 */

import { DrawIOEnhancer } from '../drawioEnhancer';

const MIN = 4;
const MAX = 12;

describe('D-091: DrawIOEnhancer.computeMarkerNormalizationScale keeps arrowheads in a legible band', () => {
    it('GROW (the fix): a fit-downscaled sub-pixel marker is scaled back UP to the floor', () => {
        // A 1px-tall arrowhead on a downscaled wide diagram — the dropped-marker case.
        const scale = DrawIOEnhancer.computeMarkerNormalizationScale(1, MIN, MAX);
        expect(scale).toBeGreaterThan(1);
        expect(1 * scale).toBeCloseTo(MIN, 5); // restored to exactly the floor
    });

    it('is a strict no-op for a marker already inside [min, max] (no corpus regression)', () => {
        for (const dim of [MIN, 5, 6, 8, MAX]) {
            expect(DrawIOEnhancer.computeMarkerNormalizationScale(dim, MIN, MAX)).toBe(1);
        }
    });

    it('SHRINK (legacy behaviour preserved): an oversized marker is scaled down to the cap', () => {
        const scale = DrawIOEnhancer.computeMarkerNormalizationScale(24, MIN, MAX);
        expect(scale).toBeLessThan(1);
        expect(24 * scale).toBeCloseTo(MAX, 5);
    });

    it('an unmeasurable (0 / non-finite) marker is left alone', () => {
        expect(DrawIOEnhancer.computeMarkerNormalizationScale(0, MIN, MAX)).toBe(1);
        expect(DrawIOEnhancer.computeMarkerNormalizationScale(NaN, MIN, MAX)).toBe(1);
        expect(DrawIOEnhancer.computeMarkerNormalizationScale(-3, MIN, MAX)).toBe(1);
    });
});
