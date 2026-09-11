/**
 * D-043 / G-40 — plotly unthemed surfaces + colorscale/surface collision.
 *
 * The triage hypothesis for D-043 named four sub-mechanisms:
 *   (1) polar/ternary/geo/table surfaces keep light defaults under dark theme
 *       so the injected dark font lands unreadable (~1.32:1)   [plotly-w1-14/w1-15]
 *   (2) a surviving author paper_bgcolor stays light under dark [plotly-w4-07]
 *   (3) a colorscale endpoint equal to the surface -> invisible cell (light) [plotly-w4-07]
 *   (4) an unset annotation arrow/font stays #444 under dark   [plotly-w1-13]
 *
 * On reading source, ALL FOUR are already resolved under prior sweep work:
 *   - polar/ternary/geo theming + table trace theming  (D-243: applyPlotlyTheme /
 *     applyPlotlyTraceTheme)
 *   - author-background reconciliation                 (D-233 dark half:
 *     reconcilePlotlyThemeSurface)
 *   - colorscale-endpoint/surface collision guard      (D-233 light half:
 *     guardColorscaleAgainstSurface)
 *   - dark annotation arrow/font resolution            (D-263: themeDarkAnnotations)
 *
 * So this iteration makes NO source change; this is a BOTH-THEME regression
 * guard that pins the resolved behaviour (green by design — mirrors the D-019
 * disposition recorded in the backlog). It asserts the previously-broken theme
 * is now correct AND the other theme still is, so a future regression in either
 * direction (e.g. a dark fix that silently breaks light) turns it red.
 */

import {
  applyPlotlyTheme,
  applyPlotlyTraceTheme,
  themeDarkAnnotations,
  reconcilePlotlyThemeSurface,
} from '../plotlyPlugin';
import { guardColorscaleAgainstSurface } from '../plotlyPreprocessor';
import { contrastRatio, isDarkBackground } from '../chartTheme';

const DARK_SURFACE = '#1e1e1e';
const LIGHT_SURFACE = '#ffffff';
const GRAPHICAL_FLOOR = 3; // WCAG large-area / graphical contrast floor

