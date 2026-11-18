/**
 * An authored legend labelLimit too small to show any text.
 *
 * Reported 2026-11-12: a two-panel vconcat loss chart carried
 * `config.legend.labelLimit: 6`, almost certainly meant as characters.  Vega
 * reads labelLimit in px, so every legend label rendered as "…".  Every
 * legend-fitting pass (optimizeLegendLayout, relocateOverflowingSideLegend)
 * treats an authored labelLimit as intent and leaves it alone, so nothing
 * repaired it.
 *
 * repairTinyLegendLabelLimits replaces a positive labelLimit below
 * TINY_LEGEND_LABEL_LIMIT_PX (config-level and per-channel) with a fitted
 * width.  0 is Vega's "no truncation" and is left alone, as is any usable
 * authored value.
 *
 * DIRECTION: every test fails before the fix, because the function does not
 * exist; the seam block fails until vegaLitePlugin calls it.
 */
import * as fs from 'fs';
import * as path from 'path';
import {
  legendWidthPx, repairTinyLegendLabelLimits, TINY_LEGEND_LABEL_LIMIT_PX,
} from '../vegaLegendPlacement';
import { estimateTextWidthPx, legendLabelRoomPx, VEGA_LEGEND_LABEL_FONT_PX } from '../vegaTextFit';

// jest-canvas-mock measures a flat 8px per character; use the per-character
// estimate instead, as vegaLegendPlacement.test.ts does.
let savedGetContext: any;
beforeAll(() => {
  savedGetContext = (HTMLCanvasElement.prototype as any).getContext;
  (HTMLCanvasElement.prototype as any).getContext = () => null;
});
afterAll(() => {
  (HTMLCanvasElement.prototype as any).getContext = savedGetContext;
});

const LONG = 'excluding 18 heavy-tail CTs';

/** The reported spec's shape: labels are derived by a calculate transform,
 *  so they cannot be read from data.values. */
const reported = () => ({
  data: { values: [{ t: '2026-11-05T01:00Z', ua: 4.6, ue: 4.5, da: 2, de: 2.03 }] },
  vconcat: [
    {
      width: 640,
      transform: [{ fold: ['ua', 'ue'], as: ['k', 'v'] },
        { calculate: `datum.k=='ua'?'all roster CTs':'${LONG}'`, as: 's' }],
      mark: 'line',
      encoding: { x: { field: 't', type: 'temporal' }, y: { field: 'v', type: 'quantitative' },
        color: { field: 's', type: 'nominal', title: null } },
    },
    {
      width: 640,
      transform: [{ fold: ['da', 'de'], as: ['k', 'v'] },
        { calculate: `datum.k=='da'?'all roster CTs':'${LONG}'`, as: 's' }],
      mark: 'line',
      encoding: { x: { field: 't', type: 'temporal' }, y: { field: 'v', type: 'quantitative' },
        color: { field: 's', type: 'nominal', title: null } },
    },
  ],
  resolve: { scale: { color: 'independent' } },
  config: { legend: { orient: 'bottom', columns: 3, labelLimit: 6 } },
});

/** Labels readable from data.values. */
const known = (legendCfg: any, channelLegend?: any) => ({
  data: { values: [{ s: 'all roster CTs', v: 1 }, { s: LONG, v: 2 }] },
  mark: 'point',
  encoding: {
    y: { field: 'v', type: 'quantitative' },
    color: { field: 's', type: 'nominal', ...(channelLegend !== undefined ? { legend: channelLegend } : {}) },
  },
  config: { legend: legendCfg },
});

