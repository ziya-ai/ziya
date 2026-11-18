/**
 * Chart titles wrapped far narrower than the chart (reported 2026-11-12).
 *
 * vegaLitePlugin wrapped every string title to `fitWidthPx - 40`, where
 * fitWidthPx is the live pane width, or the 400px floor of a detached
 * container when the pane was not measured (render tool, PDF print page).
 * A vconcat of 640px views assembles to ~1000px, so its title broke at
 * 400px -- reproduced: "... by serving / satellite and gateway site" with
 * the log line "TITLE-FIT: wrapped title to 400px" in an 1100px window.
 *
 * titleWrapWidthPx takes the wider of a MEASURED pane and the chart's own
 * width, and returns null (do not wrap) when neither is known.
 *
 * The title font was Vega's default `sans-serif`, a different face from the
 * UI and the PDF body.  applyDefaultChartFont sets config.font to the UI
 * stack unless the author chose one, and the wrap measures in that font.
 *
 * DIRECTION: the unit blocks fail before the fix (functions absent); the
 * seam block fails while the plugin still wraps at `fitWidthPx - 40`.
 */
import * as fs from 'fs';
import * as path from 'path';
import {
  applyDefaultChartFont, titleWrapWidthPx, wrapSpecTitles,
  TITLE_AXIS_ROOM_PX, VEGA_UI_FONT,
} from '../vegaTextFit';
import { estimateChartWidthPx } from '../vegaLegendPlacement';

const TITLE = 'Customer packet loss per sampled hour, all builds, by serving satellite and gateway site';

const composite = () => ({
  title: { text: TITLE },
  width: 400,
  vconcat: [
    { width: 640, mark: 'line', encoding: {} },
    { width: 640, mark: 'line', encoding: {} },
  ],
});

describe('titleWrapWidthPx', () => {
  it('a measured pane alone: the pane less its gutter', () => {
    expect(titleWrapWidthPx(900, null)).toBe(860);
  });

  it('chart width alone: the chart plus its axis room', () => {
    expect(titleWrapWidthPx(null, 640)).toBe(640 + TITLE_AXIS_ROOM_PX);
  });

  it('both: the wider of the two', () => {
    expect(titleWrapWidthPx(500, 640)).toBe(640 + TITLE_AXIS_ROOM_PX);
    expect(titleWrapWidthPx(1400, 640)).toBe(1360);
  });

  it('neither known (or zero): null, so the title is left alone', () => {
    expect(titleWrapWidthPx(null, null)).toBeNull();
    expect(titleWrapWidthPx(0, null)).toBeNull();
    expect(titleWrapWidthPx(undefined, undefined)).toBeNull();
  });
});

describe('the reported composite title', () => {
  it('wrapped at the 400px floor (positive control)', () => {
    const spec: any = composite();
    expect(wrapSpecTitles(spec, 400)).toBe(true);
    expect(Array.isArray(spec.title.text)).toBe(true);
  });

  it('fits the width the chart actually has', () => {
    const spec: any = composite();
    const w = titleWrapWidthPx(null, estimateChartWidthPx(spec, 400));
    expect(w).toBe(640 + TITLE_AXIS_ROOM_PX);
    expect(wrapSpecTitles(spec, w!)).toBe(false);
    expect(spec.title.text).toBe(TITLE);
  });
});

describe('applyDefaultChartFont', () => {
  it('sets the UI font stack when no font was authored, keeping other config', () => {
    const spec: any = { config: { legend: { orient: 'bottom' } } };
    expect(applyDefaultChartFont(spec)).toBe(true);
    expect(spec.config.font).toBe(VEGA_UI_FONT);
    expect(spec.config.legend).toEqual({ orient: 'bottom' });
  });

  it('creates config when absent', () => {
    const spec: any = {};
    expect(applyDefaultChartFont(spec)).toBe(true);
    expect(spec.config).toEqual({ font: VEGA_UI_FONT });
  });

  it('leaves an authored font alone', () => {
    const spec: any = { config: { font: 'Georgia' } };
    expect(applyDefaultChartFont(spec)).toBe(false);
    expect(spec.config.font).toBe('Georgia');
  });

  it('the stack ends in a generic family', () => {
    expect(VEGA_UI_FONT.trim().endsWith('sans-serif')).toBe(true);
  });
});

describe('seam: vegaLitePlugin', () => {
  const src = fs.readFileSync(path.resolve(__dirname, '../vegaLitePlugin.ts'), 'utf8');

  it('no longer wraps at the pane width alone', () => {
    expect(src).not.toMatch(/wrapSpecTitles\(vegaSpec, fitWidthPx - 40\)/);
  });

  it('wraps at titleWrapWidthPx of the measured pane and the chart width', () => {
    expect(src).toMatch(/titleWrapWidthPx\(\s*paneWidthKnown,\s*estimateChartWidthPx\(vegaSpec, _sizing\.width\)/);
    expect(src).toMatch(/wrapSpecTitles\(vegaSpec, titleWidthPx\)/);
  });

  it('sets the default font before the wrap measures', () => {
    const font = src.indexOf('applyDefaultChartFont(vegaSpec)');
    const wrap = src.indexOf('wrapSpecTitles(vegaSpec, titleWidthPx)');
    expect(font).toBeGreaterThan(-1);
    expect(wrap).toBeGreaterThan(font);
  });
});
