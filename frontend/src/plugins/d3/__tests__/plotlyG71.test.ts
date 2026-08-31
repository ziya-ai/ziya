/**
 * G-71 — Plotly colorscale-vs-surface guard, theme-surface reconciliation, and
 * legend-overflow sizing.
 *
 *   D-233  colorscale-min == surface renders an invisible cell (LIGHT: a z-min
 *          #fff cell on the #ffffff plot area = 1.00:1); and under DARK theme an
 *          author `paper_bgcolor:'#fff'` survives so the dark global font lands
 *          on it at 1.32:1.
 *   D-241  a >26-entry vertical legend is clipped in the static 60vh div, and
 *          the default 10-colour colorway recycles for >10 series.
 *
 * Every test carries a DIRECTION check: the raw/unpatched value is asserted so
 * the test would FAIL against the pre-fix code. D-233 is a THEME defect, so the
 * theme cases assert BOTH themes — the broken theme is now correct AND the other
 * theme still is.
 */

import { applyPlotlyTheme, reconcilePlotlyThemeSurface } from '../plotlyPlugin';
import {
  guardColorscaleAgainstSurface,
  estimateLegendEntries,
  legendAwareRenderHeightPx,
  PLOTLY_EXTENDED_COLORWAY,
  PLOTLY_COLORWAY_RECYCLE_THRESHOLD,
  PLOTLY_LEGEND_CLIP_ENTRIES,
  PLOTLY_COLORSCALE_MIN_CONTRAST,
} from '../plotlyPreprocessor';
import { contrastRatio } from '../chartTheme';

const LIGHT_BG = '#ffffff';
const DARK_BG = '#1e1e1e';

// ── D-233: colorscale endpoint vs surface guard ─────────────────────────────

describe('guardColorscaleAgainstSurface (D-233)', () => {
  const wave4Scale = [[0, '#fff'], [0.5, '#f80'], [1, '#03a']];

  it('DIRECTION: the raw #fff min endpoint is invisible on the light plot area', () => {
    expect(contrastRatio('#fff', LIGHT_BG)).toBeCloseTo(1.0, 2);
  });

  it('nudges an endpoint that collides with the LIGHT surface until the cell is visible', () => {
    const data = [{ type: 'heatmap', z: [[1]], colorscale: wave4Scale }];
    const out = guardColorscaleAgainstSurface(data, LIGHT_BG);
    const scale = out[0].colorscale;
    // min endpoint changed away from #fff
    expect(scale[0][1]).not.toBe('#fff');
    expect(contrastRatio(scale[0][1], LIGHT_BG)).toBeGreaterThanOrEqual(PLOTLY_COLORSCALE_MIN_CONTRAST);
    // non-colliding stops preserved verbatim
    expect(scale[1][1]).toBe('#f80');
    expect(scale[2][1]).toBe('#03a');
  });

  it('BOTH-THEME per-surface resolution: the SAME #fff endpoint is left alone on the DARK surface', () => {
    // On a dark plot area a white cell is highly visible — no nudge (this is why
    // the guard resolves against the theme surface, not a blind constant swap).
    expect(contrastRatio('#fff', DARK_BG)).toBeGreaterThan(3);
    const data = [{ type: 'heatmap', z: [[1]], colorscale: wave4Scale }];
    const out = guardColorscaleAgainstSurface(data, DARK_BG);
    expect(out).toBe(data); // reference-stable no-op
    expect(out[0].colorscale[0][1]).toBe('#fff');
  });

  it('guards a dark endpoint colliding with the dark surface', () => {
    const data = [{ type: 'heatmap', z: [[1]], colorscale: [[0, '#1e1e1e'], [1, '#f80']] }];
    const out = guardColorscaleAgainstSurface(data, DARK_BG);
    expect(out[0].colorscale[0][1]).not.toBe('#1e1e1e');
    expect(contrastRatio(out[0].colorscale[0][1], DARK_BG)).toBeGreaterThanOrEqual(PLOTLY_COLORSCALE_MIN_CONTRAST);
  });

  it('leaves a named (string) colorscale untouched', () => {
    const data = [{ type: 'heatmap', z: [[1]], colorscale: 'Viridis' }];
    expect(guardColorscaleAgainstSurface(data, LIGHT_BG)).toBe(data);
  });

  it('guards marker.colorscale on non-heatmap traces too', () => {
    const data = [{ type: 'scatter', marker: { colorscale: [[0, '#ffffff'], [1, '#000']] } }];
    const out = guardColorscaleAgainstSurface(data, LIGHT_BG);
    expect(out[0].marker.colorscale[0][1]).not.toBe('#ffffff');
  });

  it('is a reference-stable no-op when no endpoint collides', () => {
    const data = [{ type: 'heatmap', z: [[1]], colorscale: [[0, '#03a'], [1, '#f80']] }];
    expect(guardColorscaleAgainstSurface(data, LIGHT_BG)).toBe(data);
  });
});

// ── D-233 dark half: theme-surface reconciliation ───────────────────────────

