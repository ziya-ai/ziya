/**
 * D-001 (remaining container-clamp cause) regression test.
 *
 * `resolvePluginDimensions` (see basicChartDimensionsGD001.test.ts) already makes
 * the plugin receive the requested width/height and write them onto the SVG. But
 * for a non-'fixed' plugin the D3Renderer CONTAINER was still hard-clamped to its
 * sizingConfig default — basic-chart is `sizingStrategy:'responsive'` with
 * `containerStyles.height:'400px'` and overflow hidden — so a requested
 * 220x2400 / 3600x2600 canvas was silently cropped back to ~600x400 on capture.
 * The requested dims were honoured on the SVG yet ignored on the container.
 *
 * `resolveContainerDimensions` closes that gap: for a non-'fixed' plugin whose
 * spec supplies explicit positive dimensions it returns the pixel container size
 * to adopt, and returns null (preserve the responsive default) otherwise.
 *
 * Fail-without-the-fix: the helper did not exist before this change (import
 * undefined -> TypeError), and behaviourally the responsive container previously
 * had no path to a spec-driven height at all.
 *
 * D-001 is STRUCTURAL (theme-independent geometry).
 */

import {
    extractExplicitDimensions,
    resolveContainerDimensions,
} from '../../../utils/pluginDimensions';

describe('extractExplicitDimensions — explicit positive dims through the accepted envelopes (D-001)', () => {
    it('reads an object spec with explicit dims', () => {
        expect(extractExplicitDimensions({ type: 'bar', width: 220, height: 2400 }))
            .toEqual({ width: 220, height: 2400 });
    });

    it('reads a raw JSON string spec', () => {
        expect(extractExplicitDimensions('{"type":"bar","width":3600,"height":2600}'))
            .toEqual({ width: 3600, height: 2600 });
    });

    it('unwraps a { type:"d3", definition:<spec> } envelope', () => {
        expect(extractExplicitDimensions({ type: 'd3', definition: { type: 'bar', width: 110, height: 80 } }))
            .toEqual({ width: 110, height: 80 });
    });

    it('returns null when either dimension is missing / non-positive / non-numeric', () => {
        expect(extractExplicitDimensions({ type: 'bar', width: 220 })).toBeNull();
        expect(extractExplicitDimensions({ width: 0, height: 400 })).toBeNull();
        expect(extractExplicitDimensions({ width: '220', height: 2400 })).toBeNull();
        expect(extractExplicitDimensions('not json')).toBeNull();
        expect(extractExplicitDimensions(null)).toBeNull();
    });
});

describe('resolveContainerDimensions — a responsive container honours an explicit canvas (D-001)', () => {
    it.each([
        [{ type: 'bar', width: 220, height: 2400 }, { width: '220px', height: '2400px' }],
        [{ type: 'bar', width: 3600, height: 2600 }, { width: '3600px', height: '2600px' }],
        [{ type: 'bar', width: 110, height: 80 }, { width: '110px', height: '80px' }],
    ])('adopts the requested canvas %o instead of the 400px default', (spec, expected) => {
        expect(resolveContainerDimensions(spec, 'responsive')).toEqual(expected);
    });

    it('is a strict no-op when the spec omits dimensions (responsive default preserved)', () => {
        expect(resolveContainerDimensions({ type: 'bar', data: [] }, 'responsive')).toBeNull();
    });

    it('never overrides a fixed-strategy plugin (it already gets explicit props)', () => {
        expect(resolveContainerDimensions({ type: 'bar', width: 220, height: 2400 }, 'fixed')).toBeNull();
    });
});
