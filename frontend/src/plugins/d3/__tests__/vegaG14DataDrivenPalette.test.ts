/**
 * G-14 / D-014 — vega-family categorical palette recycles past 10.
 *
 * The full-Vega native path already stops an ordinal {scheme} from recycling a
 * data-driven domain (extendRecycledOrdinalSchemes, D-282, vegaG54.test.ts).
 * The Vega-LITE path (applyCategoricalPaletteFix, D-265/D-260, vegaG72.test.ts)
 * only fired when the colour field's cardinality was KNOWABLE statically —
 * from an explicit scale.domain or inline data.values. The four failing VL
 * specs (vega-lite-w2-04/11/12/13) all build their colour field from a
 * `data.sequence` + `transform` calculate, so analyzeCategoricalColor reports
 * cardinality 0 and the pre-fix code left them on the 10-entry theme range,
 * recycling hues for 20/30/40/50 series.
 *
 * This exercises the new unknown-cardinality branch: it injects a range that
 * BEGINS with the active theme's own 10 colours (so any genuine ≤10-series
 * spec is byte-identical) and stays injective up to CATEGORY_EXTEND_TARGET.
 *
 * Direction: this file imports EXCEL_CATEGORY_10 / extendCategoricalPalette /
 * CATEGORY_EXTEND_TARGET, which did NOT exist pre-fix (module import fails),
 * and the applied-palette assertions expect a non-null 40-entry range where
 * the unpatched code returned null and set nothing. Both themes are asserted:
 * light must now be fixed AND dark must stay fixed.
 */
import {
  applyCategoricalPaletteFix,
  extendCategoricalPalette,
  generateCategoricalPalette,
  SATURATED_CATEGORY_10,
  EXCEL_CATEGORY_10,
  CATEGORY_EXTEND_TARGET,
} from '../vegaRecovery';

// A data-driven VL spec mirroring vega-lite-w2-04: colour field produced by a
// sequence + calculate, no inline data.values, no explicit scale — cardinality
// is unknowable statically.
const dataDrivenSpec = (markOpacity?: number) => ({
  $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
  data: { sequence: { start: 0, stop: 2000, step: 1, as: 'n' } },
  transform: [
    { calculate: 'datum.n%100', as: 't' },
    { calculate: 'floor(datum.n/100)', as: 'si' },
    { calculate: "'series-'+format(floor(datum.n/100),'02d')", as: 's' },
  ],
  mark: markOpacity === undefined ? { type: 'line' } : { type: 'point', opacity: markOpacity },
  encoding: {
    x: { field: 't', type: 'quantitative' },
    y: { field: 'si', type: 'quantitative' },
    color: { field: 's', type: 'nominal' },
  },
});

describe('extendCategoricalPalette (D-014 helper)', () => {
  for (const dark of [false, true]) {
    const base = dark ? SATURATED_CATEGORY_10 : EXCEL_CATEGORY_10;
    it(`dark=${dark}: preserves the base prefix EXACTLY and reaches the target`, () => {
      const pal = extendCategoricalPalette(base, CATEGORY_EXTEND_TARGET, dark);
      expect(pal).toHaveLength(CATEGORY_EXTEND_TARGET);
      expect(pal.slice(0, 10)).toEqual(base); // ≤10 series → byte-identical
    });
    it(`dark=${dark}: every entry is a distinct #rrggbb (no recycle to 40)`, () => {
      const pal = extendCategoricalPalette(base, CATEGORY_EXTEND_TARGET, dark);
      pal.forEach((c) => expect(c).toMatch(/^#[0-9a-f]{6}$/));
      expect(new Set(pal).size).toBe(CATEGORY_EXTEND_TARGET);
    });
  }
});

describe('applyCategoricalPaletteFix — D-014 data-driven cardinality (both themes)', () => {
  for (const dark of [false, true]) {
    it(`${dark ? 'DARK' : 'LIGHT'}: a data-driven categorical channel gets an injective, prefix-preserving range`, () => {
      const spec: any = dataDrivenSpec();
      // Pre-fix direction: nothing has set a category range yet.
      expect(spec.config?.range?.category).toBeUndefined();

      const applied = applyCategoricalPaletteFix(spec, dark);

      // Pre-fix this returned null (cardinality 0 hit no branch) → this fails
      // against the unpatched tree.
      expect(applied).not.toBeNull();
      expect(spec.config.range.category).toHaveLength(CATEGORY_EXTEND_TARGET);
      expect(new Set(spec.config.range.category).size).toBe(CATEGORY_EXTEND_TARGET); // injective
      // Output-preserving: the first 10 are the active theme's own base, so a
      // genuine ≤10-series data-driven spec renders exactly as before.
      const base = dark ? SATURATED_CATEGORY_10 : EXCEL_CATEGORY_10;
      expect(spec.config.range.category.slice(0, 10)).toEqual(base);
    });
  }

  it('LIGHT + low mark opacity: uses the SATURATED base (D-260) yet still extends (D-265)', () => {
    const spec: any = dataDrivenSpec(0.35);
    const applied = applyCategoricalPaletteFix(spec, /*isDarkMode*/ false);
    expect(applied).not.toBeNull();
    expect(spec.config.range.category).toHaveLength(CATEGORY_EXTEND_TARGET);
    // Muted excel would dissolve at 0.35 over white → prefix must be saturated.
    expect(spec.config.range.category.slice(0, 10)).toEqual(SATURATED_CATEGORY_10);
  });
});

describe('applyCategoricalPaletteFix — D-014 does not over-fire on known cardinality', () => {
  it('≤10 series with INLINE data (known cardinality) still returns null in light', () => {
    const spec: any = {
      mark: 'bar',
      data: { values: [{ g: 'a', y: 1 }, { g: 'b', y: 2 }, { g: 'c', y: 3 }] },
      encoding: { color: { field: 'g', type: 'nominal' } },
    };
    expect(applyCategoricalPaletteFix(spec, false)).toBeNull();
    expect(spec.config?.range?.category).toBeUndefined();
  });

  it('>10 series with INLINE data still uses the sized generated palette, not the 40-cap extend', () => {
    const n = 20;
    const spec: any = {
      mark: 'line',
      data: { values: Array.from({ length: n * 2 }, (_, i) => ({ s: `s${i % n}`, x: i, y: i })) },
      encoding: { color: { field: 's', type: 'nominal' } },
    };
    const applied = applyCategoricalPaletteFix(spec, false);
    expect(applied).toEqual(generateCategoricalPalette(n, false));
    expect(spec.config.range.category).toHaveLength(n); // 20, not CATEGORY_EXTEND_TARGET
  });

  it('respects an author-supplied config.range.category even when data-driven', () => {
    const spec: any = dataDrivenSpec();
    spec.config = { range: { category: ['#abc', '#def'] } };
    expect(applyCategoricalPaletteFix(spec, false)).toBeNull();
    expect(spec.config.range.category).toEqual(['#abc', '#def']);
  });

  it('respects an author-supplied colour scheme even when data-driven', () => {
    const spec: any = dataDrivenSpec();
    spec.encoding.color.scale = { scheme: 'category20' };
    expect(applyCategoricalPaletteFix(spec, false)).toBeNull();
    expect(spec.config?.range?.category).toBeUndefined();
  });
});
