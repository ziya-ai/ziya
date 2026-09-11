/**
 * Preprocessor fixes surfaced by the "handover-loss, one unit" chart: a
 * layered log-scale bar chart with long nominal y-labels, a shared `x2`
 * inherited by text sub-layers, and left-aligned note labels anchored at the
 * upper end of the x domain.
 *
 * Three independent defects, each with a direction check that fails on the
 * unpatched behaviour:
 *
 *   1. clampAxisLabelLimits capped every authored labelLimit at 320px no matter
 *      how wide the chart was, so a 75-char row label was truncated by Ziya
 *      even when the author asked for 480px. -> axisLabelLimitCap(width).
 *   2. A shared encoding.x2 is inherited by text layers, which Vega-Lite drops
 *      with `WARN x2 dropped as it is incompatible with "text"`.
 *      -> sinkSecondaryChannels moves it onto the layers that can use it.
 *   3. Note labels extending past the SVG's right edge were clipped; nothing
 *      measured them. -> computeTextOverflow / growPadding drive a post-render
 *      view.padding() correction.
 */
import {
  MAX_AXIS_LABEL_LIMIT,
  AXIS_LABEL_LIMIT_HARD_MAX,
  axisLabelLimitCap,
  sinkSecondaryChannels,
} from '../vegaLayerDefaults';
import {
  computeTextOverflow,
  growPadding,
  normalizePadding,
  TEXT_OVERFLOW_MAX_PAD_FRACTION,
} from '../vegaTextOverflow';

// ── 1. width-aware labelLimit cap ────────────────────────────────────────────
describe('axisLabelLimitCap', () => {
  it('is exactly the legacy 320px at the 400px detached-container floor', () => {
    // Direction: unknown width must not change behaviour.
    expect(axisLabelLimitCap(400)).toBe(MAX_AXIS_LABEL_LIMIT);
    expect(axisLabelLimitCap(0)).toBe(MAX_AXIS_LABEL_LIMIT);
    expect(axisLabelLimitCap(NaN)).toBe(MAX_AXIS_LABEL_LIMIT);
  });

  it('lets an authored 480px limit survive in an 1100px chart', () => {
    // The handover chart: 75-char label at 10.5px needs ~480px; author asked
    // for 480. Pre-fix the clamp forced 320 and ellipsised the label.
    expect(axisLabelLimitCap(1100)).toBeGreaterThanOrEqual(480);
  });

  it('never hands the axis more than 45% of the width, and never more than the hard max', () => {
    expect(axisLabelLimitCap(1100)).toBeLessThanOrEqual(Math.floor(1100 * 0.45));
    expect(axisLabelLimitCap(5000)).toBe(AXIS_LABEL_LIMIT_HARD_MAX);
  });

  it('is monotonic in width', () => {
    const w = [400, 600, 800, 1000, 1200, 1600];
    const caps = w.map(axisLabelLimitCap);
    for (let i = 1; i < caps.length; i++) expect(caps[i]).toBeGreaterThanOrEqual(caps[i - 1]);
  });
});

// ── 2. shared x2 inherited by text layers ────────────────────────────────────
const handoverLayer = () => ({
  data: { values: [{ lo: 10, hi: 100, note: 'n' }] },
  encoding: {
    y: { field: 'row', type: 'nominal' },
    x: { field: 'lo', type: 'quantitative', scale: { type: 'log' } },
    x2: { field: 'hi', type: 'quantitative' },
    color: { field: 'tag', type: 'nominal' },
  },
  layer: [
    { mark: { type: 'bar', height: 13 } },
    {
      mark: { type: 'text', align: 'left', dx: 6 },
      encoding: { x: { field: 'hi', type: 'quantitative' }, text: { field: 'note' } },
    },
  ],
});

