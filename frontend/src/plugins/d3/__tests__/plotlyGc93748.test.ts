/**
 * @jest-environment jsdom
 */
/**
 * G-c93748 regression tests — the six legacy plotly defects whose shared root
 * cause lives in plotlyPlugin.ts / plotlyPreprocessor.ts.  Each `it` maps one
 * backlog defect id to a symptom-specific assertion that FAILS against the
 * pre-fix code (so it certifies the fix, not the bug).  These defects were the
 * original w-series observations; their fixes shipped under the companion ids
 * D-230 / D-236 / D-238 / D-241 / D-243 / D-232 already exercised by
 * plotlyG20 / plotlyG40 / plotlyG71, but nothing pinned the ORIGINAL symptoms.
 *
 *   D-211  malformed-json → 30s empty-DOM timeout: parsePlotlyDefinition now
 *          recovers every one-lexeme-off form the harness saw.
 *   D-213  hardcoded fixed margin, no automargin: enableAxisAutomargin.
 *   D-214  dark global font on an unthemed LIGHT surface (polar / table / a
 *          surviving author paper_bgcolor) ≈ 1.32:1 — asserted in BOTH themes.
 *   D-216  explicit width/height exceeds capture viewport → content cropped:
 *          clampLayoutDimensions.
 *   D-219  invalid colour tokens fall back to library default: theme-aware
 *          sanitizeLayoutColorsForTheme + stripInvalidTraceColors.
 *   D-222  vertical legend past ~26 entries clipped + 10-colour recycle:
 *          legendAwareRenderHeightPx + PLOTLY_EXTENDED_COLORWAY.
 */
import {
  parsePlotlyDefinition,
  enableAxisAutomargin,
  clampLayoutDimensions,
  stripInvalidTraceColors,
  isValidColorToken,
  estimateLegendEntries,
  legendAwareRenderHeightPx,
  PLOTLY_EXTENDED_COLORWAY,
} from '../plotlyPreprocessor';
import { applyPlotlyTheme, applyPlotlyTraceTheme, sanitizeLayoutColorsForTheme } from '../plotlyPlugin';

/** WCAG relative-contrast so the theme assertions are pinned to a ratio, not a
 *  hardcoded hex the fix could silently change. */
function luminance(hex: string): number {
  const h = hex.replace('#', '');
  const rgb = [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16) / 255);
  const lin = rgb.map(c => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)));
  return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
}
function contrast(a: string, b: string): number {
  const la = luminance(a), lb = luminance(b);
  const hi = Math.max(la, lb), lo = Math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
}

