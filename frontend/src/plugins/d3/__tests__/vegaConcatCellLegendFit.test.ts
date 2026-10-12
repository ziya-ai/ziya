/**
 * A top/bottom legend wider than its own hconcat cell (fitNarrowConcatCellLegends).
 *
 * Reported spec (2026-10-10, "GGMA broken-merlin items"): three hconcat
 * panels, a 14px colour stripe carrying a bottom legend with `columns: 3`,
 * a 200px text panel and a 260px heatmap with its own bottom gradient legend.
 * Vega sizes every grid column to the full bounds of its cell, guides
 * included, so the stripe's column came out as wide as its legend. Measured
 * with vega-lite + vega on the full spec (not assumed):
 *
 *   as given             panels at x = 0 / 530 / 834, stripe legend 0..524
 *   repaired (this pass) panels at x = 0 / 18 / 222,  stripe legend 0..168,
 *                        heatmap legend 222..422 — no overlap
 *
 * Our pipeline rewrote nothing layout-relevant for this spec, so the defect
 * was vega-lite's, and nothing repaired it: relocateOverflowingSideLegend
 * handles side legends at whole-chart level and skips authored orients.
 *
 * DIRECTION: fitNarrowConcatCellLegends does not exist before the fix, so this
 * suite cannot compile on the unpatched tree; the seam block additionally
 * fails if the helper exists but the plugin never calls it.
 */
import * as fs from 'fs';
import * as path from 'path';
import { fitNarrowConcatCellLegends, LEGEND_ENTRY_CHROME_PX } from '../vegaLegendPlacement';

// Deterministic widths: setupTests stubs measureText at a flat 8px/char,
// which is not font-proportional. Use the estimator's own fallback
// (0.52 * fontPx per char), as vegaLegendPlacement.test.ts does.
let savedGetContext: any;
beforeAll(() => {
  savedGetContext = (HTMLCanvasElement.prototype as any).getContext;
  (HTMLCanvasElement.prototype as any).getContext = () => null;
});
afterAll(() => {
  (HTMLCanvasElement.prototype as any).getContext = savedGetContext;
});

const FAMILIES = ['RF-silent', 'Mode A (832)', 'MAC ARC / RX dead (843)',
  'Offline / reflash / other', 'TX link-up frozen (watch)'];
// Longest family label is 25 chars -> ceil(25 * 10 * 0.52) = 130px + chrome.
const ENTRY_PX = 130 + LEGEND_ENTRY_CHROME_PX;

const SORT = { field: 'mean', order: 'descending' };

/** The reported spec, reduced to its layout-relevant structure. */
const reportedSpec = (): any => ({
  title: 'GGMA broken-merlin items',
  data: { values: FAMILIES.map((f, i) => ({ label: `ant${i}`, day: 'D1', bp: i / 2, family: f, cls: 'OK', mean: i })) },
  spacing: 4,
  resolve: { scale: { color: 'independent' } },
  hconcat: [
    {
      width: 14, height: 560, mark: 'rect',
      encoding: {
        y: { field: 'label', type: 'nominal', sort: SORT, title: null, axis: { labelFontSize: 10, labelLimit: 320 } },
        color: {
          field: 'family', type: 'nominal', title: 'failure type',
          scale: { domain: FAMILIES, range: ['#7b2cbf', '#d00000', '#e85d04', '#495057', '#a8dadc'] },
          legend: { orient: 'bottom', columns: 3 },
        },
      },
    },
    {
      width: 200, height: 560, title: 'class now',
      mark: { type: 'text', align: 'left', fontSize: 10 },
      encoding: {
        y: { field: 'label', type: 'nominal', sort: SORT, axis: null },
        text: { field: 'cls' }, x: { value: 2 },
        color: { condition: { test: "indexof(datum.cls,'IMPACTING')>=0", value: '#c1121f' }, value: '#555' },
      },
    },
    {
      width: 260, height: 560,
      layer: [
        {
          mark: 'rect',
          encoding: {
            x: { field: 'day', type: 'ordinal', title: null, axis: { orient: 'top' } },
            y: { field: 'label', type: 'nominal', sort: SORT, axis: null },
            color: { field: 'bp', type: 'quantitative', scale: { scheme: 'orangered', domain: [0, 2.3] }, legend: { orient: 'bottom' } },
          },
        },
        {
          mark: { type: 'text', fontSize: 10 },
          encoding: {
            x: { field: 'day', type: 'ordinal' },
            // No axis property here: the rect layer's axis:null hides the
            // merged axis (observed in vega-lite), so the cell has no y axis.
            y: { field: 'label', type: 'nominal', sort: SORT },
            text: { field: 'bp', format: '.2f', type: 'quantitative' },
          },
        },
      ],
    },
  ],
});

