/**
 * Width-driven legend relocation for fixed-width (composite) Vega-Lite specs.
 *
 * Observed defect (PDF export, 2026-09-30): a five-panel `vconcat` timeline
 * with 620px views and a three-column colour legend on the right rendered as
 * a ~920px SVG. In the ~650px print column the export pipeline scaled the
 * whole figure to ~0.7x — axis labels at 6-7pt — and left an empty band
 * beside the three-row legend. The legend would have fitted below the plot
 * at no width cost, but optimizeLegendLayout never visits concat sub-views
 * and only reacts to entry count.
 *
 * DIRECTION: `relocateOverflowingSideLegend` does not exist before the fix,
 * so this file fails to compile on the unpatched tree. The seam test at the
 * bottom additionally fails if the helper exists but the plugin never calls
 * it with the sizing result and pane width.
 */
import * as fs from 'fs';
import * as path from 'path';
import {
  collectLegendSites,
  estimateChartWidthPx,
  estimateSideLegendWidthPx,
  relocateOverflowingSideLegend,
  Y_AXIS_ROOM_PX,
} from '../vegaLegendPlacement';

const LEGEND_DOMAIN = [
  'locked A', 'locked B', 'TX on A', 'A left idle', 'arrived via A',
  'died at satellite', 'copy held at KPOP', 'copy replayed on B', 'rescued via B',
];

/** The reported spec, reduced to its legend- and width-relevant structure. */
const reportedSpec = () => ({
  resolve: { scale: { color: 'shared', x: 'shared' } },
  config: { legend: { labelFontSize: 10, columns: 3 } },
  vconcat: [
    {
      width: 620, height: 36,
      layer: [
        {
          data: { values: [{ row: 'RX CT beam', k: 'locked A', t0: 2, t1: 12 }] },
          mark: { type: 'bar' },
          encoding: {
            x: { field: 't0', type: 'quantitative' },
            x2: { field: 't1' },
            y: { field: 'row', type: 'nominal' },
            color: { field: 'k', type: 'nominal', scale: { domain: LEGEND_DOMAIN } },
          },
        },
        { data: { values: [{ t: 7 }] }, mark: 'rule', encoding: { x: { field: 't', type: 'quantitative' } } },
      ],
    },
    {
      width: 620, height: 58,
      layer: [
        {
          data: { values: [{ row: 'TX A', k: 'TX on A', t0: 0, t1: 4.5 }] },
          mark: { type: 'bar' },
          encoding: {
            x: { field: 't0', type: 'quantitative' },
            y: { field: 'row', type: 'nominal' },
            color: { field: 'k', type: 'nominal' },
          },
        },
      ],
    },
  ],
  width: 400,
  autosize: { type: 'pad', contains: 'padding' },
});

const PRINT_PANE_PX = 650;   // 210mm − 2×19mm at 96dpi
const CHAT_PANE_PX = 1200;

describe('collectLegendSites — walks composite trees', () => {
  it('finds colour legends inside vconcat > layer, with labels from data and scale.domain', () => {
    const sites = collectLegendSites(reportedSpec());
    expect(sites.map(s => s.channel)).toEqual(['color', 'color']);
    // First site carries the authored 9-entry domain (plus the data row's value, already in it).
    expect(sites[0].labels).toEqual(expect.arrayContaining(LEGEND_DOMAIN));
    expect(sites[0].labels.length).toBe(LEGEND_DOMAIN.length);
    // Second site only sees its own data.
    expect(sites[1].labels).toEqual(['TX on A']);
  });

  it('skips hidden legends and identity scales', () => {
    const spec = {
      layer: [
        { mark: 'bar', encoding: { color: { field: 'c', legend: null } } },
        { mark: 'bar', encoding: { color: { field: 'c', scale: null } } },
        { mark: 'bar', encoding: { color: { field: 'c', legend: false } } },
      ],
    };
    expect(collectLegendSites(spec)).toEqual([]);
  });

  it('inherits parent data for a child without its own', () => {
    const spec = {
      data: { values: [{ c: 'a' }, { c: 'b' }] },
      hconcat: [{ mark: 'bar', encoding: { color: { field: 'c' } } }],
    };
    expect(collectLegendSites(spec)[0].labels).toEqual(['a', 'b']);
  });
});

