import { ensurePlotlyTitleAutomargin, plotlyRenderMinHeightCss } from '../plotlyPlugin';

/**
 * D-304 (gfx-sweep G-ff1987): title-clipped-by-grown-legend-div.
 *
 * plotly-w2-02 is a 40-series figure with a plain `layout.title`. The plugin
 * forces a compact `margin: { t: 40 }` on every figure and Plotly draws the
 * title inside that top band, so a default ~17px title is clipped through its
 * middle — worst on the legend-grown tall div (D-241). `title.automargin` makes
 * the title push the top margin so it is always fully reserved regardless of
 * the fixed margin.t or the div height.
 *
 * The fix must (a) enable automargin whenever a title is present, (b) preserve
 * an author-chosen automargin, (c) be a no-op for titleless figures (so
 * previously-verified plots are byte-identical), and (d) be theme-independent
 * (the title colour is themed elsewhere), so it holds in BOTH themes.
 */

describe('ensurePlotlyTitleAutomargin (D-304)', () => {
  it('enables automargin for an object title lacking it (the w2-02 case)', () => {
    const layout: any = { title: { text: '40 series, default colorway' }, showlegend: true };
    const out = ensurePlotlyTitleAutomargin(layout);
    expect(out.title.automargin).toBe(true);
    expect(out.title.text).toBe('40 series, default colorway');
    // Non-mutating: original untouched.
    expect(layout.title.automargin).toBeUndefined();
  });

  it('wraps a bare string title and enables automargin', () => {
    const out = ensurePlotlyTitleAutomargin({ title: 'Hello' });
    expect(out.title).toEqual({ text: 'Hello', automargin: true });
  });

  it('preserves an author-chosen automargin=false', () => {
    const layout: any = { title: { text: 'x', automargin: false } };
    const out = ensurePlotlyTitleAutomargin(layout);
    expect(out.title.automargin).toBe(false);
  });

  it('is a no-op for a titleless figure (byte-identical, no regression)', () => {
    const layout: any = { showlegend: true, margin: { t: 40 } };
    const out = ensurePlotlyTitleAutomargin(layout);
    expect(out).toBe(layout); // same reference: nothing changed
    expect(out.title).toBeUndefined();
  });

  it('is a no-op for an empty-string title', () => {
    const layout: any = { title: '' };
    expect(ensurePlotlyTitleAutomargin(layout)).toBe(layout);
  });

  it('is theme-independent (same output in light and dark)', () => {
    const base = () => ({ title: { text: 'T' } });
    // The helper takes no theme input; the two callers below stand in for the
    // light and dark render paths, which differ only in trace/layout colours.
    const light = ensurePlotlyTitleAutomargin(base());
    const dark = ensurePlotlyTitleAutomargin(base());
    expect(light).toEqual(dark);
    expect(light.title.automargin).toBe(true);
  });

  it('tolerates non-object layouts', () => {
    expect(ensurePlotlyTitleAutomargin(null)).toBeNull();
    expect(ensurePlotlyTitleAutomargin(undefined)).toBeUndefined();
  });
});

// Guard the sibling min-height helper stays intact alongside this change.
describe('plotlyRenderMinHeightCss (unchanged)', () => {
  it('honours an explicit height, else 400px floor', () => {
    expect(plotlyRenderMinHeightCss(260)).toBe('260px');
    expect(plotlyRenderMinHeightCss(undefined)).toBe('400px');
  });
});
