import {
  separateIndicatorFromLayoutTitle,
  fixDenseParcoordsLabels,
  parcoordsLabelAngleFor,
  preprocessPlotlySpec,
  PLOTLY_INDICATOR_DOMAIN_TOP,
  PLOTLY_INDICATOR_TITLE_MARGIN_T,
  PLOTLY_PARCOORDS_DENSE_DIMS,
} from '../plotlyPreprocessor';

/**
 * G-7bd20a — two structural plotly defects sharing plotlyPreprocessor.ts.
 *
 * D-303 (indicator-title-overlaps-layout-title): plotly-w1-10 is a KPI gauge
 * with BOTH a `layout.title` and an indicator-trace `title`, drawn on top of
 * each other at the paper top.
 *
 * D-308 (parcoords-dimension-labels-collide): plotly-w2-13 is a 24-dimension
 * parcoords whose horizontal top-anchored dimension labels overprint.
 *
 * Both fixes are theme-independent geometry, so a single assertion per theme is
 * unnecessary — but the tests explicitly confirm the output does not depend on
 * theme (the passes take no theme input).
 */

describe('separateIndicatorFromLayoutTitle (D-303)', () => {
  const gauge = () => ({
    data: [{
      type: 'indicator',
      mode: 'gauge+number+delta',
      value: 87.4,
      title: { text: 'SLA Attainment (%)' },
      domain: { x: [0, 1], y: [0, 1] },
    }],
    layout: { title: { text: 'Service Level Gauge' }, height: 440 },
  });

  it('pulls the indicator domain top down and reserves a top margin (the w1-10 case)', () => {
    const out = separateIndicatorFromLayoutTitle(gauge());
    expect(out.data[0].domain.y).toEqual([0, PLOTLY_INDICATOR_DOMAIN_TOP]);
    expect(out.layout.margin.t).toBe(PLOTLY_INDICATOR_TITLE_MARGIN_T);
  });

  it('is a no-op when the indicator has no title of its own', () => {
    const spec: any = {
      data: [{ type: 'indicator', mode: 'number', value: 1, domain: { y: [0, 1] } }],
      layout: { title: { text: 'Just a layout title' } },
    };
    expect(separateIndicatorFromLayoutTitle(spec)).toBe(spec);
  });

  it('is a no-op when there is no layout title (no collision)', () => {
    const spec: any = {
      data: [{ type: 'indicator', title: { text: 'KPI' }, domain: { y: [0, 1] } }],
      layout: { height: 440 },
    };
    expect(separateIndicatorFromLayoutTitle(spec)).toBe(spec);
  });

  it('respects an author who already left room at the top', () => {
    const spec: any = {
      data: [{ type: 'indicator', title: { text: 'KPI' }, domain: { y: [0, 0.8] } }],
      layout: { title: 'Dash' },
    };
    expect(separateIndicatorFromLayoutTitle(spec)).toBe(spec);
  });

  it('does not depend on theme (pure geometry)', () => {
    const a = separateIndicatorFromLayoutTitle(gauge());
    const b = separateIndicatorFromLayoutTitle(gauge());
    expect(a).toEqual(b);
  });

  it('through preprocessPlotlySpec the gauge no longer stacks both titles at the top', () => {
    const out = preprocessPlotlySpec(gauge() as any);
    // BEFORE the fix the indicator domain top stayed at 1 (title at paper top,
    // under the layout title); after the fix it is pulled below the title band.
    expect(out.data[0].domain.y[1]).toBeLessThanOrEqual(PLOTLY_INDICATOR_DOMAIN_TOP);
    expect(out.layout.margin.t).toBeGreaterThanOrEqual(PLOTLY_INDICATOR_TITLE_MARGIN_T);
  });
});

describe('fixDenseParcoordsLabels (D-308)', () => {
  const makeDims = (n: number) =>
    Array.from({ length: n }, (_v, i) => ({
      label: `dimension_${String(i).padStart(2, '0')}_metric`,
      range: [0, 100],
      values: [i, i + 1, i + 2],
    }));

  it('rotates labels and reserves top margin for a 24-dimension parcoords (the w2-13 case)', () => {
    const spec: any = {
      data: [{ type: 'parcoords', dimensions: makeDims(24) }],
      layout: { title: { text: '24-dimension parcoords' } },
    };
    const out = fixDenseParcoordsLabels(spec);
    expect(out.data[0].labelangle).toBe(-60); // dims > 16 -> steepest
    expect(out.layout.margin.t).toBeGreaterThanOrEqual(120);
  });

  it('angle steepens with axis density', () => {
    expect(parcoordsLabelAngleFor(10)).toBe(-30);
    expect(parcoordsLabelAngleFor(14)).toBe(-45);
    expect(parcoordsLabelAngleFor(24)).toBe(-60);
  });

  it('is a no-op for a sparse parcoords at/under the dense threshold', () => {
    const spec: any = {
      data: [{ type: 'parcoords', dimensions: makeDims(PLOTLY_PARCOORDS_DENSE_DIMS) }],
      layout: {},
    };
    expect(fixDenseParcoordsLabels(spec)).toBe(spec);
  });

  it('respects an author-chosen labelangle (never overrides it)', () => {
    const spec: any = {
      data: [{ type: 'parcoords', labelangle: 15, dimensions: makeDims(24) }],
      layout: {},
    };
    const out = fixDenseParcoordsLabels(spec);
    expect(out.data[0].labelangle).toBe(15);
  });

  it('does not depend on theme (pure geometry)', () => {
    const spec = () => ({
      data: [{ type: 'parcoords', dimensions: makeDims(24) }],
      layout: {},
    });
    expect(fixDenseParcoordsLabels(spec() as any))
      .toEqual(fixDenseParcoordsLabels(spec() as any));
  });

  it('through preprocessPlotlySpec a dense parcoords gains a labelangle', () => {
    const spec: any = {
      data: [{ type: 'parcoords', dimensions: makeDims(24), line: { colorscale: 'Viridis' } }],
      layout: { title: { text: '24-dimension parcoords' } },
    };
    const out = preprocessPlotlySpec(spec);
    expect(typeof out.data[0].labelangle).toBe('number');
    expect(out.data[0].labelangle).toBeLessThan(0);
  });
});
