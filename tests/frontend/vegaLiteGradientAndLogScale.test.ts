/**
 * Tests for Vega-Lite preprocessing fixes:
 * 1. Malformed gradient objects in mark.color
 * 2. Area marks on log scale needing valid domain
 * 3. Nominal x-axis sort order preservation
 *
 * These test the logic inline since the full plugin depends on DOM + vega-embed.
 */

// ── 1. Malformed gradient fix ──────────────────────────────────────────────

function fixMalformedGradient(markObj: any): any {
  if (!markObj || typeof markObj !== 'object') return markObj;

  const gradientProps = ['color', 'fill', 'stroke'];
  for (const prop of gradientProps) {
    const val = markObj[prop];
    if (!val || typeof val !== 'object' || typeof val === 'string') continue;

    const hasStops = Array.isArray(val.stops) && val.stops.length > 0;
    const hasValidGradient = val.gradient === 'linear' || val.gradient === 'radial';

    if (hasStops && !hasValidGradient) {
      const cleanGradient: any = {
        gradient: 'linear',
        x1: typeof val.x1 === 'number' ? val.x1 : 0,
        y1: typeof val.y1 === 'number' ? val.y1 : 0,
        x2: typeof val.x2 === 'number' ? val.x2 : 0,
        y2: typeof val.y2 === 'number' ? val.y2 : 1,
        stops: val.stops.filter((s: any) =>
          s && typeof s.offset === 'number' && typeof s.color === 'string'
        ),
      };

      if (cleanGradient.stops.length >= 2) {
        markObj[prop] = cleanGradient;
      } else {
        const fallback = val.stops.find((s: any) => typeof s.color === 'string')?.color || '#888888';
        markObj[prop] = fallback;
      }
    }
  }
  return markObj;
}

describe('fixMalformedGradient', () => {
  it('repairs LLM-generated gradient with random hex key instead of "gradient"', () => {
    const mark = {
      type: 'area',
      color: {
        x1: 1, y1: 1, x2: 1, y2: 0,
        '#4ecdc4': 'linear',
        stops: [
          { offset: 0, color: '#1a0533' },
          { offset: 1, color: '#e94560' },
        ],
      },
    };

    fixMalformedGradient(mark);

    expect(mark.color).toEqual({
      gradient: 'linear',
      x1: 1, y1: 1, x2: 1, y2: 0,
      stops: [
        { offset: 0, color: '#1a0533' },
        { offset: 1, color: '#e94560' },
      ],
    });
  });

  it('leaves valid gradient objects untouched', () => {
    const mark = {
      type: 'area',
      color: {
        gradient: 'linear',
        x1: 0, y1: 0, x2: 0, y2: 1,
        stops: [
          { offset: 0, color: '#ff0000' },
          { offset: 1, color: '#0000ff' },
        ],
      },
    };

    const original = JSON.parse(JSON.stringify(mark));
    fixMalformedGradient(mark);
    expect(mark.color).toEqual(original.color);
  });

  it('falls back to solid color when stops are insufficient', () => {
    const mark = {
      type: 'area',
      color: {
        stops: [{ offset: 0, color: '#abcdef' }],
      },
    };

    fixMalformedGradient(mark);
    expect(mark.color).toBe('#abcdef');
  });

  it('falls back to #888888 when stops have no valid colors', () => {
    const mark = {
      type: 'area',
      color: {
        stops: [{ offset: 0 }, { offset: 1 }],
      },
    };

    fixMalformedGradient(mark);
    expect(mark.color).toBe('#888888');
  });

  it('leaves plain string color untouched', () => {
    const mark = { type: 'area', color: '#e94560' };
    fixMalformedGradient(mark);
    expect(mark.color).toBe('#e94560');
  });

  it('does not crash on null/undefined mark', () => {
    expect(fixMalformedGradient(null)).toBeNull();
    expect(fixMalformedGradient(undefined)).toBeUndefined();
  });

  it('repairs gradient in fill property', () => {
    const mark = {
      type: 'rect',
      fill: {
        stops: [
          { offset: 0, color: '#000' },
          { offset: 1, color: '#fff' },
        ],
      },
    };

    fixMalformedGradient(mark);
    expect(mark.fill.gradient).toBe('linear');
  });

  it('supplies default coordinates when x1/y1/x2/y2 are missing', () => {
    const mark = {
      type: 'area',
      color: {
        stops: [
          { offset: 0, color: '#000' },
          { offset: 1, color: '#fff' },
        ],
      },
    };

    fixMalformedGradient(mark);
    expect(mark.color.x1).toBe(0);
    expect(mark.color.y1).toBe(0);
    expect(mark.color.x2).toBe(0);
    expect(mark.color.y2).toBe(1);
  });
});

