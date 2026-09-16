/**
 * @jest-environment node
 *
 * Node environment on purpose: there is no canvas here, so these exercise the
 * per-character fallback path of estimateTextWidthPx, which is the one the
 * numbers below are calibrated against. (In a browser the canvas path is
 * used and is strictly more accurate.)
 *
 * Two user-visible defects motivated this module, both from the same chart
 * set (a 420x160 bar chart with a long title, and a stacked bar with a
 * 5-entry bottom legend):
 *   1. Title text ran off both edges of the SVG. Vega does not wrap or shrink
 *      a title, and under autosize fit-x the SVG is exactly the pane width.
 *   2. Legend entries were ellipsised ("In contact, terminal SILENT (o…")
 *      although the legend had most of the pane to itself. Vega truncates at
 *      a fixed 160px labelLimit; the plugin's D-313 limit only guards prefix
 *      collisions and, for labels sharing no prefix, collapsed to that same
 *      160px.
 */
import {
  computeLegendLabelLimitPx,
  estimateTextWidthPx,
  legendLabelRoomPx,
  wrapSpecTitles,
  wrapTextToWidth,
  VEGA_DEFAULT_LEGEND_LABEL_LIMIT,
  LEGEND_LABEL_LIMIT_UNKNOWN_WIDTH_PX,
  LEGEND_LABEL_LIMIT_MIN_PX,
} from '../vegaTextFit';

const TITLE = "KPOP's view of the same bins: % of bins with ZERO uplink packets from the terminal";
const SUBTITLE = "KPOP records arrive via ground network, so this is independent of the terminal's silence";
const LEGEND_LABELS = [
  'In contact, telemetry flowing',
  'In contact, terminal SILENT (outage)',
  'Handover window, telemetry flowing',
  'Handover window, terminal SILENT',
  'No scheduled contact',
];

describe('estimateTextWidthPx (fallback path)', () => {
  it('is monotonic in length and font size, and bold is wider', () => {
    expect(estimateTextWidthPx('', 13)).toBe(0);
    expect(estimateTextWidthPx('abcd', 13)).toBeGreaterThan(estimateTextWidthPx('ab', 13));
    expect(estimateTextWidthPx('abcd', 14)).toBeGreaterThan(estimateTextWidthPx('abcd', 13));
    expect(estimateTextWidthPx('abcd', 13, true)).toBeGreaterThan(estimateTextWidthPx('abcd', 13));
  });
});

describe('wrapTextToWidth', () => {
  it('returns the input untouched when it fits', () => {
    expect(wrapTextToWidth('short', 500, 13)).toBe('short');
  });

  it('wraps a long title into lines that each fit', () => {
    const out = wrapTextToWidth(TITLE, 420, 13, true);
    expect(Array.isArray(out)).toBe(true);
    const lines = out as string[];
    expect(lines.length).toBeGreaterThan(1);
    for (const l of lines) expect(estimateTextWidthPx(l, 13, true)).toBeLessThanOrEqual(420);
    // Nothing lost or reordered.
    expect(lines.join(' ')).toBe(TITLE);
  });

  it('does not split mid-word; an oversize single word stays whole', () => {
    const word = 'Supercalifragilisticexpialidocious';
    expect(wrapTextToWidth(word, 40, 13)).toBe(word);
    const out = wrapTextToWidth(`a ${word} b`, 40, 13) as string[];
    expect(out).toEqual(['a', word, 'b']);
  });

  it('passes non-strings and non-positive widths through', () => {
    expect(wrapTextToWidth(['a', 'b'], 10, 13)).toEqual(['a', 'b']);
    expect(wrapTextToWidth(TITLE, 0, 13)).toBe(TITLE);
  });
});