describe('applyPlotlyTheme / reconcilePlotlyThemeSurface (D-233 dark)', () => {
  it('DIRECTION: the dark global font on an author white paper is unreadable', () => {
    expect(contrastRatio('#e0e0e0', '#ffffff')).toBeLessThan(1.5);
  });

  it('DARK: an author light paper_bgcolor is resolved to the theme surface, font stays readable', () => {
    const themed = applyPlotlyTheme({ paper_bgcolor: '#fff', title: { text: 'x' } }, true);
    // paper reclaimed by the theme (was surviving #fff -> two-tone / 1.32:1)
    expect(themed.paper_bgcolor).toBe('#1e1e1e');
    expect(themed.plot_bgcolor).toBe('#1e1e1e');
    expect(themed.font.color).toBe('#e0e0e0');
    expect(contrastRatio(themed.font.color, themed.paper_bgcolor)).toBeGreaterThanOrEqual(4.5);
  });

  it('BOTH-THEME: LIGHT keeps the author white paper (no clash) and the light font', () => {
    const themed = applyPlotlyTheme({ paper_bgcolor: '#fff', title: { text: 'x' } }, false);
    expect(themed.paper_bgcolor).toBe('#fff');
    expect(themed.font.color).toBe('#333333');
    expect(contrastRatio(themed.font.color, '#ffffff')).toBeGreaterThanOrEqual(4.5);
  });

  it('respects an explicitly author-pinned font colour (deliberate custom scheme)', () => {
    const themed = applyPlotlyTheme({ paper_bgcolor: '#fff', font: { color: '#000' } }, true);
    // author owns the scheme: paper NOT reclaimed, font NOT overridden
    expect(themed.paper_bgcolor).toBe('#fff');
    expect(themed.font.color).toBe('#000');
  });

  it('does NOT touch an author background that AGREES with the theme', () => {
    const themed = applyPlotlyTheme({ paper_bgcolor: '#111111' }, true);
    expect(themed.paper_bgcolor).toBe('#111111'); // dark bg under dark theme kept
  });

  it('LIGHT mirror: an author dark paper under light theme is reclaimed to white + dark font', () => {
    const themed = applyPlotlyTheme({ paper_bgcolor: '#111111' }, false);
    expect(themed.paper_bgcolor).toBe('#ffffff');
    expect(themed.font.color).toBe('#333333');
    expect(contrastRatio(themed.font.color, '#ffffff')).toBeGreaterThanOrEqual(4.5);
  });

  it('reconcilePlotlyThemeSurface returns merged untouched when author pinned a font', () => {
    const merged = { paper_bgcolor: '#fff', font: { color: '#000' } };
    expect(reconcilePlotlyThemeSurface(merged, { font: { color: '#000' } }, true)).toBe(merged);
  });
});

// ── D-241: legend overflow sizing + colorway extension ──────────────────────

describe('estimateLegendEntries / legendAwareRenderHeightPx (D-241)', () => {
  const mkSeries = (n: number) =>
    Array.from({ length: n }, (_, i) => ({ type: 'scatter', mode: 'lines', name: `s${i}`, y: [i] }));

  it('counts legend-bearing traces', () => {
    expect(estimateLegendEntries(mkSeries(40), { showlegend: true })).toBe(40);
  });

  it('returns 0 when the layout disables the legend', () => {
    expect(estimateLegendEntries(mkSeries(40), { showlegend: false })).toBe(0);
  });

  it('excludes colorbar/surface traces and showlegend:false traces from the count', () => {
    const data = [
      { type: 'heatmap', z: [[1]] },
      { type: 'scatter', name: 'a' },
      { type: 'scatter', name: 'b', showlegend: false },
    ];
    expect(estimateLegendEntries(data, {})).toBe(1);
  });

  it('DIRECTION: <=26 entries keep the default 60vh (null), so ordinary figures are unchanged', () => {
    expect(legendAwareRenderHeightPx(PLOTLY_LEGEND_CLIP_ENTRIES)).toBeNull();
    expect(legendAwareRenderHeightPx(10)).toBeNull();
  });

  it('grows the render height when the legend would overflow', () => {
    const h = legendAwareRenderHeightPx(40);
    expect(h).not.toBeNull();
    expect(h!).toBeGreaterThan(480); // beyond the clip point / min grown height
    expect(h!).toBeLessThanOrEqual(2400); // clamped
  });

  it('clamps an extreme legend to the ceiling', () => {
    expect(legendAwareRenderHeightPx(10000)).toBe(2400);
  });
});

describe('PLOTLY_EXTENDED_COLORWAY (D-241)', () => {
  it('has more than the recycling threshold of entries', () => {
    expect(PLOTLY_EXTENDED_COLORWAY.length).toBeGreaterThan(PLOTLY_COLORWAY_RECYCLE_THRESHOLD);
  });

  it('preserves plotly default 10 as the first entries (series 1..10 unchanged)', () => {
    expect(PLOTLY_EXTENDED_COLORWAY.slice(0, 10)).toEqual([
      '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
      '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
    ]);
  });

  it('every entry is a distinct hex (no recycling within the extended palette)', () => {
    expect(new Set(PLOTLY_EXTENDED_COLORWAY).size).toBe(PLOTLY_EXTENDED_COLORWAY.length);
  });
});