// ── 2. Area log scale domain fix ───────────────────────────────────────────

function fixAreaLogScaleDomain(s: any): any {
  const markType = typeof s.mark === 'string' ? s.mark : s.mark?.type;
  if (!['area', 'line'].includes(markType)) return s;

  const yEnc = s.encoding?.y;
  if (!yEnc || yEnc.type !== 'quantitative') return s;
  if (!yEnc.scale || yEnc.scale.type !== 'log') return s;

  if (Array.isArray(yEnc.scale.domain) && yEnc.scale.domain[0] > 0) return s;

  const field = yEnc.field;
  if (!field || !s.data?.values) return s;

  const values = s.data.values
    .map((d: any) => d[field])
    .filter((v: any) => typeof v === 'number' && v > 0);

  if (values.length === 0) return s;

  const minVal = Math.min(...values);
  const maxVal = Math.max(...values);
  const lowerBound = Math.min(1, minVal);
  const upperBound = maxVal * 2;

  yEnc.scale.domain = [lowerBound, upperBound];
  return s;
}

describe('fixAreaLogScaleDomain', () => {
  it('adds domain to area mark with log scale and no domain', () => {
    const spec = {
      mark: 'area',
      data: { values: [{ temp: 10000000000 }, { temp: 2.7 }] },
      encoding: {
        y: { field: 'temp', type: 'quantitative', scale: { type: 'log' } },
      },
    };

    fixAreaLogScaleDomain(spec);

    expect(spec.encoding.y.scale.domain).toBeDefined();
    expect(spec.encoding.y.scale.domain[0]).toBeGreaterThan(0);
    expect(spec.encoding.y.scale.domain[0]).toBeLessThanOrEqual(1);
    expect(spec.encoding.y.scale.domain[1]).toBeGreaterThan(10000000000);
  });

  it('does not modify bar charts', () => {
    const spec = {
      mark: 'bar',
      data: { values: [{ v: 10 }] },
      encoding: { y: { field: 'v', type: 'quantitative', scale: { type: 'log' } } },
    };

    const original = JSON.parse(JSON.stringify(spec));
    fixAreaLogScaleDomain(spec);
    expect(spec).toEqual(original);
  });

  it('does not modify area charts without log scale', () => {
    const spec = {
      mark: 'area',
      data: { values: [{ v: 10 }] },
      encoding: { y: { field: 'v', type: 'quantitative', scale: {} } },
    };

    const original = JSON.parse(JSON.stringify(spec));
    fixAreaLogScaleDomain(spec);
    expect(spec).toEqual(original);
  });

  it('preserves existing valid domain', () => {
    const spec = {
      mark: { type: 'area' },
      data: { values: [{ v: 100 }, { v: 1 }] },
      encoding: {
        y: {
          field: 'v', type: 'quantitative',
          scale: { type: 'log', domain: [0.5, 500] },
        },
      },
    };

    fixAreaLogScaleDomain(spec);
    expect(spec.encoding.y.scale.domain).toEqual([0.5, 500]);
  });

  it('handles fractional minimum values (e.g., 2.7 K)', () => {
    const spec = {
      mark: 'area',
      data: { values: [{ temp: 3000 }, { temp: 2.7 }] },
      encoding: {
        y: { field: 'temp', type: 'quantitative', scale: { type: 'log' } },
      },
    };

    fixAreaLogScaleDomain(spec);
    expect(spec.encoding.y.scale.domain[0]).toBeLessThanOrEqual(1);
    expect(spec.encoding.y.scale.domain[0]).toBeGreaterThan(0);
  });

  it('also works for line marks with log scale', () => {
    const spec = {
      mark: 'line',
      data: { values: [{ v: 1000 }, { v: 5 }] },
      encoding: {
        y: { field: 'v', type: 'quantitative', scale: { type: 'log' } },
      },
    };

    fixAreaLogScaleDomain(spec);
    expect(spec.encoding.y.scale.domain).toBeDefined();
    expect(spec.encoding.y.scale.domain[0]).toBeGreaterThan(0);
  });
});