describe('estimateChartWidthPx', () => {
  it('takes the widest sub-view over a smaller top-level width (the reported 620 vs 400)', () => {
    expect(estimateChartWidthPx(reportedSpec(), 400)).toBe(620);
  });
  it('a top-level width never short-circuits the walk (it is only the child default)', () => {
    // First version returned 400 here: `own` was returned before the children were visited.
    expect(estimateChartWidthPx({ width: 400, vconcat: [{ width: 620 }, { mark: 'bar' }] }, undefined)).toBe(620);
    // …but it still counts when every child is wider-or-equal to nothing.
    expect(estimateChartWidthPx({ width: 400, vconcat: [{ mark: 'bar' }] }, undefined)).toBe(400);
  });
  it('sums hconcat children, filling an unsized child from the node default', () => {
    expect(estimateChartWidthPx({ hconcat: [{ width: 300 }, { width: 250 }] }, undefined)).toBe(550);
    expect(estimateChartWidthPx({ width: 200, hconcat: [{ width: 300 }, { mark: 'bar' }] }, undefined)).toBe(500);
  });
  it("is null for a 'container' chart with no numeric width anywhere", () => {
    expect(estimateChartWidthPx({ mark: 'bar', encoding: {} }, 'container')).toBeNull();
  });
});

describe('relocateOverflowingSideLegend — the reported case', () => {
  it('moves the legend below the plot when chart + axis + side legend exceed the print column', () => {
    const spec = reportedSpec();
    const chartW = estimateChartWidthPx(spec, 400)!;
    const sideW = estimateSideLegendWidthPx(spec, collectLegendSites(spec));
    // Sanity on the premise: 620 + axis + 3-column legend really does not fit 650.
    expect(chartW + Y_AXIS_ROOM_PX + sideW).toBeGreaterThan(PRINT_PANE_PX);

    const result = relocateOverflowingSideLegend(spec, chartW, PRINT_PANE_PX);
    expect(result).not.toBeNull();
    expect(result!.orient).toBe('bottom');
    expect(spec.config.legend).toMatchObject({ orient: 'bottom', labelFontSize: 10 });
    // Columns are fitted to the 620px plot, several per row, never more than entries.
    expect(result!.columns).toBeGreaterThan(1);
    expect(result!.columns).toBeLessThanOrEqual(LEGEND_DOMAIN.length);
    expect(spec.config.legend.columns).toBe(result!.columns);
    // Everything else about the spec is untouched.
    expect(spec.vconcat[0].width).toBe(620);
    expect(spec.resolve).toEqual({ scale: { color: 'shared', x: 'shared' } });
  });

  it('leaves the spec byte-identical in a wide chat pane where the side legend fits', () => {
    const spec = reportedSpec();
    const before = JSON.stringify(spec);
    expect(relocateOverflowingSideLegend(spec, estimateChartWidthPx(spec, 400), CHAT_PANE_PX)).toBeNull();
    expect(JSON.stringify(spec)).toBe(before);
  });

  // Second reported spec (2026-09-30): a single layered chart with
  // width:'container' + autosize fit-x. fit-x SUBTRACTS the side legend from
  // the pane, so in a 650px print column a five-entry legend with the label
  // "sent on A - LOST (no copy exists)" left ~330px of plot beside ~190px of
  // legend. In a 1200px chat pane the same subtraction is 16% and harmless.
  const containerSpec = () => ({
    width: 'container', height: 250,
    config: { legend: { labelFontSize: 11 } },
    layer: [{
      mark: 'rect',
      data: { values: [{ k: 'CT beam locked to A' }] },
      encoding: { color: { field: 'k', type: 'nominal', scale: { domain: [
        'CT beam locked to A', 'CT beam locked to B', 'sent on A - delivered',
        'sent on A - LOST (no copy exists)', 'sent on B - delivered',
      ] } } },
    }],
    autosize: { type: 'fit-x', contains: 'padding' },
  });

  it('container chart: moves a legend that would take more than a quarter of the print pane', () => {
    const spec = containerSpec();
    const result = relocateOverflowingSideLegend(spec, null, PRINT_PANE_PX);
    expect(result).not.toBeNull();
    expect(result!.orient).toBe('bottom');
    expect(spec.config.legend).toMatchObject({ orient: 'bottom', labelFontSize: 11 });
    expect(result!.columns).toBeGreaterThanOrEqual(2);
    expect(result!.columns).toBeLessThanOrEqual(5);
    // Column room is the pane minus the y axis, never the (unknown) chart width.
    expect(result!.labelLimit!).toBeLessThanOrEqual(Math.floor((PRINT_PANE_PX - Y_AXIS_ROOM_PX) / result!.columns));
    expect(spec.width).toBe('container'); // sizing untouched
  });

  it('container chart: left alone in a wide chat pane where the legend is a small fraction', () => {
    const spec = containerSpec();
    const before = JSON.stringify(spec);
    expect(relocateOverflowingSideLegend(spec, null, CHAT_PANE_PX)).toBeNull();
    expect(JSON.stringify(spec)).toBe(before);
  });

  it('container chart with a short single-entry legend is left alone even in a narrow pane', () => {
    const spec = { mark: 'bar', data: { values: [{ c: 'a' }] }, encoding: { color: { field: 'c' } } };
    expect(relocateOverflowingSideLegend(spec, null, 300)).toBeNull();
    expect((spec as any).config).toBeUndefined();
  });

  it('does nothing when there is no legend to move', () => {
    const spec = { vconcat: [{ width: 900, mark: 'bar', encoding: { x: { field: 'a' } } }] };
    expect(relocateOverflowingSideLegend(spec, 900, PRINT_PANE_PX)).toBeNull();
  });

  it('respects an authored orient in config (author intent wins even when it overflows)', () => {
    const spec = reportedSpec();
    (spec.config.legend as any).orient = 'right';
    expect(relocateOverflowingSideLegend(spec, 620, PRINT_PANE_PX)).toBeNull();
    expect(spec.config.legend).toMatchObject({ orient: 'right', columns: 3 });
  });

  it('respects an authored orient on the encoding', () => {
    const spec = reportedSpec();
    (spec.vconcat[0].layer[0].encoding as any).color.legend = { orient: 'top' };
    expect(relocateOverflowingSideLegend(spec, 620, PRINT_PANE_PX)).toBeNull();
    expect((spec.config as any).legend.orient).toBeUndefined();
  });

  it('carries an authored per-channel column count along with the relocation', () => {
    const spec = reportedSpec();
    const color = (spec.vconcat[0].layer[0].encoding as any).color;
    color.legend = { columns: 3 };
    const result = relocateOverflowingSideLegend(spec, 620, PRINT_PANE_PX)!;
    expect(result).not.toBeNull();
    // A per-channel `columns: 3` chosen for the side layout would override the
    // config; it must now agree with the fitted bottom layout.
    expect(color.legend.columns).toBe(result.columns);
  });

  it('lifts the 160px default labelLimit to fit the longest label in the per-column room', () => {
    // Observed after the relocation landed: "via B - dies at sat B (CT not yet on B)"
    // still printed as "…(CT not yet o…" because Vega's default labelLimit is
    // 160px and no other pass visits vconcat sub-views.
    const spec = reportedSpec();
    (spec.vconcat[0].layer[0].encoding as any).color.scale.domain = [
      'CT beam locked to A', 'via B - dies at sat B (CT not yet on B)',
    ];
    delete (spec.config.legend as any).columns;
    const result = relocateOverflowingSideLegend(spec, 620, PRINT_PANE_PX)!;
    expect(result).not.toBeNull();
    expect(result.labelLimit).not.toBeNull();
    expect(result.labelLimit!).toBeGreaterThan(160);
    // Never wider than the room one column actually has.
    expect(result.labelLimit!).toBeLessThanOrEqual(Math.floor(620 / result.columns));
    expect(spec.config.legend.labelLimit).toBe(result.labelLimit);
  });

  it('leaves an authored labelLimit alone when relocating', () => {
    const spec = reportedSpec();
    (spec.config.legend as any).labelLimit = 90;
    const result = relocateOverflowingSideLegend(spec, 620, PRINT_PANE_PX)!;
    expect(result.labelLimit).toBeNull();
    expect(spec.config.legend.labelLimit).toBe(90);
  });

  it('a single-column side legend with short labels on a chart that fits is left alone', () => {
    const spec = {
      vconcat: [{
        width: 400, mark: 'bar',
        data: { values: [{ c: 'a' }, { c: 'b' }] },
        encoding: { color: { field: 'c' } },
      }],
    };
    expect(relocateOverflowingSideLegend(spec, 400, PRINT_PANE_PX)).toBeNull();
  });
});

describe('seam — vegaLitePlugin actually applies the relocation', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'vegaLitePlugin.ts'), 'utf8');

  it('imports the helper', () => {
    expect(src).toMatch(/import\s*\{[^}]*relocateOverflowingSideLegend[^}]*\}\s*from\s*'\.\/vegaLegendPlacement'/);
  });

  it('calls it with the resolved sizing width and the pane width, after optimizeLegendLayout', () => {
    const call = src.indexOf('relocateOverflowingSideLegend(');
    const optimise = src.indexOf('optimizeLegendLayout(vegaSpec);');
    expect(call).toBeGreaterThan(-1);
    expect(optimise).toBeGreaterThan(-1);
    expect(call).toBeGreaterThan(optimise);
    const window = src.slice(call, call + 200);
    expect(window).toContain('estimateChartWidthPx(vegaSpec, _sizing.width)');
    expect(window).toContain('fitWidthPx');
  });
});