// ── plotly-w1-14: polar (non-cartesian) subplot surface theming ───────────────
describe('D-043 plotly-w1-14: polar subplot surfaces themed per theme', () => {
  const layout = () => ({
    title: { text: 'Service Scorecard' },
    polar: { radialaxis: { visible: true, range: [0, 5] } },
    showlegend: true,
    height: 480,
  });

  it('DARK (was broken): polar gets a dark bgcolor so the dark font is legible', () => {
    const out = applyPlotlyTheme(layout(), true);
    expect(out.polar.bgcolor).toBe(DARK_SURFACE);
    // author radialaxis sub-object is preserved (merged last)
    expect(out.polar.radialaxis.range).toEqual([0, 5]);
    // injected dark font reads on the now-dark polar surface
    expect(contrastRatio(out.font.color, out.polar.bgcolor)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
  });

  it('LIGHT (still correct): no dark polar surface injected, light font legible on default', () => {
    const out = applyPlotlyTheme(layout(), false);
    // light branch does not force a dark polar bgcolor onto the light default
    expect(out.polar?.bgcolor).not.toBe(DARK_SURFACE);
    expect(contrastRatio(out.font.color, LIGHT_SURFACE)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
  });
});

// ── plotly-w1-15: table trace-owned fills (unreachable by layout theming) ─────
describe('D-043 plotly-w1-15: table header/cell fills themed in dark, byte-identical in light', () => {
  const data = () => ([{
    type: 'table',
    header: { values: ['<b>Service</b>'], fill: { color: '#4c78a8' }, font: { color: '#ffffff' } },
    cells: { values: [['auth', 'catalog']] }, // fill + font UNSET
  }]);

  it('DARK (was broken): unset cell fill/font themed dark; author header kept', () => {
    const [t] = applyPlotlyTraceTheme(data(), true);
    expect(t.cells.fill.color).toBe(DARK_SURFACE);       // unset -> dark cell
    expect(t.cells.font.color).toBe('#e0e0e0');           // unset -> dark font
    expect(t.header.fill.color).toBe('#4c78a8');          // author pinned -> kept
    expect(t.header.font.color).toBe('#ffffff');          // author pinned -> kept
    expect(contrastRatio(t.cells.font.color, t.cells.fill.color)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
  });

  it('LIGHT (still correct): data returned byte-identical (dark-gated)', () => {
    const input = data();
    expect(applyPlotlyTraceTheme(input, false)).toBe(input); // same reference = no-op
  });
});

// ── plotly-w4-07: author light paper under dark + colorscale/surface collision ─
describe('D-043 plotly-w4-07: author #fff paper reconciled + white colorscale endpoint guarded', () => {
  const shorthandScale = [[0, '#fff'], [0.5, '#f80'], [1, '#03a']];

  it('DARK (was broken): a surviving author paper_bgcolor:#fff is resolved to the dark surface', () => {
    // simulate the merged layout after the ...base spread let the author bg win
    const merged = { paper_bgcolor: '#fff', plot_bgcolor: DARK_SURFACE, font: { color: '#e0e0e0' } };
    const out = reconcilePlotlyThemeSurface(merged, { paper_bgcolor: '#fff' }, true);
    expect(isDarkBackground(out.paper_bgcolor)).toBe(true);
    expect(contrastRatio(out.font.color, out.paper_bgcolor)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
    // on the dark surface the white colorscale endpoint is high-contrast -> kept
    const [t] = guardColorscaleAgainstSurface(
      [{ type: 'heatmap', colorscale: shorthandScale }], DARK_SURFACE);
    expect(t.colorscale[0][1]).toBe('#fff');
  });

  it('LIGHT (was broken): white z-min endpoint on a white surface is nudged to a visible shade', () => {
    const [t] = guardColorscaleAgainstSurface(
      [{ type: 'heatmap', colorscale: shorthandScale }], LIGHT_SURFACE);
    const endpoint = t.colorscale[0][1];
    expect(endpoint).not.toBe('#fff');                    // the invisible hole is closed
    expect(contrastRatio(endpoint, LIGHT_SURFACE)).toBeGreaterThan(1.1);
    // author light paper #fff AGREES with light theme -> left verbatim
    const out = reconcilePlotlyThemeSurface(
      { paper_bgcolor: '#fff', plot_bgcolor: '#fff', font: { color: '#333333' } },
      { paper_bgcolor: '#fff' }, false);
    expect(out.paper_bgcolor).toBe('#fff');
  });
});

// ── plotly-w1-13: unset annotation arrow/font ─────────────────────────────────
describe('D-043 plotly-w1-13: annotation arrow/font resolved from dark theme, light untouched', () => {
  const layout = () => ({
    annotations: [{ x: 'W5', y: 3.9, text: 'SLO breach', showarrow: true, arrowhead: 3 }],
  });

  it('DARK (was broken): unset arrowcolor/font filled with the dark foreground', () => {
    const out = themeDarkAnnotations(layout(), true);
    expect(out.annotations[0].arrowcolor).toBe('#e0e0e0');
    expect(out.annotations[0].font.color).toBe('#e0e0e0');
    expect(contrastRatio(out.annotations[0].arrowcolor, DARK_SURFACE)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
  });

  it('LIGHT (still correct): annotations returned untouched (plotly #444 default = 9.74:1 on white)', () => {
    const input = layout();
    expect(themeDarkAnnotations(input, false)).toBe(input); // no-op in light
    expect(contrastRatio('#444444', LIGHT_SURFACE)).toBeGreaterThanOrEqual(GRAPHICAL_FLOOR);
  });

  it('DARK: an explicit author annotation colour still wins over the theme', () => {
    const authored = { annotations: [{ text: 'x', arrowcolor: '#ff0000', font: { color: '#00ff00' } }] };
    const out = themeDarkAnnotations(authored, true);
    expect(out.annotations[0].arrowcolor).toBe('#ff0000');
    expect(out.annotations[0].font.color).toBe('#00ff00');
  });
});
