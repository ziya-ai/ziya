/**
 * G-cc6859 / D-091 — custom-and-orthogonal-arrowheads-dropped (fit-scale half).
 *
 * ROOT CAUSE the earlier fixes missed: `path.getBBox()` reports a marker's INTRINSIC
 * local size, unaffected by the ancestor view-scale transform that fitCenter applies.
 * The on-screen size is `localDim * viewScale`. `scaleDownArrowMarkers` decided the
 * legibility band against the LOCAL size and, worse, ran BEFORE fitCenter (while
 * view.scale === 1). So on a WIDE diagram (drawio-w2-11 star / drawio-w2-15 chain) that
 * fitCenter DOWN-scales, a local ~4px arrowhead — perfectly "in band" locally — renders
 * sub-pixel and drops. The pure-function floor added previously could never fire because
 * the value it was fed (the local size) was already in band.
 *
 * THE FIX: `scaleDownArrowMarkers` now takes the final `viewScale`, normalises in SCREEN
 * space (`screenDim = localDim * viewScale`), and the plugin calls it AFTER fitCenter with
 * the settled `graph.view.scale`. A collapsed marker is grown; an amplified one is shrunk;
 * an on-screen-in-band marker (the verified corpus) is left untouched.
 *
 * DIRECTION: with the OLD (local-space, pre-fit) logic a local-4px marker at viewScale 0.4
 * resolves to factor 1 (no transform → stays dropped); with the fix it resolves to a factor
 * > 1 that restores the on-screen size to the 4px floor. Marker geometry strokes/fills in
 * the edge colour, so it is theme-independent — one assertion set covers light and dark;
 * pixel sufficiency is the render-stage check.
 *
 * @jest-environment jsdom
 */

import { DrawIOEnhancer } from '../drawioEnhancer';

const SVG_NS = 'http://www.w3.org/2000/svg';
const MIN = 4;
const MAX = 12;

/** Build an edge group: an unfilled line path + a filled marker path with a stubbed bbox. */
function makeEdgeWithMarker(markerLocalPx: number): {
    svg: SVGSVGElement;
    marker: SVGPathElement;
} {
    const svg = document.createElementNS(SVG_NS, 'svg') as SVGSVGElement;
    const g = document.createElementNS(SVG_NS, 'g');
    // Edge body — unfilled path, marks this group as an edge shape group.
    const line = document.createElementNS(SVG_NS, 'path');
    line.setAttribute('fill', 'none');
    line.setAttribute('d', 'M0,0 L100,0');
    // Arrowhead — small filled path.
    const marker = document.createElementNS(SVG_NS, 'path') as SVGPathElement;
    marker.setAttribute('fill', '#333333');
    marker.setAttribute('d', 'M0,0 L4,2 L0,4 Z');
    // jsdom has no getBBox; stub the intrinsic local size.
    (marker as any).getBBox = () => ({
        x: 0, y: 0, width: markerLocalPx, height: markerLocalPx,
    });
    g.appendChild(line);
    g.appendChild(marker);
    svg.appendChild(g);
    return { svg, marker };
}

/** Parse the scale() factor out of the transform the enhancer appends, or 1 if none. */
function appliedScale(marker: SVGPathElement): number {
    const t = marker.getAttribute('transform') || '';
    const m = t.match(/scale\(([\d.]+)\)/);
    return m ? parseFloat(m[1]) : 1;
}

describe('D-091: scaleDownArrowMarkers normalises in SCREEN space using the fit view scale', () => {
    it('GROW (the fix): a locally in-band marker collapsed by a fit-DOWNscale is grown back to the floor', () => {
        // Local 4px is "in band" locally, but at viewScale 0.4 it renders 1.6px — dropped.
        const { svg, marker } = makeEdgeWithMarker(4);
        DrawIOEnhancer.scaleDownArrowMarkers(svg, MAX, MIN, 0.4);

        const f = appliedScale(marker);
        // OLD local-space logic left this at 1 (assertion below fails without the fix).
        expect(f).toBeGreaterThan(1);
        // Screen size restored to exactly the floor: (4 * 0.4) * f === 4.
        expect(4 * 0.4 * f).toBeCloseTo(MIN, 3);
    });

    it('SHRINK: a marker amplified past the cap by a fit-UPscale is shrunk to the cap', () => {
        // Local 4px at viewScale 4 renders 16px — oversized.
        const { svg, marker } = makeEdgeWithMarker(4);
        DrawIOEnhancer.scaleDownArrowMarkers(svg, MAX, MIN, 4);

        const f = appliedScale(marker);
        expect(f).toBeLessThan(1);
        expect(4 * 4 * f).toBeCloseTo(MAX, 3);
    });

    it('NO-OP: an on-screen-in-band marker (verified corpus) is left byte-for-byte unchanged', () => {
        // Local 4px at viewScale ~2 renders ~8px — squarely in [4,12], must not be touched.
        const { svg, marker } = makeEdgeWithMarker(4);
        DrawIOEnhancer.scaleDownArrowMarkers(svg, MAX, MIN, 2);
        expect(marker.getAttribute('transform')).toBeNull();
    });

    it('a non-finite / zero view scale falls back to 1 (never divides by zero)', () => {
        const { svg, marker } = makeEdgeWithMarker(6); // 6px local, in band at scale 1
        DrawIOEnhancer.scaleDownArrowMarkers(svg, MAX, MIN, 0);
        expect(marker.getAttribute('transform')).toBeNull();
    });
});
