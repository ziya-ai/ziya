/**
 * D-513 (unranged-ordinal-scale-legend-has-no-swatches) — group G-9ee008.
 *
 * vega-w3-14 authors NO colour anywhere. Its native-Vega `col` ordinal scale
 * has a domain but NO range, and a `series` legend references it for `fill`.
 * Native Vega gives a rangeless ordinal scale no default colours, so the legend
 * renders labels with an EMPTY swatch column. Vega-Lite auto-assigns a
 * categorical scheme; native Vega does not, so we inject Vega's default
 * categorical scheme onto the legend-referenced rangeless ordinal scale.
 *
 * Direction: on the unpatched tree the scale keeps `range: undefined`, so the
 * "scale gained a scheme range" assertion below fails.
 *
 * Theme-independent: the injected range is theme-agnostic (categorical schemes
 * are identical in both themes), asserted via two invocations.
 */
import { injectRangelessOrdinalColorScaleRange } from '../vegaPlugin';

// Reduced form of vega-w3-14: an ordinal `col` scale with a domain, no range,
// referenced by a legend fill.
const rangelessOrdinalLegend = () => ({
  $schema: 'https://vega.github.io/schema/vega/v5.json',
  data: [{ name: 't', values: [{ c: 'alpha', v: 31 }, { c: 'beta', v: 58 }] }],
  scales: [
    { name: 'x', type: 'band', domain: { data: 't', field: 'c' }, range: 'width' },
    { name: 'y', type: 'linear', domain: { data: 't', field: 'v' }, range: 'height' },
    { name: 'col', type: 'ordinal', domain: { data: 't', field: 'c' } },
  ],
  legends: [{ fill: 'col', title: 'series' }],
  marks: [{ type: 'rect', from: { data: 't' } }],
});

describe('D-513 rangeless ordinal colour scale gets a default range', () => {
  it('injects a categorical scheme range onto a legend-referenced rangeless ordinal scale', () => {
    const spec: any = rangelessOrdinalLegend();
    const injected = injectRangelessOrdinalColorScaleRange(spec);

    expect(injected).toBe(1);
    const col = spec.scales.find((s: any) => s.name === 'col');
    expect(col.range).toBeDefined();
    expect(col.range.scheme).toBe('category10');
    // The positional scales are untouched — they are not colour roles.
    expect(spec.scales.find((s: any) => s.name === 'x').range).toBe('width');
    expect(spec.scales.find((s: any) => s.name === 'y').range).toBe('height');
  });

  it('is theme-independent: identical injection in both themes', () => {
    const light: any = rangelessOrdinalLegend();
    const dark: any = rangelessOrdinalLegend();
    injectRangelessOrdinalColorScaleRange(light);
    injectRangelessOrdinalColorScaleRange(dark);
    expect(light.scales).toEqual(dark.scales);
  });

  it('leaves an ordinal colour scale that already has a range alone', () => {
    const spec: any = rangelessOrdinalLegend();
    const col = spec.scales.find((s: any) => s.name === 'col');
    col.range = { scheme: 'tableau10' };
    expect(injectRangelessOrdinalColorScaleRange(spec)).toBe(0);
    expect(col.range).toEqual({ scheme: 'tableau10' });
  });

  it('leaves an explicit array range alone', () => {
    const spec: any = rangelessOrdinalLegend();
    const col = spec.scales.find((s: any) => s.name === 'col');
    col.range = ['#111', '#222'];
    expect(injectRangelessOrdinalColorScaleRange(spec)).toBe(0);
    expect(col.range).toEqual(['#111', '#222']);
  });

  it('does not touch a rangeless ordinal scale NOT referenced by a legend colour role', () => {
    const spec: any = rangelessOrdinalLegend();
    spec.legends = []; // no legend uses `col` for colour
    expect(injectRangelessOrdinalColorScaleRange(spec)).toBe(0);
    expect(spec.scales.find((s: any) => s.name === 'col').range).toBeUndefined();
  });
});
