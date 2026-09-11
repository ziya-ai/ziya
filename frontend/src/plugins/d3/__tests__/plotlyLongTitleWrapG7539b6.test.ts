/**
 * D-305 (group G-7539b6) — pathologically long title / axis-title / legend text
 * must be wrapped (titles) or ellipsized (legend names) so it no longer clips at
 * the paper edges. Theme-independent geometry: preprocessing is theme-agnostic,
 * so the SAME wrapped/ellipsized output must hold regardless of background — we
 * assert that explicitly (the transform is a pure function of the spec).
 *
 * These assertions FAIL without wrapLongTitles / ellipsizeLongLegendNames being
 * wired into preprocessPlotlySpec (the title stays a single un-broken line and
 * the legend names stay full length), and PASS with the fix.
 */
import {
  preprocessPlotlySpec,
  wrapPlotlyText,
  wrapLongTitles,
  ellipsizeLongLegendNames,
  PLOTLY_TITLE_WRAP_MAXCHARS,
  PLOTLY_TITLE_WRAP_MAXLINES,
  PLOTLY_LEGEND_NAME_MAXCHARS,
} from '../plotlyPreprocessor';

const LONG_TITLE =
  'Aggregate p99 end-to-end request latency measured at the edge termination ' +
  'layer for authenticated traffic excluding health-check probes and synthetic canaries';
const LONG_NAME =
  'us-west-2 primary availability zone cluster group alpha, canary cohort, ' +
  'post-migration cutover window, excluding shadow traffic';

const longSpec = () => ({
  data: [
    { type: 'scatter', mode: 'lines+markers', name: LONG_NAME, x: ['a', 'b'], y: [1, 2] },
    { type: 'scatter', mode: 'lines+markers', name: LONG_NAME + ' beta', x: ['a', 'b'], y: [3, 4] },
  ],
  layout: {
    title: { text: LONG_TITLE },
    xaxis: { title: { text: LONG_TITLE }, type: 'category' },
    yaxis: { title: { text: LONG_TITLE } },
  },
});

const maxLineLen = (s: string) =>
  s.split('<br>').reduce((m, ln) => Math.max(m, ln.length), 0);

describe('wrapPlotlyText', () => {
  it('breaks a long single line into <br>-joined lines each within the width', () => {
    const out = wrapPlotlyText(LONG_TITLE, PLOTLY_TITLE_WRAP_MAXCHARS, PLOTLY_TITLE_WRAP_MAXLINES);
    expect(out).toContain('<br>');
    expect(maxLineLen(out)).toBeLessThanOrEqual(PLOTLY_TITLE_WRAP_MAXCHARS);
    expect(out.split('<br>').length).toBeLessThanOrEqual(PLOTLY_TITLE_WRAP_MAXLINES);
  });

  it('leaves short text and author-wrapped text untouched (conservative)', () => {
    expect(wrapPlotlyText('short title', 60, 4)).toBe('short title');
    expect(wrapPlotlyText('one<br>two three four', 60, 4)).toBe('one<br>two three four');
  });

  it('hard-breaks a single token wider than a line', () => {
    const token = 'x'.repeat(150);
    const out = wrapPlotlyText(token, 60, 4);
    expect(maxLineLen(out)).toBeLessThanOrEqual(60);
  });
});

describe('D-305 wrapLongTitles / ellipsizeLongLegendNames via preprocessPlotlySpec', () => {
  it('wraps the main title and both axis titles', () => {
    const out: any = preprocessPlotlySpec(longSpec() as any);
    for (const t of [out.layout.title.text, out.layout.xaxis.title.text, out.layout.yaxis.title.text]) {
      expect(t).toContain('<br>');
      expect(maxLineLen(t)).toBeLessThanOrEqual(PLOTLY_TITLE_WRAP_MAXCHARS);
    }
  });

  it('ellipsizes over-long legend names to the cap', () => {
    const out: any = preprocessPlotlySpec(longSpec() as any);
    for (const tr of out.data) {
      expect(tr.name.length).toBeLessThanOrEqual(PLOTLY_LEGEND_NAME_MAXCHARS);
      expect(tr.name.endsWith('\u2026')).toBe(true);
    }
  });

  it('is theme-independent: identical output for two invocations (pure transform)', () => {
    const a = JSON.stringify(preprocessPlotlySpec(longSpec() as any));
    const b = JSON.stringify(preprocessPlotlySpec(longSpec() as any));
    expect(a).toBe(b);
  });

  it('leaves a normal short-title / short-name spec byte-identical', () => {
    const spec = {
      data: [{ type: 'scatter', name: 'series A', x: [1, 2], y: [3, 4] }],
      layout: { title: { text: 'Short title' }, xaxis: { title: { text: 'X' } } },
    };
    const before = JSON.parse(JSON.stringify(spec));
    const wl = wrapLongTitles(before.layout);
    expect(wl).toBe(before.layout); // returned by reference (no-op)
    expect(ellipsizeLongLegendNames(before.data)).toBe(before.data);
  });
});
