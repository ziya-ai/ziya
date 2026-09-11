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
  SATURATED_CATEGORY_10,
  EXCEL_CATEGORY_10,
} from '../vegaRecovery';

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
});