// ── 3. Nominal x-axis sort order preservation ──────────────────────────────

function fixNominalSortOrder(spec: any): any {
  if (!spec.encoding?.x?.type || spec.encoding.x.type !== 'nominal') return spec;
  if (spec.encoding.x.sort) return spec;
  if (!spec.data?.values) return spec;

  const xField = spec.encoding.x.field;
  if (!xField) return spec;

  const dataOrder = spec.data.values
    .map((d: any) => d[xField])
    .filter((v: any, i: number, arr: any[]) => arr.indexOf(v) === i);

  if (dataOrder.length > 1 && dataOrder.length <= 100) {
    spec.encoding.x.sort = dataOrder;
  }
  return spec;
}

describe('fixNominalSortOrder', () => {
  it('preserves chronological order from data for cosmic epochs', () => {
    const spec = {
      data: {
        values: [
          { epoch: '0 sec', temp: 1e10 },
          { epoch: '3 min', temp: 1e9 },
          { epoch: '380K yr', temp: 3000 },
          { epoch: '13.8B yr', temp: 2.7 },
        ],
      },
      encoding: {
        x: { field: 'epoch', type: 'nominal', title: 'Cosmic Epoch' },
      },
    };

    fixNominalSortOrder(spec);
    expect(spec.encoding.x.sort).toEqual(['0 sec', '3 min', '380K yr', '13.8B yr']);
  });

  it('does not override explicit sort', () => {
    const spec = {
      data: { values: [{ a: 'B' }, { a: 'A' }] },
      encoding: {
        x: { field: 'a', type: 'nominal', sort: ['A', 'B'] },
      },
    };

    fixNominalSortOrder(spec);
    expect(spec.encoding.x.sort).toEqual(['A', 'B']);
  });

  it('does not apply to quantitative axes', () => {
    const spec = {
      data: { values: [{ v: 3 }, { v: 1 }] },
      encoding: { x: { field: 'v', type: 'quantitative' } },
    };

    const original = JSON.parse(JSON.stringify(spec));
    fixNominalSortOrder(spec);
    expect(spec).toEqual(original);
  });

  it('deduplicates repeated values in data', () => {
    const spec = {
      data: { values: [{ c: 'X' }, { c: 'Y' }, { c: 'X' }, { c: 'Z' }] },
      encoding: { x: { field: 'c', type: 'nominal' } },
    };

    fixNominalSortOrder(spec);
    expect(spec.encoding.x.sort).toEqual(['X', 'Y', 'Z']);
  });

  it('does not apply when there is only one unique value', () => {
    const spec = {
      data: { values: [{ c: 'A' }, { c: 'A' }] },
      encoding: { x: { field: 'c', type: 'nominal' } },
    };

    fixNominalSortOrder(spec);
    expect(spec.encoding.x.sort).toBeUndefined();
  });
});