describe('wrapSpecTitles', () => {
  it('wraps a string title', () => {
    const spec: any = { title: TITLE };
    expect(wrapSpecTitles(spec, 420)).toBe(true);
    expect(Array.isArray(spec.title)).toBe(true);
  });

  it('wraps text and subtitle of an object title (the reported chart)', () => {
    const spec: any = { title: { text: TITLE, subtitle: SUBTITLE } };
    expect(wrapSpecTitles(spec, 420)).toBe(true);
    expect(Array.isArray(spec.title.text)).toBe(true);
    expect(Array.isArray(spec.title.subtitle)).toBe(true);
  });

  it('is a no-op when the title fits the pane', () => {
    const spec: any = { title: { text: TITLE, subtitle: SUBTITLE } };
    expect(wrapSpecTitles(spec, 1200)).toBe(false);
    expect(spec.title.text).toBe(TITLE);
    expect(spec.title.subtitle).toBe(SUBTITLE);
  });

  it('respects an authored array (own line breaks) and an authored limit', () => {
    const arr: any = { title: { text: ['line one', 'line two'] } };
    expect(wrapSpecTitles(arr, 10)).toBe(false);
    const lim: any = { title: { text: TITLE, limit: 300 } };
    expect(wrapSpecTitles(lim, 100)).toBe(false);
    expect(lim.title.text).toBe(TITLE);
  });

  it('uses an authored fontSize for the fit', () => {
    // At 13px this fits 300px; at 26px it should not.
    const text = 'twenty-eight character title';
    const small: any = { title: { text } };
    const big: any = { title: { text, fontSize: 26 } };
    expect(wrapSpecTitles(small, 300)).toBe(false);
    expect(wrapSpecTitles(big, 300)).toBe(true);
  });
});

describe('legendLabelRoomPx', () => {
  it('gives a bottom legend a per-column share and a side legend a pane fraction', () => {
    expect(legendLabelRoomPx(1000, 'bottom', 3)).toBe(Math.floor(960 / 3) - 30);
    expect(legendLabelRoomPx(1000, 'right')).toBe(350);
    expect(legendLabelRoomPx(1000, 'top', 1)).toBeGreaterThan(legendLabelRoomPx(1000, 'right'));
  });

  it('falls back when the width is unknown and never drops below the floor', () => {
    expect(legendLabelRoomPx(0)).toBe(LEGEND_LABEL_LIMIT_UNKNOWN_WIDTH_PX);
    expect(legendLabelRoomPx(-5)).toBe(LEGEND_LABEL_LIMIT_UNKNOWN_WIDTH_PX);
    expect(legendLabelRoomPx(100, 'bottom', 6)).toBe(LEGEND_LABEL_LIMIT_MIN_PX);
  });
});

describe('computeLegendLabelLimitPx (the ellipsised legend)', () => {
  it('exceeds Vega\'s 160px default for the reported labels in a wide pane', () => {
    const limit = computeLegendLabelLimitPx({
      labels: LEGEND_LABELS, containerWidthPx: 1184, orient: 'bottom', columns: 3,
    });
    expect(limit).toBeGreaterThan(VEGA_DEFAULT_LEGEND_LABEL_LIMIT);
    // and is at least as wide as the longest label
    const longest = Math.max(...LEGEND_LABELS.map((l) => estimateTextWidthPx(l, 10)));
    expect(limit).toBeGreaterThanOrEqual(longest);
  });

  it('is capped by the room the legend actually has', () => {
    const limit = computeLegendLabelLimitPx({
      labels: LEGEND_LABELS, containerWidthPx: 600, orient: 'bottom', columns: 3,
    });
    expect(limit).toBe(legendLabelRoomPx(600, 'bottom', 3));
  });

  it('honours the D-313 disambiguation floor but not past the room', () => {
    const labels = ['ab', 'ac'];
    expect(computeLegendLabelLimitPx({ labels, containerWidthPx: 1000, floorPx: 200 })).toBe(200);
    expect(computeLegendLabelLimitPx({ labels, containerWidthPx: 1000, orient: 'bottom', columns: 12, floorPx: 999 }))
      .toBe(legendLabelRoomPx(1000, 'bottom', 12));
  });

  it('never returns 0 (Vega\'s "no truncation" sentinel)', () => {
    expect(computeLegendLabelLimitPx({ labels: [], containerWidthPx: 0 })).toBeGreaterThanOrEqual(1);
  });

  it('leaves short labels at or below the default so callers can no-op', () => {
    const limit = computeLegendLabelLimitPx({ labels: ['a', 'bb', 'ccc'], containerWidthPx: 1000 });
    expect(limit).toBeLessThanOrEqual(VEGA_DEFAULT_LEGEND_LABEL_LIMIT);
  });
});
