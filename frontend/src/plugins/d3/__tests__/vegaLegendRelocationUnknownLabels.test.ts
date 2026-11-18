/**
 * relocateOverflowingSideLegend when the legend's labels cannot be read from
 * the data.
 *
 * Reported 2026-11-13 (second report of the same chart; the first, on
 * 2026-11-12, was attributed to an authored `labelLimit: 6`): a faceted line
 * chart coloured by `stat`, a field produced by a `fold` transform, showed
 * "…" for all four legend entries.  The spec as authored has no legend
 * config at all.  `stat` is not a column of data.values, so the site's label
 * list is empty, the "longest label" is 0px, and the relocation wrote
 * `labelLimit = min(0 + 6, room) = 6` into config.legend.  The exported spec
 * (`orient: bottom, columns: 2, labelLimit: 6`) is this function's output.
 *
 * DIRECTION: the first two tests fail on the unpatched tree with
 * labelLimit === 6.  The third is the positive control that the fit to a
 * known longest label still happens.
 */
import { relocateOverflowingSideLegend } from '../vegaLegendPlacement';

let savedGetContext: any;
beforeAll(() => {
  savedGetContext = (HTMLCanvasElement.prototype as any).getContext;
  (HTMLCanvasElement.prototype as any).getContext = () => null;
});
afterAll(() => {
  (HTMLCanvasElement.prototype as any).getContext = savedGetContext;
});

/** Narrow enough that a 380px chart plus a side legend does not fit. */
const PANE_PX = 500;
const VEGA_DEFAULT_LIMIT = 160;

/** The reported chart, reduced: colour field exists only after the fold. */
const foldedSpec = (): any => ({
  data: { values: [
    { day: '2026-10-30', dir: 'ul', total: 0.58, p90: 0.75, p95: 2.87, p99: 14.07 },
    { day: '2026-10-30', dir: 'dl', total: 1.31, p90: 0.81, p95: 1.47, p99: 5.87 },
  ] },
  transform: [
    { fold: ['total', 'p90', 'p95', 'p99'], as: ['stat', 'pct'] },
    { calculate: "datum.dir == 'ul' ? 'Uplink' : 'Downlink'", as: 'direction' },
  ],
  facet: { column: { field: 'direction', type: 'nominal' } },
  spec: {
    width: 380, height: 300,
    mark: { type: 'line', point: true },
    encoding: {
      x: { field: 'day', type: 'temporal' },
      y: { field: 'pct', type: 'quantitative' },
      color: { field: 'stat', type: 'nominal', sort: ['total', 'p90', 'p95', 'p99'], title: null },
    },
  },
});

describe('relocateOverflowingSideLegend with labels produced by a transform', () => {
  it('does not write a labelLimit sized to zero-width labels', () => {
    const spec = foldedSpec();
    const result = relocateOverflowingSideLegend(spec, 380, PANE_PX);
    // The path under test ran: the legend was moved below the plot.
    expect(result).not.toBeNull();
    expect(spec.config.legend.orient).toBe('bottom');
    // Unpatched: 6.  Labels are unknown, so the limit must leave at least
    // Vega's default room (or the whole column when that is narrower).
    const room = Math.floor(380 / result!.columns);
    expect(spec.config.legend.labelLimit).toBeGreaterThanOrEqual(Math.min(VEGA_DEFAULT_LIMIT, room - 24));
    expect(spec.config.legend.labelLimit).toBeLessThanOrEqual(room);
    expect(result!.labelLimit).toBe(spec.config.legend.labelLimit);
  });

  it('does not size the limit to the known labels when another legend is unknown', () => {
    // Two legend sites: one enumerable with short labels, one not.  A limit
    // fitted to "a"/"b" would cut the unknown legend to a few pixels.
    const spec: any = {
      vconcat: [
        { width: 380, mark: 'bar', data: { values: [{ c: 'a' }, { c: 'b' }] },
          encoding: { color: { field: 'c', type: 'nominal', legend: { columns: 4 } } } },
        { width: 380, mark: 'line', data: { values: [{ v: 1 }] },
          transform: [{ fold: ['v'], as: ['made_up', 'val'] }],
          encoding: { color: { field: 'made_up', type: 'nominal', legend: { columns: 4 } } } },
      ],
    };
    const result = relocateOverflowingSideLegend(spec, 380, PANE_PX);
    expect(result).not.toBeNull();
    const room = Math.floor(380 / result!.columns);
    expect(spec.config.legend.labelLimit).toBeGreaterThanOrEqual(Math.min(VEGA_DEFAULT_LIMIT, room - 24));
  });

  it('still fits the limit to the longest label when every label is known', () => {
    const spec = foldedSpec();
    spec.spec.encoding.color.scale = { domain: ['total', 'p90', 'p95', 'p99'] };
    // Wide columns so the side layout overflows and the relocation runs.
    spec.spec.encoding.color.legend = { columns: 3 };
    const result = relocateOverflowingSideLegend(spec, 380, PANE_PX);
    expect(result).not.toBeNull();
    // "total" at the default 10-11px legend font is a few dozen px: wider
    // than the 6px bug value, far narrower than the 160px default.
    expect(spec.config.legend.labelLimit).toBeGreaterThan(20);
    expect(spec.config.legend.labelLimit).toBeLessThan(VEGA_DEFAULT_LIMIT);
  });
});
