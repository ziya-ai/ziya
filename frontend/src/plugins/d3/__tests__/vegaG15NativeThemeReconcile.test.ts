/**
 * G-15 / D-015 — native Vega path skips theme colour reconciliation.
 *
 * vegaRecovery.ts owns reconcileThemeColors (D-257) + reconcileBackground
 * (D-258) + the guide-colour (labelColor/titleColor/tickColor/domainColor/
 * gridColor) below-floor walk. Those were wired ONLY into vegaLitePlugin, so a
 * FULL-VEGA spec's authored guide/background colours passed through unadapted:
 *   - vega-w4-11 dark: authored `background:'#fff'` honoured verbatim while the
 *     dark theme whitens guides -> white-on-white 1.00:1 (guides vanish).
 *   - vega-w4-12 dark: axis `labelColor:'rgb(70,70,70)'` (1.34:1 on the #333
 *     dark panel) and `gridColor:'rgba(0,0,0,0.12)'` (1.66:1) stay unreadable.
 *
 * FIX: vegaPlugin now exports `reconcileVegaGuideColors` and calls it in
 * render(), routing the SAME shared reconciler onto the native path.
 *
 * DIRECTION GUARANTEE: this file imports `reconcileVegaGuideColors` from
 * ../vegaPlugin, which does NOT exist on the unpatched tree (module symbol
 * absent -> import is undefined -> the applied-behaviour assertions throw). A
 * pure test of reconcileThemeColors alone would pass against unpatched code
 * (the helper already exists) and would certify the bug, not the wiring — so
 * the new native-path wrapper is what is asserted here.
 *
 * BOTH THEMES asserted per the theme-fix contract: for every colour touched,
 * one assertion that the BROKEN (dark) theme is now readable, PAIRED with one
 * that the OTHER (light) theme is unchanged and still legible — the guard
 * against swapping one hardcoded colour for another that fixes dark and breaks
 * light.
 */
import { reconcileVegaGuideColors } from '../vegaPlugin';
import { resolveColorToRgb, contrastRatio } from '../vegaRecovery';

const DARK: [number, number, number] = [51, 51, 51]; // vega-embed 'dark' panel
const LIGHT: [number, number, number] = [255, 255, 255];

/** Contrast of a spec colour string against a resolved background. */
const cr = (color: string, bg: [number, number, number]): number => {
  const rgb = resolveColorToRgb(color);
  if (!rgb) return NaN;
  return contrastRatio(rgb, bg);
};

// vega-w4-11 shape: native spec with an authored light top-level background.
const w4_11 = () => ({
  $schema: 'https://vega.github.io/schema/vega/v5.json',
  width: 380,
  height: 200,
  background: '#fff',
  data: [{ name: 't', values: [{ c: 'a', v: 30 }] }],
  scales: [{ name: 'x', type: 'band', domain: { data: 't', field: 'c' }, range: 'width' }],
  axes: [{ orient: 'bottom', scale: 'x' }],
  marks: [{ type: 'rect', from: { data: 't' }, encode: { update: {} } }],
});

// vega-w4-12 shape: native spec with authored rgb()/rgba() guide colours.
const w4_12 = () => ({
  $schema: 'https://vega.github.io/schema/vega/v5.json',
  width: 380,
  height: 200,
  data: [{ name: 't', values: [{ c: 'a', v: 30 }] }],
  scales: [{ name: 'x', type: 'band', domain: { data: 't', field: 'c' }, range: 'width' }],
  axes: [
    { orient: 'bottom', scale: 'x', labelColor: 'rgb(70, 70, 70)' },
    { orient: 'left', scale: 'y', grid: true, gridColor: 'rgba(0, 0, 0, 0.12)' },
  ],
  marks: [{ type: 'rect', from: { data: 't' }, encode: { update: {} } }],
});

describe('G-15 / D-015 reconcileVegaGuideColors — native Vega theme reconciliation', () => {
  it('is exported from vegaPlugin (proves the reconciler is wired into the native path)', () => {
    expect(typeof reconcileVegaGuideColors).toBe('function');
  });

  describe('w4-11 authored light background', () => {
    it('DARK: drops the light `#fff` background so the dark panel + its guides regain contrast', () => {
      const spec = reconcileVegaGuideColors(w4_11(), true);
      // Broken theme now correct: the white-on-white slab is gone.
      expect(spec.background).toBeUndefined();
    });

    it('LIGHT: keeps the `#fff` background (correct for the light canvas — no over-correction)', () => {
      const spec = reconcileVegaGuideColors(w4_11(), false);
      // Other theme still correct: a light bg under the light theme is legitimate.
      expect(spec.background).toBe('#fff');
    });
  });

  describe('w4-12 authored rgb()/rgba() guide colours', () => {
    it('DARK: nudges the sub-floor axis labelColor and gridColor to >=3:1 on the #333 panel', () => {
      const spec = reconcileVegaGuideColors(w4_12(), true);
      const labelColor = spec.axes[0].labelColor as string;
      const gridColor = spec.axes[1].gridColor as string;
      // Pre-fix values (1.34:1 / 1.66:1 on #333) must have been rewritten...
      expect(labelColor).not.toBe('rgb(70, 70, 70)');
      expect(gridColor).not.toBe('rgba(0, 0, 0, 0.12)');
      // ...to something that clears the 3:1 graphical floor on the dark panel.
      expect(cr(labelColor, DARK)).toBeGreaterThanOrEqual(3);
      expect(cr(gridColor, DARK)).toBeGreaterThanOrEqual(3);
    });

    it('LIGHT: leaves the already-legible guide colours untouched (no dark-fix regression)', () => {
      const spec = reconcileVegaGuideColors(w4_12(), false);
      // Other theme still correct: rgb(70,70,70)=9.44:1 and rgba(0,0,0,.12)~21:1
      // on white are both above the floor, so the reconciler must NOT rewrite
      // them — this is the swap-one-constant guard.
      expect(spec.axes[0].labelColor).toBe('rgb(70, 70, 70)');
      expect(spec.axes[1].gridColor).toBe('rgba(0, 0, 0, 0.12)');
      expect(cr('rgb(70, 70, 70)', LIGHT)).toBeGreaterThanOrEqual(3);
    });
  });

  describe('themed default text-mark fill (config.text.fill) applied on both themes', () => {
    it('DARK: sets a readable default text fill (>=4.5:1 on the #333 panel)', () => {
      const spec = reconcileVegaGuideColors(w4_11(), true);
      const fill = spec.config?.text?.fill as string;
      expect(typeof fill).toBe('string');
      expect(cr(fill, DARK)).toBeGreaterThanOrEqual(4.5);
    });

    it('LIGHT: sets a readable default text fill (>=4.5:1 on white)', () => {
      const spec = reconcileVegaGuideColors(w4_11(), false);
      const fill = spec.config?.text?.fill as string;
      expect(typeof fill).toBe('string');
      expect(cr(fill, LIGHT)).toBeGreaterThanOrEqual(4.5);
    });
  });

  it('preserves an author-pinned text fill in both themes (default only, never an override)', () => {
    const withAuthorText = (dark: boolean) => {
      const s: any = w4_11();
      s.config = { text: { fill: '#123456' } };
      return reconcileVegaGuideColors(s, dark);
    };
    expect(withAuthorText(true).config.text.fill).toBe('#123456');
    expect(withAuthorText(false).config.text.fill).toBe('#123456');
  });
});
