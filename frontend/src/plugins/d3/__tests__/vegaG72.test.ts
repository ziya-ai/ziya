/**
 * G-72 Vega-Lite categorical-palette regression tests.
 *
 * Two defects share the theme category palette as their root cause and are
 * fixed by exported pure helpers in vegaRecovery.ts (so the tests exercise the
 * ACTUAL code path):
 *   - D-265 (structural, BOTH themes): categorical encoding stops being
 *     injective past 10 series because both active palettes are 10 long
 *     (light 'excel' explicit range.category, dark 'tableau10' fallback), so
 *     12/20/30 series alias / repeat. Fix: generateCategoricalPalette gives an
 *     injective range sized to the domain, biased to the active canvas.
 *   - D-260 (theme, LIGHT only): the muted excel range dissolves to near-
 *     identical pale tints at low mark opacity over #fff (ΔE≈8 between groups
 *     at opacity 0.35); dark passes with saturated tableau10 (ΔE≈12). Fix:
 *     applyCategoricalPaletteFix swaps in SATURATED_CATEGORY_10 in light only.
 *
 * Direction: every assertion is written so it FAILS against the pre-fix code
 * (which set no config.range.category at all). The D-260 case, being a theme
 * defect, asserts BOTH themes: light is now fixed AND dark is left untouched.
 */

import {
  SATURATED_CATEGORY_10,
  hslToHex,
  generateCategoricalPalette,
  analyzeCategoricalColor,
  applyCategoricalPaletteFix,
} from '../vegaRecovery';

// ── local WCAG + CIELAB helpers (independent of the module under test) ──────
function h2r(h: string): [number, number, number] {
  const s = h.replace('#', '');
  return [parseInt(s.slice(0, 2), 16), parseInt(s.slice(2, 4), 16), parseInt(s.slice(4, 6), 16)];
}
function comp(fg: [number, number, number], bg: [number, number, number], a: number): [number, number, number] {
  return [a * fg[0] + (1 - a) * bg[0], a * fg[1] + (1 - a) * bg[1], a * fg[2] + (1 - a) * bg[2]];
}
function srgb2lin(v: number): number {
  const c = v / 255;
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}
function lab([r, g, b]: [number, number, number]): [number, number, number] {
  const rl = srgb2lin(r), gl = srgb2lin(g), bl = srgb2lin(b);
  const X = rl * 0.4124 + gl * 0.3576 + bl * 0.1805;
  const Y = rl * 0.2126 + gl * 0.7152 + bl * 0.0722;
  const Z = rl * 0.0193 + gl * 0.1192 + bl * 0.9505;
  const f = (t: number) => (t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116);
  const fx = f(X / 0.95047), fy = f(Y / 1.0), fz = f(Z / 1.08883);
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
}
function deltaE(a: string, b: string): number {
  const [l1, a1, b1] = lab(h2r(a)), [l2, a2, b2] = lab(h2r(b));
  return Math.sqrt((l1 - l2) ** 2 + (a1 - a2) ** 2 + (b1 - b2) ** 2);
}
function minPairDeltaE(pal: string[], opacity = 1, bg: [number, number, number] = [255, 255, 255]): number {
  const hexes = pal.map(c => {
    const cc = comp(h2r(c), bg, opacity);
    const to = (v: number) => Math.round(v).toString(16).padStart(2, '0');
    return `#${to(cc[0])}${to(cc[1])}${to(cc[2])}`;
  });
  let mn = Infinity;
  for (let i = 0; i < hexes.length; i++)
    for (let j = i + 1; j < hexes.length; j++)
      mn = Math.min(mn, deltaE(hexes[i], hexes[j]));
  return mn;
}

// The muted light 'excel' theme range.category (from vega-themes theme-excel).
const EXCEL_CATEGORY_10 = [
  '#4572a7', '#aa4643', '#8aa453', '#71598e', '#4598ae',
  '#d98445', '#94aace', '#d09393', '#b9cc98', '#a99cbc',
];