describe('repairTinyLegendLabelLimits', () => {
  it('reported spec: config labelLimit 6 becomes the room a bottom 3-column legend has', () => {
    const spec: any = reported();
    const out = repairTinyLegendLabelLimits(spec, 900);
    expect(out).toEqual([{ site: 'config', from: 6, to: legendLabelRoomPx(900, 'bottom', 3) }]);
    expect(spec.config.legend.labelLimit).toBe(legendLabelRoomPx(900, 'bottom', 3));
    // Wide enough for the longest label the transform produces.
    expect(spec.config.legend.labelLimit).toBeGreaterThan(estimateTextWidthPx(LONG, VEGA_LEGEND_LABEL_FONT_PX));
    // Other authored legend settings are kept.
    expect(spec.config.legend.orient).toBe('bottom');
    expect(spec.config.legend.columns).toBe(3);
  });

  it('known labels: fitted to the longest label, which then fits', () => {
    const spec: any = known({ labelLimit: 6 });
    repairTinyLegendLabelLimits(spec, 1000);
    const limit = spec.config.legend.labelLimit;
    expect(limit).toBeGreaterThan(estimateTextWidthPx(LONG, VEGA_LEGEND_LABEL_FONT_PX));
    expect(limit).toBeGreaterThanOrEqual(TINY_LEGEND_LABEL_LIMIT_PX);
  });

  it('per-channel labelLimit is repaired; config is not touched', () => {
    const spec: any = known({ orient: 'right' }, { labelLimit: 4, title: 'Series' });
    const out = repairTinyLegendLabelLimits(spec, 1000);
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ site: 'color', from: 4 });
    expect(spec.encoding.color.legend.labelLimit).toBeGreaterThan(
      estimateTextWidthPx(LONG, VEGA_LEGEND_LABEL_FONT_PX));
    expect(spec.encoding.color.legend.title).toBe('Series');
    expect(spec.config.legend).toEqual({ orient: 'right' });
  });

  it('a config fit uses room, not the known labels, when some legend labels are unknown', () => {
    // One site with readable labels, one transform-derived: the known
    // labels' longest could be shorter than the unknown ones.
    const spec: any = reported();
    spec.vconcat[0].data = { values: [{ s: 'a' }] };
    repairTinyLegendLabelLimits(spec, 900);
    expect(spec.config.legend.labelLimit).toBe(legendLabelRoomPx(900, 'bottom', 3));
  });

  it.each([
    ['a usable authored value', 90],
    ['the threshold itself', TINY_LEGEND_LABEL_LIMIT_PX],
    ["Vega's no-truncation 0", 0],
  ])('leaves %s unchanged', (_label, v) => {
    const spec: any = known({ labelLimit: v }, { labelLimit: v });
    const before = JSON.stringify(spec);
    expect(repairTinyLegendLabelLimits(spec, 1000)).toEqual([]);
    expect(JSON.stringify(spec)).toBe(before);
  });

  it('adds nothing when no labelLimit was authored', () => {
    const spec: any = known({ orient: 'bottom' });
    expect(repairTinyLegendLabelLimits(spec, 1000)).toEqual([]);
    expect(spec.config.legend).toEqual({ orient: 'bottom' });
    expect(spec.encoding.color.legend).toBeUndefined();
  });
});

describe('legendWidthPx (the width a legend lays out across)', () => {
  it('an unmeasured pane: the chart width, so the reported legend fits', () => {
    // Reproduced 2026-11-12: with the unmeasured 440px floor the repair set
    // 103px and "excluding 18 heavy-tail CTs" still showed as "excluding 18 heav…".
    const w = legendWidthPx(null, 640, 440);
    expect(w).toBe(640 + 40);
    const spec: any = reported();
    repairTinyLegendLabelLimits(spec, w);
    expect(spec.config.legend.labelLimit).toBeGreaterThan(
      estimateTextWidthPx(LONG, VEGA_LEGEND_LABEL_FONT_PX));
    // Positive control: the floor alone is too narrow for that label.
    const floor: any = reported();
    repairTinyLegendLabelLimits(floor, 440);
    expect(floor.config.legend.labelLimit).toBeLessThan(
      estimateTextWidthPx(LONG, VEGA_LEGEND_LABEL_FONT_PX));
  });

  it('the wider of a measured pane and the chart', () => {
    expect(legendWidthPx(1200, 640, 440)).toBe(1200);
    expect(legendWidthPx(500, 640, 440)).toBe(680);
  });

  it('neither known: the fallback', () => {
    expect(legendWidthPx(null, null, 440)).toBe(440);
  });
});

describe('seam: vegaLitePlugin repairs before the legend passes', () => {
  const src = fs.readFileSync(path.resolve(__dirname, '../vegaLitePlugin.ts'), 'utf8');

  it('imports and calls the repair with the legend width, not the bare pane floor', () => {
    expect(src).toMatch(/import \{[^}]*repairTinyLegendLabelLimits[^}]*\} from '\.\/vegaLegendPlacement'/);
    expect(src).not.toMatch(/repairTinyLegendLabelLimits\(vegaSpec, fitWidthPx\)/);
    expect(src).toMatch(/legendWidthPx\(\s*legendPaneKnown,\s*estimateChartWidthPx\(vegaSpec, _sizing\.width\)/);
    expect(src).toMatch(/repairTinyLegendLabelLimits\(vegaSpec, legendRoomPx\)/);
  });

  it('runs before optimizeLegendLayout, which treats an authored limit as intent', () => {
    const call = src.indexOf('repairTinyLegendLabelLimits(vegaSpec, legendRoomPx)');
    const optimize = src.indexOf('optimizeLegendLayout(vegaSpec);');
    expect(call).toBeGreaterThan(-1);
    expect(optimize).toBeGreaterThan(call);
  });
});
