/**
 * Regression: a Vega-Lite spec with an explicit numeric width/height must NOT
 * have those values adopted as the D3Renderer CONTAINER size.
 *
 * Symptom this guards against: every inline `vega-lite` block with e.g.
 * `"width":420,"height":160` rendered followed by a blank region as tall as
 * the chart itself (worse on wide viewports). resolveContainerDimensions
 * pinned the d3-container to 420px; Vega initialised its `width:'container'`
 * signal from that box and emitted a 420-wide SVG (with viewBox); the
 * container was then released to 100%, the responsive `svg { width:100% }`
 * rule stretched the SVG ~3x, and the grow-only wrapper height writers baked
 * the inflated height (measured 740px for a 248px chart at 1184px wide) into
 * the layout for good.
 *
 * For a plugin that ownsSpecDimensions, width/height are DOCUMENT properties
 * (plot area, or the string 'container'), so the container clamp must be a
 * no-op. Envelope plugins keep the D-001 behaviour.
 */
import { resolveContainerDimensions } from '../pluginDimensions';
import { vegaLitePlugin } from '../../plugins/d3/vegaLitePlugin';

const specWithDims = {
    $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
    width: 420,
    height: 160,
    mark: 'bar',
    data: { values: [{ a: 'x', b: 1 }] },
    encoding: { x: { field: 'a' }, y: { field: 'b' } },
};

describe('resolveContainerDimensions vs ownsSpecDimensions', () => {
    it('returns null for a document-owning plugin even when the spec has numeric dims', () => {
        expect(resolveContainerDimensions(specWithDims, 'responsive', { ownsSpecDimensions: true })).toBeNull();
    });

    it('returns null for the real vegaLitePlugin (the case that produced the blank band)', () => {
        expect(vegaLitePlugin.ownsSpecDimensions).toBe(true);
        expect(
            resolveContainerDimensions(specWithDims, vegaLitePlugin.sizingConfig?.sizingStrategy, vegaLitePlugin),
        ).toBeNull();
    });

    it('still adopts explicit dims for an envelope plugin (D-001 unchanged)', () => {
        expect(resolveContainerDimensions(specWithDims, 'responsive', { ownsSpecDimensions: false }))
            .toEqual({ width: '420px', height: '160px' });
        expect(resolveContainerDimensions(specWithDims, 'responsive'))
            .toEqual({ width: '420px', height: '160px' });
    });

    it('is unchanged for fixed plugins and dimensionless specs', () => {
        expect(resolveContainerDimensions(specWithDims, 'fixed', { ownsSpecDimensions: false })).toBeNull();
        expect(resolveContainerDimensions({ mark: 'bar' }, 'responsive')).toBeNull();
    });
});