// ── hslToHex sanity ─────────────────────────────────────────────────────────
describe('hslToHex', () => {
  it('produces valid 6-digit hex and known anchors', () => {
    expect(hslToHex(0, 1, 0.5)).toBe('#ff0000');
    expect(hslToHex(120, 1, 0.5)).toBe('#00ff00');
    expect(hslToHex(240, 1, 0.5)).toBe('#0000ff');
    expect(hslToHex(400, 0.7, 0.5)).toMatch(/^#[0-9a-f]{6}$/); // hue wraps
  });
});

// ── D-265: injective generated palette (both themes) ────────────────────────
describe('generateCategoricalPalette (D-265)', () => {
  for (const dark of [false, true]) {
    for (const n of [11, 20, 30]) {
      it(`n=${n} dark=${dark}: returns exactly n colours, all distinct`, () => {
        const pal = generateCategoricalPalette(n, dark);
        expect(pal).toHaveLength(n);
        expect(pal.every(c => /^#[0-9a-f]{6}$/.test(c))).toBe(true);
        // injective: no repeated colour (the pre-fix 10-entry palette repeats
        // at index 10 — 10 % 10 === 0 — the exact aliasing this removes).
        expect(new Set(pal).size).toBe(n);
      });
      it(`n=${n} dark=${dark}: hues are perceptually separable (min ΔE well above confusable)`, () => {
        const pal = generateCategoricalPalette(n, dark);
        // ΔE < ~5 = confusable; the generated palette clears that comfortably.
        expect(minPairDeltaE(pal)).toBeGreaterThan(6);
      });
    }
  }

  it('theme bias: dark palette is lighter on average than the light palette', () => {
    const avgLum = (pal: string[]) =>
      pal.reduce((s, c) => {
        const [r, g, b] = h2r(c);
        return s + (0.2126 * srgb2lin(r) + 0.7152 * srgb2lin(g) + 0.0722 * srgb2lin(b));
      }, 0) / pal.length;
    expect(avgLum(generateCategoricalPalette(12, true)))
      .toBeGreaterThan(avgLum(generateCategoricalPalette(12, false)));
  });
});

// ── analyzeCategoricalColor ─────────────────────────────────────────────────
describe('analyzeCategoricalColor', () => {
  it('counts distinct field values for a nominal colour', () => {
    const rows = Array.from({ length: 30 }, (_, i) => ({ series: `s${i % 12}`, y: i }));
    const spec = { mark: 'line', data: { values: rows }, encoding: { color: { field: 'series', type: 'nominal' } } };
    const info = analyzeCategoricalColor(spec);
    expect(info.isCategorical).toBe(true);
    expect(info.cardinality).toBe(12);
    expect(info.hasExplicitColors).toBe(false);
  });

  it('uses explicit scale.domain length when present', () => {
    const spec = {
      mark: 'bar',
      encoding: { color: { field: 'g', type: 'nominal', scale: { domain: Array.from({ length: 15 }, (_, i) => `${i}`) } } },
    };
    expect(analyzeCategoricalColor(spec).cardinality).toBe(15);
  });

  it('flags an author-pinned scheme/range as explicit', () => {
    const scheme = { mark: 'bar', encoding: { color: { field: 'g', type: 'nominal', scale: { scheme: 'viridis' } } } };
    const range = { mark: 'bar', encoding: { color: { field: 'g', type: 'nominal', scale: { range: ['#111', '#222'] } } } };
    expect(analyzeCategoricalColor(scheme).hasExplicitColors).toBe(true);
    expect(analyzeCategoricalColor(range).hasExplicitColors).toBe(true);
  });

  it('does not treat a quantitative colour as categorical', () => {
    const spec = { mark: 'point', encoding: { color: { field: 'v', type: 'quantitative' } } };
    expect(analyzeCategoricalColor(spec).isCategorical).toBe(false);
  });

  it('reads mark.opacity and opacity encoding value', () => {
    const m = { mark: { type: 'point', opacity: 0.35 }, encoding: { color: { field: 'g', type: 'nominal' } } };
    const e = { mark: 'point', encoding: { color: { field: 'g', type: 'nominal' }, opacity: { value: 0.4 } } };
    expect(analyzeCategoricalColor(m).opacity).toBeCloseTo(0.35);
    expect(analyzeCategoricalColor(e).opacity).toBeCloseTo(0.4);
  });
});

// ── D-265 applied: >10 series get an injective range in BOTH themes ─────────
describe('applyCategoricalPaletteFix — D-265 (>10 series, both themes)', () => {
  const build = (n: number) => ({
    mark: 'line',
    data: { values: Array.from({ length: n * 2 }, (_, i) => ({ series: `s${i % n}`, x: i, y: i })) },
    encoding: { color: { field: 'series', type: 'nominal' } },
  });

  for (const dark of [false, true]) {
    it(`dark=${dark}: 20 series → injective range of length 20 (pre-fix: none, aliased to 10)`, () => {
      const spec: any = build(20);
      // Pre-fix state (direction): nothing sets a category range.
      expect(spec.config?.range?.category).toBeUndefined();
      const applied = applyCategoricalPaletteFix(spec, dark);
      expect(applied).not.toBeNull();
      expect(spec.config.range.category).toHaveLength(20);
      expect(new Set(spec.config.range.category).size).toBe(20); // injective
      // The 10-entry theme palettes would have collapsed series 10..19 onto
      // 0..9; assert the fix is strictly longer than that ceiling.
      expect(spec.config.range.category.length).toBeGreaterThan(SATURATED_CATEGORY_10.length);
    });
  }

  it('30 stacked bands → 30 distinct colours (w2-13)', () => {
    const spec: any = build(30);
    applyCategoricalPaletteFix(spec, false);
    expect(spec.config.range.category).toHaveLength(30);
    expect(new Set(spec.config.range.category).size).toBe(30);
  });
});

// ── D-260 applied: THEME defect — assert BOTH themes ────────────────────────
describe('applyCategoricalPaletteFix — D-260 (low-opacity light dissolve)', () => {
  // w2-02: 5 groups, 5000 points at opacity 0.35.
  const build = () => ({
    mark: { type: 'point', opacity: 0.35 },
    data: { values: Array.from({ length: 50 }, (_, i) => ({ g: `g${i % 5}`, x: i, y: i })) },
    encoding: { color: { field: 'g', type: 'nominal' } },
  });

  it('LIGHT (broken theme) now uses the saturated tableau10 range, not the muted excel one', () => {
    const spec: any = build();
    const applied = applyCategoricalPaletteFix(spec, /*isDarkMode*/ false);
    expect(applied).toEqual(SATURATED_CATEGORY_10);
    expect(spec.config.range.category).toEqual(SATURATED_CATEGORY_10);
    // and it is genuinely NOT the muted palette that was failing:
    expect(spec.config.range.category).not.toEqual(EXCEL_CATEGORY_10);
  });

  it('DARK (other theme) is left untouched — no regression', () => {
    const spec: any = build();
    const applied = applyCategoricalPaletteFix(spec, /*isDarkMode*/ true);
    // Dark already passes (tableau10 fallback); the fix must not inject here.
    expect(applied).toBeNull();
    expect(spec.config?.range?.category).toBeUndefined();
  });

  it('separability: saturated palette beats the muted excel palette at opacity 0.35 over #fff', () => {
    // The heart of D-260: hues must stay apart when composited. Direction check
    // — the muted palette (pre-fix) is measurably worse than the fix palette.
    const muted5 = minPairDeltaE(EXCEL_CATEGORY_10.slice(0, 5), 0.35);
    const sat5 = minPairDeltaE(SATURATED_CATEGORY_10.slice(0, 5), 0.35);
    expect(sat5).toBeGreaterThan(muted5);
    expect(sat5).toBeGreaterThan(10); // clearly distinguishable
  });
});

// ── no-op guards: never override author intent or unrelated specs ───────────
describe('applyCategoricalPaletteFix — guards', () => {
  it('does not fire for ≤10 series at full opacity in light (unrelated output unchanged)', () => {
    const spec: any = {
      mark: 'bar',
      data: { values: [{ g: 'a', y: 1 }, { g: 'b', y: 2 }, { g: 'c', y: 3 }] },
      encoding: { color: { field: 'g', type: 'nominal' } },
    };
    expect(applyCategoricalPaletteFix(spec, false)).toBeNull();
    expect(spec.config?.range?.category).toBeUndefined();
  });

  it('does not fire in dark for ≤10 series (tableau10 fallback is fine)', () => {
    const spec: any = {
      mark: { type: 'point', opacity: 0.3 },
      data: { values: Array.from({ length: 20 }, (_, i) => ({ g: `g${i % 5}`, y: i })) },
      encoding: { color: { field: 'g', type: 'nominal' } },
    };
    expect(applyCategoricalPaletteFix(spec, true)).toBeNull();
  });

  it('respects an author-supplied scheme', () => {
    const spec: any = {
      mark: 'line',
      data: { values: Array.from({ length: 24 }, (_, i) => ({ s: `s${i % 12}`, y: i })) },
      encoding: { color: { field: 's', type: 'nominal', scale: { scheme: 'category20' } } },
    };
    expect(applyCategoricalPaletteFix(spec, false)).toBeNull();
    expect(spec.config?.range?.category).toBeUndefined();
  });

  it('respects an author-supplied config.range.category', () => {
    const spec: any = {
      mark: 'line',
      data: { values: Array.from({ length: 24 }, (_, i) => ({ s: `s${i % 12}`, y: i })) },
      encoding: { color: { field: 's', type: 'nominal' } },
      config: { range: { category: ['#abc', '#def'] } },
    };
    expect(applyCategoricalPaletteFix(spec, false)).toBeNull();
    expect(spec.config.range.category).toEqual(['#abc', '#def']);
  });

  it('does not fire for a quantitative colour ramp', () => {
    const spec: any = {
      mark: 'rect',
      data: { values: Array.from({ length: 30 }, (_, i) => ({ v: i })) },
      encoding: { color: { field: 'v', type: 'quantitative' } },
    };
    expect(applyCategoricalPaletteFix(spec, false)).toBeNull();
  });
});
