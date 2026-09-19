/**
 * G-e2d5a2 / D-253 — data-driven categorical palette EXTENSION (both themes).
 *
 * D-253's own fix EXTENDS the theme's 10-colour range with a generated tail so
 * a data-driven channel with >10 series stays injective (no recycle). That
 * tail regressed TWICE as a CONTRAST failure: it used a fixed lightness that
 * ignores hue, so on the light card the yellow/lime band sank to ~1.7:1 and on
 * the #333 dark card the blue-violet band to ~1.9:1 — series rendered invisible
 * for THIN/SMALL-mark specs (w2-04 1px lines, w2-12 size-90 points) even though
 * every hex was unique.
 *
 * The targeted fix CLAMPS each generated tail colour's lightness (hue + sat
 * preserved) so it clears CATEGORY_CONTRAST_FLOOR on the ACTIVE theme canvas —
 * a theme-resolved value, not a swapped constant. The base PREFIX (the theme's
 * own range) is untouched, so ≤10-series and low-cardinality data-driven specs
 * are byte-identical and the sibling D-014/D-019/D-260 contracts hold; the base
 * palette's own muted contrast is a separate concern (D-260).
 *
 * Direction the pre-fix tree fails: it imports clampLightnessForContrast /
 * CATEGORY_CONTRAST_FLOOR (absent → module import fails), and the tail-contrast
 * assertions expect ≥3:1 where the unclamped tail sat at ~1.7:1. Both themes.
 */
import {
  applyCategoricalPaletteFix,
  analyzeCategoricalColor,
  estimateDataDrivenCardinality,
  extendCategoricalPalette,
  clampLightnessForContrast,
  hslToHex,
  contrastRatio,
  CATEGORY_CONTRAST_FLOOR,
  CATEGORY_EXTEND_TARGET,
  MAX_ESTIMATED_CATEGORY,
  EXCEL_CATEGORY_10,
  SATURATED_CATEGORY_10,
} from '../vegaRecovery';

const LIGHT_BG: [number, number, number] = [255, 255, 255];
const DARK_BG: [number, number, number] = [51, 51, 51];

const hexToRgb = (hex: string): [number, number, number] => {
  const h = hex.replace('#', '');
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
};
const worstContrast = (colors: string[], bg: [number, number, number]): number =>
  Math.min(...colors.map((c) => contrastRatio(hexToRgb(c), bg)));
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
  mark: { type: 'line', strokeWidth: 1 },
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
    expect(analyzeCategoricalColor(w2_12()).cardinality).toBe(0);
    expect(analyzeCategoricalColor(w2_12()).estimatedCardinality).toBe(50);
  });
});

describe('clampLightnessForContrast — theme-resolved lightness (both themes)', () => {
  it('LIGHT: darkens a low-contrast yellow to clear the floor on #fff (hue preserved)', () => {
    const s = 0.6, hue = 60, rawL = 0.55; // yellow at the light tier lightness
    expect(contrastRatio(hexToRgb(hslToHex(hue, s, rawL)), LIGHT_BG)).toBeLessThan(CATEGORY_CONTRAST_FLOOR);
    const l = clampLightnessForContrast(hue, s, rawL, false);
    expect(contrastRatio(hexToRgb(hslToHex(hue, s, l)), LIGHT_BG)).toBeGreaterThanOrEqual(3.0);
    expect(l).toBeLessThanOrEqual(rawL); // readable side on white is darker
  });
  it('DARK: lightens a low-contrast blue-violet to clear the floor on #333', () => {
    const s = 0.62, hue = 265, rawL = 0.54; // blue-violet at the dark tier lightness
    expect(contrastRatio(hexToRgb(hslToHex(hue, s, rawL)), DARK_BG)).toBeLessThan(CATEGORY_CONTRAST_FLOOR);
    const l = clampLightnessForContrast(hue, s, rawL, true);
    expect(contrastRatio(hexToRgb(hslToHex(hue, s, l)), DARK_BG)).toBeGreaterThanOrEqual(3.0);
    expect(l).toBeGreaterThanOrEqual(rawL); // readable side on #333 is lighter
  });
  it('leaves a colour already clearing the floor untouched', () => {
    const s = 0.7, hue = 0, rawL = 0.35; // deep red, already >3.2:1 on white
    if (contrastRatio(hexToRgb(hslToHex(hue, s, rawL)), LIGHT_BG) >= CATEGORY_CONTRAST_FLOOR) {
      expect(clampLightnessForContrast(hue, s, rawL, false)).toBe(rawL);
    }
  });
});

describe('extendCategoricalPalette — D-253 tail is contrast-safe AND injective (both themes)', () => {
  for (const dark of [false, true]) {
    const base = dark ? SATURATED_CATEGORY_10 : EXCEL_CATEGORY_10;
    const bg = dark ? DARK_BG : LIGHT_BG;
    it(`${dark ? 'DARK' : 'LIGHT'}: 50-entry palette — base prefix preserved, tail all >=3:1, injective`, () => {
      const pal = extendCategoricalPalette(base, 50, dark);
      expect(pal).toHaveLength(50);
      // Base PREFIX byte-identical → ≤10-series / low-card specs unchanged.
      expect(pal.slice(0, 10)).toEqual(base);
      // Injective — no colour repeats (D-253's original no-recycle guarantee).
      expect(new Set(pal).size).toBe(50);
      // The GENERATED tail is the part D-253's fix owns: every tail colour must
      // clear the floor (the unclamped tail bottomed out near 1.7:1 here).
      const tail = pal.slice(base.length);
      expect(worstContrast(tail, bg)).toBeGreaterThanOrEqual(3.0);
      // Tail stays perceptually separable (no near-duplicate swatches).
      expect(minPairwiseRgbDistance(tail)).toBeGreaterThan(12);
    });
  }
});

describe('applyCategoricalPaletteFix — D-253 sizes + contrast-clamps the extension (both themes)', () => {
  for (const dark of [false, true]) {
    const bg = dark ? DARK_BG : LIGHT_BG;
    const base = dark ? SATURATED_CATEGORY_10 : EXCEL_CATEGORY_10;

    it(`${dark ? 'DARK' : 'LIGHT'}: w2-12 (50-slot) — >=50 injective, base prefix intact, tail >=3:1`, () => {
      const spec: any = w2_12();
      const applied = applyCategoricalPaletteFix(spec, dark);
      expect(applied).not.toBeNull();
      const cat: string[] = spec.config.range.category;
      expect(cat.length).toBeGreaterThanOrEqual(50);
      expect(cat.length).toBeLessThanOrEqual(MAX_ESTIMATED_CATEGORY);
      expect(new Set(cat).size).toBe(cat.length); // injective — no recycle
      expect(cat.slice(0, 10)).toEqual(base);      // low-card contract preserved
      expect(worstContrast(cat.slice(10, 50), bg)).toBeGreaterThanOrEqual(3.0); // tail contrast
    });

    it(`${dark ? 'DARK' : 'LIGHT'}: w2-04 (20 series in 2000 rows) keeps the 40 default, tail >=3:1`, () => {
      const spec: any = w2_04();
      const applied = applyCategoricalPaletteFix(spec, dark);
      expect(applied).not.toBeNull();
      const cat: string[] = spec.config.range.category;
      expect(cat).toHaveLength(CATEGORY_EXTEND_TARGET); // huge seq → default 40
      expect(new Set(cat).size).toBe(cat.length);
      expect(worstContrast(cat.slice(10), bg)).toBeGreaterThanOrEqual(3.0);
    });
  }
});
