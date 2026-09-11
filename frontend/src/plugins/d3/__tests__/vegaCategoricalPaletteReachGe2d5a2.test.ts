/**
 * G-e2d5a2 / D-253 — categorical palette recycles at >40 data-driven series.
 *
 * The G-14/D-014 data-driven branch of applyCategoricalPaletteFix extends the
 * theme's 10-colour range to a FIXED CATEGORY_EXTEND_TARGET (40). That is
 * injective for the w2-04/11/13 specs (20/40/30 distinct series) but NOT for
 * vega-lite-w2-12, whose `data.sequence{stop:50}` + calculate produces 50
 * distinct `category-name-NN` series — entries 41..50 recycle onto colours
 * 1..10, a silent encoding lie in BOTH themes.
 *
 * The fix sizes the extended palette to the data's row/sequence UPPER BOUND
 * when that bound is modest (> 40, ≤ MAX_ESTIMATED_CATEGORY=64) — 50 for
 * w2-12 — so the range is injective. A huge sequence (w2-04's 2000 rows for
 * ~20 real series) keeps the 40 default (would only bloat the range).
 *
 * Direction: this imports estimateDataDrivenCardinality / MAX_ESTIMATED_CATEGORY
 * (which did not exist pre-fix → module import fails on the unpatched tree),
 * and the 50-series assertions expect a >=50-entry injective range where the
 * unpatched code emitted exactly 40 and recycled. Both themes are asserted.
 */
import {
  applyCategoricalPaletteFix,
  analyzeCategoricalColor,
  estimateDataDrivenCardinality,
  extendCategoricalPalette,
  CATEGORY_EXTEND_TARGET,
  MAX_ESTIMATED_CATEGORY,
  EXCEL_CATEGORY_10,
  SATURATED_CATEGORY_10,
} from '../vegaRecovery';

// vega-lite-w2-12: 50-entry categorical legend from a sequence + calculate.
const w2_12 = () => ({
  $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
  data: { sequence: { start: 0, stop: 50, step: 1, as: 'n' } },
  transform: [
    { calculate: 'datum.n%25', as: 'x' },
    { calculate: "'category-name-'+format(datum.n,'02d')", as: 'k' },
  ],
  mark: { type: 'point', size: 90, filled: true },
  encoding: {
    x: { field: 'x', type: 'quantitative' },
    y: { field: 'x', type: 'quantitative' },
    color: { field: 'k', type: 'nominal' },
  },
});

// vega-lite-w2-04: 20 real series but a 2000-row sequence (huge upper bound).
const w2_04 = () => ({
  $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
  data: { sequence: { start: 0, stop: 2000, step: 1, as: 'n' } },
  transform: [{ calculate: "'series-'+format(floor(datum.n/100),'02d')", as: 's' }],
  mark: { type: 'line' },
  encoding: {
    x: { field: 'n', type: 'quantitative' },
    y: { field: 'n', type: 'quantitative' },
    color: { field: 's', type: 'nominal' },
  },
});

describe('estimateDataDrivenCardinality (D-253 helper)', () => {
  it('reads a sequence length as the upper bound', () => {
    expect(estimateDataDrivenCardinality({ sequence: { start: 0, stop: 50, step: 1 } })).toBe(50);
    expect(estimateDataDrivenCardinality({ sequence: { start: 0, stop: 2000, step: 1 } })).toBe(2000);
    expect(estimateDataDrivenCardinality({ sequence: { start: 0, stop: 20, step: 2 } })).toBe(10);
  });
  it('reads an inline row count and returns 0 for data.url / junk', () => {
    expect(estimateDataDrivenCardinality({ values: [1, 2, 3] })).toBe(3);
    expect(estimateDataDrivenCardinality({ url: 'x.json' })).toBe(0);
    expect(estimateDataDrivenCardinality(null)).toBe(0);
  });
  it('surfaces the estimate through analyzeCategoricalColor for a data-driven channel', () => {
    // Direct distinct count is 0 (transform-derived) → estimate falls to seq len.
    expect(analyzeCategoricalColor(w2_12()).cardinality).toBe(0);
    expect(analyzeCategoricalColor(w2_12()).estimatedCardinality).toBe(50);
  });
});

describe('applyCategoricalPaletteFix — D-253 reaches a 50-series data-driven legend (both themes)', () => {
  for (const dark of [false, true]) {
    it(`${dark ? 'DARK' : 'LIGHT'}: 50-slot sequence gets an injective range covering all 50 series`, () => {
      const spec: any = w2_12();
      const applied = applyCategoricalPaletteFix(spec, dark);

      expect(applied).not.toBeNull();
      // Pre-fix this was exactly CATEGORY_EXTEND_TARGET (40) → 50 series recycled.
      expect(spec.config.range.category.length).toBeGreaterThanOrEqual(50);
      expect(spec.config.range.category.length).toBeLessThanOrEqual(MAX_ESTIMATED_CATEGORY);
      // Injective: no colour repeats within the range → every series distinct.
      expect(new Set(spec.config.range.category).size).toBe(spec.config.range.category.length);
      // Prefix still the active theme's own base → ≤10-series specs unchanged.
      const base = dark ? SATURATED_CATEGORY_10 : EXCEL_CATEGORY_10;
      expect(spec.config.range.category.slice(0, 10)).toEqual(base);
    });
  }

  it('huge sequence (w2-04, ~20 real series in 2000 rows) keeps the 40 default, not a 2000 range', () => {
    const spec: any = w2_04();
    const applied = applyCategoricalPaletteFix(spec, false);
    expect(applied).not.toBeNull();
    expect(spec.config.range.category).toHaveLength(CATEGORY_EXTEND_TARGET); // 40, covers 20 injectively
  });
});

// D-253 regression: the generated tail must stay PERCEPTUALLY distinct, not
// merely hex-unique. The old golden-angle tail dropped to a ~10 RGB min gap at
// a 40-entry tail (target 50) — two swatches read as the same colour, the
// perceptual "recycle" the renderer flagged. Even hue spacing keeps the tail
// well-separated. This asserts the DIRECTION: a floor the golden-angle tree
// fails (10 < 16) and the even-spaced tail clears, in BOTH themes.
describe('extendCategoricalPalette — D-253 tail stays perceptually distinct at 50 (both themes)', () => {
  const hexToRgb = (hex: string): [number, number, number] => {
    const h = hex.replace('#', '');
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  };
  const minPairwiseRgbDistance = (colors: string[]): number => {
    const rgb = colors.map(hexToRgb);
    let min = Infinity;
    for (let i = 0; i < rgb.length; i++) {
      for (let j = i + 1; j < rgb.length; j++) {
        const d = Math.hypot(rgb[i][0] - rgb[j][0], rgb[i][1] - rgb[j][1], rgb[i][2] - rgb[j][2]);
        if (d < min) min = d;
      }
    }
    return min;
  };

  for (const dark of [false, true]) {
    const base = dark ? SATURATED_CATEGORY_10 : EXCEL_CATEGORY_10;
    it(`${dark ? 'DARK' : 'LIGHT'}: 50-entry palette has no near-duplicate swatches`, () => {
      const palette = extendCategoricalPalette(base, 50, dark);
      expect(palette).toHaveLength(50);
      expect(new Set(palette).size).toBe(50); // hex-unique
      // Perceptual floor: the golden-angle tail bottomed out at ~10 here.
      const tail = palette.slice(base.length);
      expect(minPairwiseRgbDistance(tail)).toBeGreaterThan(16);
    });
  }
});
