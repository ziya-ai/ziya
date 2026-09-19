import {
  repairAuthorColorsForTheme,
  applyPlotlyTheme,
  sanitizeLayoutColorsForTheme,
} from '../plotlyPlugin';
import { contrastRatio } from '../chartTheme';

/**
 * D-457 (author-color-not-theme-repaired:dark) and D-458 (named-template-string-
 * ignored), gfx-sweep G-0934c9.
 *
 * D-457: applyPlotlyTheme re-backgrounds the canvas and sets a global font but
 *        never reconciles author-pinned title/legend/shape colours against the
 *        themed dark surface — an author title #1f77b4 (3.46:1 on #1e1e1e), an
 *        author legend.bgcolor #ffffff keeping the themed font at 1.32:1, and an
 *        author #333 guide-stroke at 1.32:1 are all illegible under dark theme.
 * D-458: layout.template='plotly_dark' as a STRING survives sanitize but is
 *        silently ignored by plotly.js (no registered named templates), so the
 *        figure renders LIGHT library defaults and the theme is never applied.
 *
 * Each test asserts the FAILING direction (below) and the fixed direction (at/
 * above the floor), in BOTH themes where the defect spans them.
 */

const DARK = '#1e1e1e';
const LIGHT = '#ffffff';

describe('repairAuthorColorsForTheme (D-457)', () => {
  it('repairs an author title font colour that fails the 4.5:1 floor on the dark surface', () => {
    const layout: any = {
      paper_bgcolor: DARK,
      plot_bgcolor: DARK,
      title: { text: 'Sales', font: { color: '#1f77b4' } },
    };
    // Precondition: the author colour fails on the dark paper.
    expect(contrastRatio('#1f77b4', DARK)).toBeLessThan(4.5);
    const out = repairAuthorColorsForTheme(layout, true);
    expect(out.title.font.color).not.toBe('#1f77b4');
    expect(contrastRatio(out.title.font.color, DARK)).toBeGreaterThanOrEqual(4.5);
  });

  it('leaves an author title colour that already passes on the light surface byte-identical', () => {
    // #1f77b4 is 4.82:1 on white, so light must NOT be touched (no regression).
    expect(contrastRatio('#1f77b4', LIGHT)).toBeGreaterThanOrEqual(4.5);
    const layout: any = {
      paper_bgcolor: LIGHT,
      title: { text: 'Sales', font: { color: '#1f77b4' } },
    };
    const out = repairAuthorColorsForTheme(layout, false);
    expect(out.title.font.color).toBe('#1f77b4');
  });

  it('resolves a theme-clashing author legend.bgcolor back to the dark surface so the themed font reads', () => {
    const layout: any = {
      paper_bgcolor: DARK,
      font: { color: '#e0e0e0' },
      legend: { bgcolor: '#ffffff' },
    };
    // The themed global font on the author white legend is unreadable.
    expect(contrastRatio('#e0e0e0', '#ffffff')).toBeLessThan(3);
    const out = repairAuthorColorsForTheme(layout, true);
    expect(out.legend.bgcolor).toBe(DARK);
    expect(contrastRatio('#e0e0e0', out.legend.bgcolor)).toBeGreaterThanOrEqual(4.5);
  });

  it('does not touch a legend.bgcolor that AGREES with the theme (light panel, light theme)', () => {
    const layout: any = { paper_bgcolor: LIGHT, legend: { bgcolor: '#ffffff' } };
    const out = repairAuthorColorsForTheme(layout, false);
    expect(out.legend.bgcolor).toBe('#ffffff');
  });

  it('repairs an author guide-shape stroke that vanishes on the dark plot surface (3:1 line floor)', () => {
    const layout: any = {
      plot_bgcolor: DARK,
      shapes: [{ type: 'line', line: { color: '#333333', dash: 'dot' } }],
    };
    expect(contrastRatio('#333333', DARK)).toBeLessThan(3);
    const out = repairAuthorColorsForTheme(layout, true);
    expect(out.shapes[0].line.color).not.toBe('#333333');
    expect(contrastRatio(out.shapes[0].line.color, DARK)).toBeGreaterThanOrEqual(3);
    // dash preserved
    expect(out.shapes[0].line.dash).toBe('dot');
  });

  it('is a no-op (reference-equal) for a layout with no author-pinned foreground colours', () => {
    const layout: any = { paper_bgcolor: DARK, title: { text: 'x' } };
    expect(repairAuthorColorsForTheme(layout, true)).toBe(layout);
  });
});

describe('applyPlotlyTheme string template (D-458)', () => {
  it('drops a named string template so the ACTIVE dark theme is applied, not ignored', () => {
    const themed = applyPlotlyTheme({ template: 'plotly_dark' }, true);
    expect(themed.template).toBeUndefined();
    // Dark theme surfaces were applied rather than left at plotly light defaults.
    expect(themed.paper_bgcolor).toBe(DARK);
    expect(themed.plot_bgcolor).toBe(DARK);
  });

  it('drops a named string template under LIGHT theme too (page theme wins)', () => {
    const themed = applyPlotlyTheme({ template: 'plotly_dark' }, false);
    expect(themed.template).toBeUndefined();
    expect(themed.paper_bgcolor).toBe(LIGHT);
  });

  it('sanitizeLayoutColorsForTheme strips any string template', () => {
    expect(sanitizeLayoutColorsForTheme({ template: 'plotly_white' }, false).template).toBeUndefined();
    expect(sanitizeLayoutColorsForTheme({ template: 'made_up_theme' }, true).template).toBeUndefined();
  });

  it('preserves an OBJECT (inline) template', () => {
    const tpl = { layout: { paper_bgcolor: '#123456' } };
    const out = sanitizeLayoutColorsForTheme({ template: tpl }, true);
    expect(out.template).toEqual(tpl);
  });
});