describe('fitNarrowConcatCellLegends — the reported spec', () => {
  it('flushes the row and fits the stripe legend into the room before the heatmap legend', () => {
    const spec = reportedSpec();
    const fits = fitNarrowConcatCellLegends(spec);
    expect(spec.bounds).toBe('flush');
    // Room = 14 + 4 + 200 = 218px before the heatmap's own bottom legend.
    expect(Math.floor((218 - 18) / ENTRY_PX)).toBe(1);
    expect(spec.hconcat[0].encoding.color.legend).toEqual({ orient: 'bottom', columns: 1 });
    expect(fits).toEqual([expect.objectContaining({
      cellIndex: 0, channel: 'color', cellWidthPx: 14, columns: 1, flushed: true,
    })]);
    expect(fits[0].estimatedLegendPx).toBe(3 * ENTRY_PX + 18);
  });

  it('leaves the other panels and the gradient legend as authored', () => {
    const spec = reportedSpec();
    const before = reportedSpec();
    fitNarrowConcatCellLegends(spec);
    expect(spec.hconcat[1]).toEqual(before.hconcat[1]);
    expect(spec.hconcat[2]).toEqual(before.hconcat[2]);
    expect(spec.hconcat.map((c: any) => c.width)).toEqual([14, 200, 260]);
  });

  it('is idempotent', () => {
    const spec = reportedSpec();
    fitNarrowConcatCellLegends(spec);
    const once = JSON.parse(JSON.stringify(spec));
    expect(fitNarrowConcatCellLegends(spec)).toEqual([]);
    expect(spec).toEqual(once);
  });
});

describe('fitNarrowConcatCellLegends — room and fallbacks', () => {
  it('with no later same-side legend, the legend may span the whole row', () => {
    const spec = reportedSpec();
    delete spec.hconcat[2].layer[0].encoding.color.legend;   // heatmap legend back to the right
    spec.hconcat[2].layer[0].encoding.color.legend = null;    // ...and hidden
    const fits = fitNarrowConcatCellLegends(spec);
    // Room 14+4+200+4+260 = 482: the authored 3 columns (480px) fit.
    expect(spec.bounds).toBe('flush');
    expect(spec.hconcat[0].encoding.color.legend.columns).toBe(3);
    expect(fits).toHaveLength(1);
    expect(fits[0]).toMatchObject({ columns: 3, flushed: true });
  });

  it('only narrows the columns when one column fits inside the cell', () => {
    const spec = reportedSpec();
    spec.hconcat[0].width = 400;
    delete spec.hconcat[0].encoding.color.legend.columns;     // default: all 5 in one row
    const fits = fitNarrowConcatCellLegends(spec);
    expect(spec.bounds).toBeUndefined();
    expect(spec.hconcat[0].encoding.color.legend.columns).toBe(Math.floor((400 - 18) / ENTRY_PX));
    expect(fits[0]).toMatchObject({ cellIndex: 0, columns: 2, flushed: false });
  });

  it('does not flush when a later cell has a visible y axis; falls back to one column', () => {
    const spec = reportedSpec();
    spec.hconcat[1].encoding.y.axis = {};                     // visible left axis on panel 2
    const fits = fitNarrowConcatCellLegends(spec);
    expect(spec.bounds).toBeUndefined();
    expect(spec.hconcat[0].encoding.color.legend.columns).toBe(1);
    expect(fits[0]).toMatchObject({ columns: 1, flushed: false });
  });

  it('does not flush when a non-last cell has a default (right-hand) legend', () => {
    const spec = reportedSpec();
    spec.hconcat[1].encoding.color = { field: 'cls', type: 'nominal' };
    fitNarrowConcatCellLegends(spec);
    expect(spec.bounds).toBeUndefined();
    expect(spec.hconcat[0].encoding.color.legend.columns).toBe(1);
  });

  it('respects an authored bounds value', () => {
    const spec = reportedSpec();
    spec.bounds = 'full';
    fitNarrowConcatCellLegends(spec);
    expect(spec.bounds).toBe('full');
    expect(spec.hconcat[0].encoding.color.legend.columns).toBe(1);
  });

  it('leaves a legend that fits its cell untouched', () => {
    const spec = reportedSpec();
    spec.hconcat[0].width = 600;
    const before = JSON.parse(JSON.stringify(spec));
    expect(fitNarrowConcatCellLegends(spec)).toEqual([]);
    expect(spec).toEqual(before);
  });

  it('does not touch gradient legends or side legends', () => {
    const spec = reportedSpec();
    spec.hconcat[0].encoding.color.legend = {};               // default orient: right
    const before = JSON.parse(JSON.stringify(spec));
    expect(fitNarrowConcatCellLegends(spec)).toEqual([]);
    expect(spec).toEqual(before);
  });

  it('visits hconcat rows nested in a vconcat', () => {
    const row = reportedSpec();
    const spec = { data: row.data, vconcat: [{ spacing: 4, hconcat: row.hconcat }] };
    fitNarrowConcatCellLegends(spec);
    expect((spec.vconcat[0] as any).bounds).toBe('flush');
    expect(spec.vconcat[0].hconcat[0].encoding.color.legend.columns).toBe(1);
  });
});

describe('seam — vegaLitePlugin applies the cell fit', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'vegaLitePlugin.ts'), 'utf8');

  it('imports the helper', () => {
    expect(src).toMatch(/import\s*\{[^}]*fitNarrowConcatCellLegends[^}]*\}\s*from\s*'\.\/vegaLegendPlacement'/);
  });

  it('calls it on vegaSpec after the side-legend relocation', () => {
    const fit = src.indexOf('fitNarrowConcatCellLegends(vegaSpec)');
    const relocate = src.indexOf('relocateOverflowingSideLegend(');
    expect(relocate).toBeGreaterThan(-1);
    expect(fit).toBeGreaterThan(relocate);
  });
});
