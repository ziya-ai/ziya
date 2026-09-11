/**
 * G-17 / D-019 — low-opacity categorical palette dissolves in LIGHT theme.
 *
 * At mark opacity 0.35 the muted light 'excel' category range composites onto
 * white into near-identical pale tints, destroying the colour channel; dark's
 * saturated tableau10 stays separable at the same opacity. The failing spec
 * (vega-lite-w2-02) is DATA-DRIVEN (data.sequence, colour = n%5), so its
 * cardinality is 0 (unknowable statically) — the branch that must fire is the
 * cardinality-0 path, which for a low-opacity light canvas selects the
 * SATURATED base rather than the muted excel base.
 *
 * This mechanism already landed under D-014 / D-260; this suite is the
 * BOTH-THEME regression guard for it, per the theme-fix contract: one assertion
 * that the previously-broken theme (light) is now correct, paired with one that
 * the other theme (dark) still is. It is green on the current tree by design —
 * D-019 required no NEW source change, only confirmation that the shared
 * palette path covers the data-driven low-opacity case.
 */
import {
  applyCategoricalPaletteFix,
  SATURATED_CATEGORY_10,
  EXCEL_CATEGORY_10,
  CATEGORY_LOW_OPACITY,
} from '../vegaRecovery';

// Reduced form of vega-lite-w2-02: 5 colour groups, opacity 0.35, sequence data
// so the colour field's cardinality cannot be counted statically.
const lowOpacityScatter = () => ({
  $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
  data: { sequence: { start: 0, stop: 5000, step: 1, as: 'n' } },
  transform: [{ calculate: "'g'+(datum.n%5)", as: 'g' }],
  mark: { type: 'point', filled: true, size: 12, opacity: 0.35 },
  height: 420,
  encoding: {
    x: { field: 'x', type: 'quantitative' },
    y: { field: 'y', type: 'quantitative' },
    color: { field: 'g', type: 'nominal' },
  },
});

describe('G-17 D-019 low-opacity categorical palette parity', () => {
  it('sanity: the mark opacity is genuinely below the dissolve threshold', () => {
    expect(0.35).toBeLessThan(CATEGORY_LOW_OPACITY);
  });

  it('LIGHT (previously broken): uses the SATURATED base, not the muted excel base', () => {
    const spec: any = lowOpacityScatter();
    const palette = applyCategoricalPaletteFix(spec, /* isDarkMode */ false);

    expect(palette).not.toBeNull();
    // The injected range must BEGIN with the saturated palette so the five
    // groups stay separable when composited onto white at opacity 0.35 —
    // the muted excel base is exactly what dissolved.
    expect(palette!.slice(0, SATURATED_CATEGORY_10.length)).toEqual(SATURATED_CATEGORY_10);
    expect(palette!.slice(0, EXCEL_CATEGORY_10.length)).not.toEqual(EXCEL_CATEGORY_10);
    expect(spec.config.range.category).toEqual(palette);
  });

  it('DARK (still correct): also uses the SATURATED base — theme parity preserved', () => {
    const spec: any = lowOpacityScatter();
    const palette = applyCategoricalPaletteFix(spec, /* isDarkMode */ true);

    expect(palette).not.toBeNull();
    expect(palette!.slice(0, SATURATED_CATEGORY_10.length)).toEqual(SATURATED_CATEGORY_10);
  });

  it('at FULL opacity in light the muted excel base is still used (no needless change)', () => {
    // Guards that the saturated swap is scoped to the low-opacity failure and
    // does not repaint every ordinary light-theme categorical chart.
    const spec: any = lowOpacityScatter();
    spec.mark.opacity = 1;
    const palette = applyCategoricalPaletteFix(spec, false);

    expect(palette).not.toBeNull();
    expect(palette!.slice(0, EXCEL_CATEGORY_10.length)).toEqual(EXCEL_CATEGORY_10);
  });
});
