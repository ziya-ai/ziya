/**
 * @jest-environment jsdom
 */
/**
 * G-721d64 / D-460 — plotly-w3-07: scatterternary + carpet/contourcarpet.
 *
 * Two in-scope halves of the "carpet-traces-dropped-by-hang-mitigation" defect:
 *
 *   (1) THEME (applyPlotlyTheme, dark): the dark branch re-backgrounded only the
 *       ternary `bgcolor` and left the three ternary sub-axes (aaxis/baxis/caxis)
 *       at plotly's default grid (~#282828) which is 1.13:1 on the #1e1e1e
 *       surface — invisible.  The fix themes the sub-axis grid/line to #8a8a8a
 *       (4.83:1 on dark #1e1e1e, 3.45:1 on light #ffffff — clears 3:1 on BOTH),
 *       while an author sub-axis field (e.g. title) still wins.
 *
 *   (2) STRUCTURE (neutralizeCaptureHangCombos): dropping the carpet family to
 *       dodge the upstream contourcarpet sync-hang left the x2/y2 subplot the
 *       carpet occupied as an empty framed box — a "silent blank" half the
 *       canvas wide.  The fix also strips the now-orphaned axis definitions no
 *       surviving trace references.
 *
 * Every assertion is DIRECTIONAL: it fails against the pre-fix code (no ternary
 * sub-axis grid; orphaned xaxis2 retained) and passes with the fix.
 */
import { applyPlotlyTheme } from '../plotlyPlugin';
import { neutralizeCaptureHangCombos } from '../plotlyPreprocessor';

function luminance(hex: string): number {
  const h = hex.replace('#', '');
  const ch = [0, 2, 4].map(i => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
}
function contrast(a: string, b: string): number {
  const la = luminance(a), lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

// The real plotly-w3-07 layout.ternary shape (author titles on each sub-axis).
const ternaryLayout = () => ({
  ternary: {
    domain: { x: [0, 0.45] },
    aaxis: { title: { text: 'A' } },
    baxis: { title: { text: 'B' } },
    caxis: { title: { text: 'C' } },
  },
});

describe('D-460 dark ternary grid — sub-axes themed and visible on both backgrounds', () => {
  it('DARK: aaxis/baxis/caxis gain a grid/line colour that clears 3:1 on BOTH surfaces', () => {
    const dark = applyPlotlyTheme(ternaryLayout(), true);
    for (const ax of ['aaxis', 'baxis', 'caxis'] as const) {
      const grid = dark.ternary[ax].gridcolor;
      const line = dark.ternary[ax].linecolor;
      // DIRECTION: pre-fix these were undefined (plotly's ~1.13:1 default grid stood).
      expect(grid).toBeDefined();
      expect(line).toBeDefined();
      expect(contrast(grid, '#1e1e1e')).toBeGreaterThanOrEqual(3); // dark background
      expect(contrast(grid, '#ffffff')).toBeGreaterThanOrEqual(3); // light background
    }
    // The old near-invisible default pairing.
    expect(contrast('#282828', '#1e1e1e')).toBeLessThan(1.5);
  });

  it('DARK: author sub-axis title survives the grid merge', () => {
    const dark = applyPlotlyTheme(ternaryLayout(), true);
    expect(dark.ternary.aaxis.title.text).toBe('A');
    expect(dark.ternary.baxis.title.text).toBe('B');
    expect(dark.ternary.caxis.title.text).toBe('C');
    expect(dark.ternary.bgcolor).toBe('#1e1e1e');
  });

  it('LIGHT: unchanged — no injected ternary sub-axis grid', () => {
    const light = applyPlotlyTheme(ternaryLayout(), false);
    // Light path only spreads base; it must NOT gain a themed grid colour.
    expect(light.ternary?.aaxis?.gridcolor).toBeUndefined();
    expect(light.paper_bgcolor).toBe('#ffffff');
  });
});

describe('D-460 hang mitigation — dropped carpet leaves no orphaned empty subplot', () => {
  const w307 = () => ({
    data: [
      { type: 'scatterternary', a: [0.6, 0.2], b: [0.2, 0.6], c: [0.2, 0.2], mode: 'markers' },
      { type: 'carpet', carpet: 'c1', a: [0, 1], b: [1, 2], y: [1, 2], xaxis: 'x2', yaxis: 'y2' },
      { type: 'contourcarpet', carpet: 'c1', a: [0, 1], b: [1, 2], z: [1, 2], xaxis: 'x2', yaxis: 'y2' },
    ],
    layout: {
      ternary: { domain: { x: [0, 0.45] } },
      xaxis2: { domain: [0.58, 1.0] },
      yaxis2: {},
    },
  });

  it('drops the carpet family AND removes the now-orphaned x2/y2 axes', () => {
    const out = neutralizeCaptureHangCombos(w307() as any, true);
    expect((out.data || []).map((t: any) => t.type)).toEqual(['scatterternary']);
    // DIRECTION: pre-fix xaxis2/yaxis2 stayed and plotly drew an empty framed box.
    expect((out.layout as any).xaxis2).toBeUndefined();
    expect((out.layout as any).yaxis2).toBeUndefined();
    // The ternary (surviving content) keeps its layout.
    expect((out.layout as any).ternary).toBeDefined();
  });

  it('keeps an axis still referenced by a surviving trace', () => {
    const spec = {
      data: [
        { type: 'scatter', x: [1], y: [2], xaxis: 'x2', yaxis: 'y2' },
        { type: 'carpet', carpet: 'c1', a: [0], b: [1], y: [1], xaxis: 'x2', yaxis: 'y2' },
        { type: 'contourcarpet', carpet: 'c1', a: [0], b: [1], z: [1], xaxis: 'x2', yaxis: 'y2' },
      ],
      layout: { xaxis2: { domain: [0.58, 1] }, yaxis2: {} },
    };
    const out = neutralizeCaptureHangCombos(spec as any, true);
    expect((out.data || []).map((t: any) => t.type)).toEqual(['scatter']);
    // x2/y2 still used by the surviving scatter -> must NOT be stripped.
    expect((out.layout as any).xaxis2).toBeDefined();
    expect((out.layout as any).yaxis2).toBeDefined();
  });
});
