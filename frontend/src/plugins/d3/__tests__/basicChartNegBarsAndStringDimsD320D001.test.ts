/**
 * Two rendering bugs that surfaced in the basic-chart engine ONCE the dispatch
 * fix (D-320) let its specs actually render — both are red against the pre-fix
 * code and green after.
 *
 *   D-320 / basic-chart-w3-04  Negative bar values.
 *     The y-domain was hardcoded `[0, max]`, so a negative datum mapped BELOW
 *     the [0..] floor and the bar's `height - y(value)` went negative — SVG
 *     rejects a negative rect height (console error) and the bar vanished. A
 *     dataset with any negative value lost every negative bar. Fix: span the
 *     domain across zero and anchor bars to the zero baseline.
 *
 *   D-001 / basic-chart-w2-11  3600x2600 bubble canvas rendered blank.
 *     extractExplicitDimensions only unwrapped an OBJECT `definition`. The
 *     render boundary (diagram_renderer.normalize_spec_definition) serialises
 *     the definition to a JSON STRING before it reaches the browser, so the
 *     memoised container-style path read no dims, returned null, and the
 *     responsive 400px-height default clamped the 2600px-tall canvas to blank
 *     on capture. w2-08 (180px tall) only escaped because it fit inside 400px.
 *     Fix: parse a string definition too.
 */
import { barYDomain, barRectGeometry } from '../basicChart';
import {
    extractExplicitDimensions,
    resolveContainerDimensions,
} from '../../../utils/pluginDimensions';

describe('barYDomain — negative bar values (D-320 / w3-04)', () => {
    it('spans the domain across zero when data has negatives (pre-fix was [0,max])', () => {
        // w3-04 data: neg=-40, pos=30, neg2=-15
        expect(barYDomain([-40, 30, -15])).toEqual([-40, 30]);
    });

    it('is a strict no-op for all-positive data (domain stays [0,max])', () => {
        expect(barYDomain([42, 58, 31])).toEqual([0, 58]);
    });

    it('keeps zero on-scale for all-negative data', () => {
        expect(barYDomain([-5, -20, -3])).toEqual([-20, 0]);
    });

    it('tolerates an empty / all-NaN dataset', () => {
        expect(barYDomain([])).toEqual([0, 0]);
        expect(barYDomain([NaN as unknown as number])).toEqual([0, 0]);
    });
});

describe('barRectGeometry — zero-baseline bars (D-320 / w3-04)', () => {
    // Model a 400px-tall plot with domain [-40, 30] (range [height,0]=[400,0]).
    // Linear map: y(v) = 400 * (30 - v) / (30 - (-40)) = 400 * (30 - v) / 70.
    const H = 400;
    const y = (v: number) => H * (30 - v) / 70;
    const baseline = y(0); // ~171.4

    it('gives a POSITIVE height for a negative value (pre-fix went negative)', () => {
        const g = barRectGeometry(y(-40), baseline);
        expect(g.height).toBeGreaterThan(0);
        // Negative bar drops from the baseline downward: its top is the baseline.
        expect(g.y).toBeCloseTo(baseline, 5);
        expect(g.y + g.height).toBeCloseTo(y(-40), 5);
    });

    it('gives a POSITIVE height for a positive value, rising from the baseline', () => {
        const g = barRectGeometry(y(30), baseline);
        expect(g.height).toBeGreaterThan(0);
        expect(g.y).toBeCloseTo(y(30), 5);
        expect(g.y + g.height).toBeCloseTo(baseline, 5);
    });

    it('reduces to the historical formula for an all-positive chart', () => {
        // All-positive: domain [0,max], baseline === plot bottom (== height).
        const yPos = (v: number) => H * (58 - v) / 58; // domain [0,58]
        const base = yPos(0); // === H (plot bottom)
        expect(base).toBeCloseTo(H, 5);
        const g = barRectGeometry(yPos(42), base);
        expect(g.y).toBeCloseTo(yPos(42), 5);            // old: y = y(value)
        expect(g.height).toBeCloseTo(H - yPos(42), 5);   // old: height = plotBottom - y(value)
    });
});

describe('extractExplicitDimensions — string definition envelope (D-001 / w2-11)', () => {
    it('reads dims from a STRING definition (the render-boundary shape; pre-fix returned null)', () => {
        const env = {
            type: 'basic-chart',
            definition: JSON.stringify({ type: 'bubble', width: 3600, height: 2600, data: [{ x: 1, y: 1, size: 5 }] }),
        };
        expect(extractExplicitDimensions(env)).toEqual({ width: 3600, height: 2600 });
    });

    it('still reads dims from an OBJECT definition envelope (unchanged path)', () => {
        const env = { type: 'basic-chart', definition: { type: 'bubble', width: 3600, height: 2600 } };
        expect(extractExplicitDimensions(env)).toEqual({ width: 3600, height: 2600 });
    });

    it('returns null for a non-JSON string definition (graphviz/mermaid source)', () => {
        const env = { type: 'graphviz', definition: 'digraph { a -> b }' };
        expect(extractExplicitDimensions(env)).toBeNull();
    });

    it('returns null when a string definition carries no explicit dims', () => {
        const env = { type: 'basic-chart', definition: JSON.stringify({ type: 'bar', data: [{ label: 'A', value: 1 }] }) };
        expect(extractExplicitDimensions(env)).toBeNull();
    });
});

describe('resolveContainerDimensions — responsive plugin adopts string-envelope dims (D-001 / w2-11)', () => {
    it('adopts the 3600x2600 canvas on the container (pre-fix stayed on the 400px default)', () => {
        const env = {
            type: 'basic-chart',
            definition: JSON.stringify({ type: 'bubble', width: 3600, height: 2600, data: [] }),
        };
        expect(resolveContainerDimensions(env, 'responsive', { ownsSpecDimensions: false }))
            .toEqual({ width: '3600px', height: '2600px' });
    });

    it('is null for a responsive spec with no explicit dims (default preserved)', () => {
        const env = { type: 'basic-chart', definition: JSON.stringify({ type: 'bar', data: [] }) };
        expect(resolveContainerDimensions(env, 'responsive', { ownsSpecDimensions: false })).toBeNull();
    });
});
