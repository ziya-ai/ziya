/**
 * D-258 (G-46c6a4) — low-opacity categorical marks dissolve in the LIGHT theme.
 *
 * Duplicate signature of D-014 / D-019 / D-260 ("low-opacity-marks-dissolve:light"):
 * the identical spec (vega-lite-w2-02) passes in the DARK theme (saturated
 * tableau10 fallback stays hue-separable at opacity 0.35) but fails in LIGHT,
 * where the muted 'excel' range.category composites onto #ffffff into
 * near-identical pale tints (measured between-group separability ~1.15:1),
 * collapsing the colour channel.
 *
 * The mechanism (applyCategoricalPaletteFix, cardinality-0 low-opacity branch)
 * already landed for the earlier duplicates; this suite pins D-258 to the
 * VERBATIM on-disk spec — all three calculate transforms plus the sequence
 * data, so the colour field 'g' is genuinely data-driven (cardinality 0,
 * unknowable statically) — and asserts the theme-fix contract in BOTH themes:
 * light (previously broken) now resolves the saturated base; dark (still
 * correct) is unchanged. It fails without the cardinality-0 low-opacity branch
 * (light would fall through to the muted excel base) and passes with it.
 */
import {
  applyCategoricalPaletteFix,
  liftLowOpacityLightCategorical,
  LIGHT_CATEGORY_OPACITY_FLOOR,
  SATURATED_CATEGORY_10,
  EXCEL_CATEGORY_10,
} from '../vegaRecovery';

// Minimal sRGB→Lab + ΔE(CIE76) so the test can assert the SEPARABILITY the fix
// is really about (WCAG luminance contrast is ~1.0 for equal-lightness hues and
// does not capture it). Composite a mark colour over a background at opacity a.
const hx = (h: string): [number, number, number] => {
  const s = h.replace('#', '');
  return [parseInt(s.slice(0, 2), 16), parseInt(s.slice(2, 4), 16), parseInt(s.slice(4, 6), 16)];
};
const comp = (c: number[], bg: number[], a: number): number[] =>
  [0, 1, 2].map((i) => bg[i] * (1 - a) + c[i] * a);
const lin = (v: number): number => {
  v /= 255;
  return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
};
const lab = (c: number[]): [number, number, number] => {
  const [r, g, b] = c.map(lin);
  const X = r * 0.4124 + g * 0.3576 + b * 0.1805;
  const Y = r * 0.2126 + g * 0.7152 + b * 0.0722;
  const Z = r * 0.0193 + g * 0.1192 + b * 0.9505;
  const f = (t: number): number => (t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116);
  const fx = f(X / 0.95047), fy = f(Y / 1.0), fz = f(Z / 1.08883);
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
};
const de = (a: number[], b: number[]): number =>
  Math.sqrt(a.reduce((s, _, i) => s + (a[i] - b[i]) ** 2, 0));
const minGroupDE = (pal: string[], bg: number[], a: number): number => {
  const L = pal.map((c) => lab(comp(hx(c), bg, a)));
  let m = Infinity;
  for (let i = 0; i < L.length; i++)
    for (let j = i + 1; j < L.length; j++) m = Math.min(m, de(L[i], L[j]));
  return m;
};
const WHITE = [255, 255, 255], BLACK = [0, 0, 0];

// The exact definition stored at .ziya/gfx-sweep/specs/vega-lite/vega-lite-w2-02.json.
const w2_02 = () => ({
  $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
  title: '5000-point scatter',
  data: { sequence: { start: 0, stop: 5000, step: 1, as: 'n' } },
  transform: [
    { calculate: '50+18*sin(datum.n*2.399)+6*cos(datum.n*0.7)', as: 'x' },
    { calculate: '50+18*cos(datum.n*1.618)+6*sin(datum.n*0.31)', as: 'y' },
    { calculate: "'g'+(datum.n%5)", as: 'g' },
  ],
  mark: { type: 'point', filled: true, size: 12, opacity: 0.35 },
  height: 420,
  encoding: {
    x: { field: 'x', type: 'quantitative' },
    y: { field: 'y', type: 'quantitative' },
    color: { field: 'g', type: 'nominal' },
  },
});

describe('D-258 vega-lite-w2-02 low-opacity scatter theme parity', () => {
  it('LIGHT (previously broken): injects the saturated base so groups stay separable on #fff', () => {
    const spec: any = w2_02();
    const palette = applyCategoricalPaletteFix(spec, /* isDarkMode */ false);

    expect(palette).not.toBeNull();
    // The dissolving muted excel range must NOT be the base…
    expect(palette!.slice(0, EXCEL_CATEGORY_10.length)).not.toEqual(EXCEL_CATEGORY_10);
    // …the saturated palette is, matching what dark already used.
    expect(palette!.slice(0, SATURATED_CATEGORY_10.length)).toEqual(SATURATED_CATEGORY_10);
    expect(spec.config.range.category).toEqual(palette);
  });

  it('DARK (still correct): identical spec keeps the saturated base — parity preserved', () => {
    const spec: any = w2_02();
    const palette = applyCategoricalPaletteFix(spec, /* isDarkMode */ true);

    expect(palette).not.toBeNull();
    expect(palette!.slice(0, SATURATED_CATEGORY_10.length)).toEqual(SATURATED_CATEGORY_10);
  });

  // Regression completion: the saturated palette alone keeps the LEGEND
  // separable, but the PLOT still washes together on the light canvas — white
  // compositing at opacity 0.35 compresses min between-group ΔE to 12.4 (dark
  // reaches 16.6 on black). The opacity floor is what carries the plot.
  it('LIGHT: lifts the low mark opacity to the floor so groups survive on #fff', () => {
    const spec: any = w2_02();
    expect(spec.mark.opacity).toBe(0.35); // authored, below the floor
    const applied = liftLowOpacityLightCategorical(spec, /* isDarkMode */ false);
    expect(applied).toBe(LIGHT_CATEGORY_OPACITY_FLOOR);
    expect(spec.mark.opacity).toBe(LIGHT_CATEGORY_OPACITY_FLOOR);
  });

  it('DARK: never touches opacity — the dark theme already survives at 0.35', () => {
    const spec: any = w2_02();
    const applied = liftLowOpacityLightCategorical(spec, /* isDarkMode */ true);
    expect(applied).toBeNull();
    expect(spec.mark.opacity).toBe(0.35); // unchanged — themes stay a matched pair
  });

  it('LIGHT: saturated base + opacity floor restores dark-parity between-group ΔE on #fff', () => {
    const groups = SATURATED_CATEGORY_10.slice(0, 5); // g0..g4 map to the base prefix
    const darkRef = minGroupDE(groups, BLACK, 0.35); // dark passes at the authored opacity

    // Muted excel at the authored opacity: the original dissolve (well below dark).
    const mutedLight = minGroupDE(EXCEL_CATEGORY_10.slice(0, 5), WHITE, 0.35);
    // Saturated at the authored opacity: better, but still short of dark parity.
    const satLightAuthored = minGroupDE(groups, WHITE, 0.35);
    // Saturated at the lifted floor: reaches dark parity.
    const satLightFixed = minGroupDE(groups, WHITE, LIGHT_CATEGORY_OPACITY_FLOOR);

    expect(satLightAuthored).toBeGreaterThan(mutedLight);        // palette fix helps…
    expect(satLightAuthored).toBeLessThan(darkRef);              // …but is not enough alone
    expect(satLightFixed).toBeGreaterThanOrEqual(darkRef * 0.95); // floor closes the gap
  });
});