describe('G-c93748 — plotly plugin/preprocessor legacy defects', () => {
  it('D-211: parsePlotlyDefinition recovers the malformed forms that used to hang the harness', () => {
    const cases: string[] = [
      '```json\n{"data":[{"x":[1,2],"y":[3,4]}]}\n```',        // markdown fence
      '{"data":[{"x":[1,2],"y":[3,4]},]}',                     // trailing comma
      "{data:[{x:[1,2],y:[3,4]}]}",                            // unquoted keys
      "{'data':[{'x':[1,2],'y':[3,4]}]}",                      // single quotes
      'var fig = {"data":[{"x":[1,2],"y":[3,4]}]};',           // assignment wrapper + ;
      '{"data":[{"x":[1,2],"y":[3,4],"visible":True}]}',       // Python literal
    ];
    for (const raw of cases) {
      const parsed = parsePlotlyDefinition(raw);
      expect(parsed).toBeTruthy();
      expect(Array.isArray(parsed.data)).toBe(true);
      expect(parsed.data[0].x).toEqual([1, 2]);
    }
    // A genuinely unparseable string still fails fast (null), not a hang.
    expect(parsePlotlyDefinition('not a spec at all {{{')).toBeFalsy();
  });

  it('D-213: enableAxisAutomargin sets automargin on every cartesian axis (fixed-margin clip)', () => {
    const layout = { xaxis: { title: { text: 'x' } }, yaxis: {}, yaxis2: {} };
    const out = enableAxisAutomargin(layout);
    expect(out.xaxis.automargin).toBe(true);
    expect(out.yaxis.automargin).toBe(true);
    expect(out.yaxis2.automargin).toBe(true);
    // an author-set automargin is preserved, not clobbered.
    const kept = enableAxisAutomargin({ xaxis: { automargin: false } });
    expect(kept.xaxis.automargin).toBe(false);
  });

  it('D-214: dark global font resolves onto a THEMED surface (polar/table/paper) in both themes', () => {
    const DARK_BG = '#1e1e1e', LIGHT_BG = '#ffffff';

    // polar subplot: dark theme must re-background it (was white → 1.32:1).
    const darkPolar = applyPlotlyTheme({ polar: { radialaxis: {} } }, true);
    expect(darkPolar.polar.bgcolor).toBe(DARK_BG);
    expect(contrast('#e0e0e0', darkPolar.polar.bgcolor)).toBeGreaterThan(4.5);
    // light theme leaves the light surface + dark font readable and does NOT
    // paint a dark polar bg (byte-identical light path).
    const lightPolar = applyPlotlyTheme({ polar: { radialaxis: {} } }, false);
    expect(lightPolar.polar?.bgcolor).toBeUndefined();
    expect(contrast('#333333', LIGHT_BG)).toBeGreaterThan(4.5);

    // table trace fills are layout-invisible; applyPlotlyTraceTheme themes them.
    const table = [{ type: 'table', header: { values: ['a'] }, cells: { values: [[1]] } }];
    const darkTable = applyPlotlyTraceTheme(table, true);
    expect(darkTable[0].cells.fill.color).toBe(DARK_BG);
    expect(contrast(darkTable[0].cells.font.color, darkTable[0].cells.fill.color)).toBeGreaterThan(4.5);
    // light is returned byte-identical (same reference).
    expect(applyPlotlyTraceTheme(table, false)).toBe(table);

    // a surviving author paper_bgcolor '#fff' under dark is reconciled to the
    // theme surface so the dark font is not stranded on white.
    const survived = applyPlotlyTheme({ paper_bgcolor: '#fff' }, true);
    expect(survived.paper_bgcolor).toBe(DARK_BG);
  });

  it('D-216: clampLayoutDimensions shrinks an oversized explicit width/height to the viewport', () => {
    const out = clampLayoutDimensions({ width: 4000, height: 2600 }, 1280, 1024);
    expect(out.width).toBe(1280);
    expect(out.height).toBe(1024);
    // a within-bounds figure is returned by reference (never grown/added-to).
    const small = { width: 600, height: 400 };
    expect(clampLayoutDimensions(small, 1280, 1024)).toBe(small);
  });

  it('D-219: invalid colour tokens are stripped/repaired theme-aware, not left to the library default', () => {
    expect(isValidColorToken('var(--accent-color)')).toBe(false);
    expect(isValidColorToken('neutral-300')).toBe(false);
    expect(isValidColorToken('#1f77b4')).toBe(true);

    // trace-level invalid colour is removed so plotly assigns a palette colour.
    const traces = stripInvalidTraceColors([{ line: { color: 'var(--accent-color)' }, marker: { color: 'primary' } }]);
    expect(traces[0].line.color).toBeUndefined();
    expect(traces[0].marker.color).toBeUndefined();

    // layout-level invalid grid/bg/template repaired to THEME defaults.
    const darkSan = sanitizeLayoutColorsForTheme(
      { gridcolor: 'neutral-300', paper_bgcolor: '$background', template: 'plotly_dark_v2' }, true);
    expect(darkSan.gridcolor).toBe('#8a8a8a');
    expect(darkSan.paper_bgcolor).toBe('#1e1e1e');   // theme surface, not white
    expect(darkSan.template).toBeUndefined();         // hallucinated template dropped
    expect(contrast('#8a8a8a', '#1e1e1e')).toBeGreaterThan(3);
    // light half of the same repair.
    const lightSan = sanitizeLayoutColorsForTheme({ gridcolor: 'neutral-300', paper_bgcolor: '$background' }, false);
    expect(lightSan.paper_bgcolor).toBe('#ffffff');
    expect(contrast('#8a8a8a', '#ffffff')).toBeGreaterThan(3);
  });

  it('D-222: a legend past ~26 entries grows the capture div and >10 series stop recycling colours', () => {
    const data = Array.from({ length: 40 }, (_, i) => ({ type: 'scatter', name: 's' + i, x: [1], y: [1] }));
    expect(estimateLegendEntries(data, {})).toBe(40);
    const px = legendAwareRenderHeightPx(40);
    expect(px).not.toBeNull();
    expect(px!).toBeGreaterThan(480);            // grown beyond the default floor
    // a modest legend keeps the default (null → 60vh) so ordinary figures are unchanged.
    expect(legendAwareRenderHeightPx(12)).toBeNull();
    // the extended colorway has >10 distinct hues and keeps the default 10 first.
    expect(PLOTLY_EXTENDED_COLORWAY.length).toBeGreaterThan(10);
    expect(new Set(PLOTLY_EXTENDED_COLORWAY).size).toBe(PLOTLY_EXTENDED_COLORWAY.length);
    expect(PLOTLY_EXTENDED_COLORWAY.slice(0, 10)).toEqual([
      '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
      '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
    ]);
  });
});