describe('sinkSecondaryChannels', () => {
  it('moves the shared x2 onto the bar layer and removes it from the parent', () => {
    const view = handoverLayer();
    const moved = sinkSecondaryChannels(view);
    expect(moved).toEqual(['x2@0']);
    expect(view.encoding.x2).toBeUndefined();
    expect(view.layer[0].encoding.x2).toEqual({ field: 'hi', type: 'quantitative' });
  });

  it('does NOT give the text layer an x2 (that is exactly what Vega-Lite warned about)', () => {
    const view = handoverLayer();
    sinkSecondaryChannels(view);
    expect(view.layer[1].encoding.x2).toBeUndefined();
    // and the text layer's own encoding is untouched
    expect(view.layer[1].encoding.text).toEqual({ field: 'note' });
  });

  it('keeps the other shared channels shared (only x2/y2 move)', () => {
    const view = handoverLayer();
    sinkSecondaryChannels(view);
    expect(view.encoding.x).toBeDefined();
    expect(view.encoding.y).toBeDefined();
    expect(view.encoding.color).toBeDefined();
  });

  it('is a no-op when every leaf can consume the secondary channel', () => {
    // Two bar layers sharing x2: nothing would be dropped, so leave the
    // shared encoding alone (moving it is pointless churn).
    const view: any = {
      encoding: { x: { field: 'a' }, x2: { field: 'b' } },
      layer: [{ mark: 'bar' }, { mark: 'rect' }],
    };
    expect(sinkSecondaryChannels(view)).toEqual([]);
    expect(view.encoding.x2).toEqual({ field: 'b' });
  });

  it('does not overwrite a layer that already declares its own x2', () => {
    const view: any = {
      encoding: { x2: { field: 'shared' } },
      layer: [{ mark: 'bar', encoding: { x2: { field: 'own' } } }, { mark: 'text' }],
    };
    sinkSecondaryChannels(view);
    expect(view.layer[0].encoding.x2).toEqual({ field: 'own' });
    expect(view.encoding.x2).toBeUndefined();
  });

  it('pushes through a nested layer group and recurses into it', () => {
    const view: any = {
      encoding: { y2: { field: 'top' } },
      layer: [
        { mark: 'text' },
        { layer: [{ mark: 'rule' }, { mark: 'point' }] },
      ],
    };
    const moved = sinkSecondaryChannels(view);
    expect(view.encoding.y2).toBeUndefined();
    // group received it, then sank it to its rule leaf only
    expect(view.layer[1].encoding?.y2).toBeUndefined();
    expect(view.layer[1].layer[0].encoding.y2).toEqual({ field: 'top' });
    expect(view.layer[1].layer[1].encoding?.y2).toBeUndefined();
    expect(moved).toEqual(expect.arrayContaining(['y2@1', 'y2@1.0']));
  });

  it('handles a spec with no layer array', () => {
    const view: any = { mark: 'bar', encoding: { x2: { field: 'b' } } };
    expect(sinkSecondaryChannels(view)).toEqual([]);
    expect(view.encoding.x2).toEqual({ field: 'b' });
  });
});

// ── 3. text marks clipped at the SVG edge ────────────────────────────────────
const svg = { left: 0, right: 1000, top: 0, bottom: 400 };
const rect = (left: number, right: number) => ({ left, right, top: 10, bottom: 20 });

describe('computeTextOverflow', () => {
  it('reports the largest right-edge overrun, rounded up', () => {
    const o = computeTextOverflow([rect(100, 300), rect(900, 1060.3), rect(950, 1020)], svg);
    expect(o).toEqual({ right: 61, left: 0 });
  });

  it('reports a left-edge overrun independently', () => {
    expect(computeTextOverflow([rect(-30, 40)], svg)).toEqual({ right: 0, left: 30 });
  });

  it('ignores sub-pixel overrun (anti-aliasing noise) and zero-size rects', () => {
    // jsdom / a not-yet-laid-out SVG reports all-zero rects: must be a no-op.
    expect(computeTextOverflow([rect(0, 0), rect(999.5, 1000.8)], svg)).toEqual({ right: 0, left: 0 });
    expect(computeTextOverflow([], svg)).toEqual({ right: 0, left: 0 });
  });

  it('caps the correction so a pathological label cannot eat the plot', () => {
    const o = computeTextOverflow([rect(500, 5000)], svg);
    expect(o.right).toBe(1000 * TEXT_OVERFLOW_MAX_PAD_FRACTION);
  });
});

describe('normalizePadding / growPadding', () => {
  it('expands a numeric Vega padding to four sides', () => {
    expect(normalizePadding(5)).toEqual({ top: 5, right: 5, bottom: 5, left: 5 });
  });

  it('fills missing sides of an object padding with 0', () => {
    expect(normalizePadding({ right: 12 })).toEqual({ top: 0, right: 12, bottom: 0, left: 0 });
    expect(normalizePadding(undefined)).toEqual({ top: 0, right: 0, bottom: 0, left: 0 });
  });

  it('adds the overflow to the matching sides only', () => {
    expect(growPadding(5, { right: 61, left: 0 })).toEqual({ top: 5, right: 66, bottom: 5, left: 5 });
    expect(growPadding({ left: 2 }, { right: 0, left: 30 })).toEqual({ top: 0, right: 0, bottom: 0, left: 32 });
  });
});
