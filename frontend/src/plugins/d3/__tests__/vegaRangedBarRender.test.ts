/**
 * End-to-end seam test for applyRangedBarFixes: compile the spec with the real
 * Vega-Lite compiler and render it headlessly with Vega, then count the bars
 * that are actually visible. This is what the structural assertions in
 * vegaRangedBar.test.ts cannot show: that the rewritten spec produces a chart
 * with datapoints on it.
 *
 * The "before" assertion certifies the bug: without the fix every bar is
 * degenerate (zero width from the floored 30-s intervals, and/or default
 * sliver height), so the visible count is 0.
 */

import { applyRangedBarFixes } from '../vegaLitePlugin';

// vega / vega-lite publish ESM entry points that jest's node runtime cannot
// load untransformed; the UMD bundles work. The vega-lite UMD does
// `require('vega')` under CommonJS, so point that at vega's UMD too.
/* eslint-disable @typescript-eslint/no-var-requires */
// jsdom lacks structuredClone, which vega-lite calls at module load.
if (typeof (globalThis as any).structuredClone !== 'function') {
  (globalThis as any).structuredClone = (v: any) => JSON.parse(JSON.stringify(v));
}
jest.mock('vega', () => require('vega/build/vega.js'));
const vega = require('vega');
const vl = require('vega-lite/build/vega-lite.min.js');

const episodes = [
  ['2026-10-17T06:23:00Z', '2026-10-17T06:23:30Z', 50, 'blip'],
  ['2026-10-17T07:38:00Z', '2026-10-17T07:39:30Z', 100, 'sat'],
  ['2026-10-17T08:27:00Z', '2026-10-17T08:29:30Z', 97, 'stick'],
  ['2026-10-17T11:29:00Z', '2026-10-17T11:31:00Z', 100, 'hole'],
  ['2026-10-17T12:32:00Z', '2026-10-17T12:33:30Z', 100, 'hole'],
].map(([s, e, peak, c]) => ({ s, e, peak, c }));

const spec = (): any => ({
  $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
  width: 800,
  height: 220,
  data: { values: episodes },
  layer: [
    {
      mark: { type: 'bar', cornerRadius: 2 },
      encoding: {
        x: { field: 's', type: 'temporal', timeUnit: 'utchoursminutes' },
        x2: { field: 'e', type: 'temporal' },
        y: { field: 'peak', type: 'quantitative' },
        color: { field: 'c', type: 'nominal' },
      },
    },
    {
      mark: { type: 'text', dy: -8 },
      encoding: {
        x: { field: 's', type: 'temporal', timeUnit: 'utchoursminutes' },
        y: { field: 'peak', type: 'quantitative' },
        text: { field: 'c' },
      },
    },
  ],
});

// Render headlessly and return the bounding boxes of the bar-mark rects.
async function visibleBars(vlSpec: any): Promise<{ w: number; h: number }[]> {
  const { spec: vgSpec } = vl.compile(vlSpec);
  const view = new vega.View(vega.parse(vgSpec), { renderer: 'none' });
  await view.runAsync();
  const out: { w: number; h: number }[] = [];
  // Scenegraph: find every rect item under a mark of role "mark".
  const walk = (node: any) => {
    if (!node) return;
    if (node.marktype === 'rect' && Array.isArray(node.items)) {
      for (const it of node.items) {
        const w = Math.abs((it.x2 ?? it.x + (it.width ?? 0)) - it.x);
        const h = Math.abs((it.y2 ?? it.y + (it.height ?? 0)) - it.y);
        out.push({ w, h });
      }
    }
    if (Array.isArray(node.items)) node.items.forEach(walk);
  };
  walk(view.scenegraph().root);
  return out.filter(b => b.w >= 1 && b.h >= 1);
}

describe('applyRangedBarFixes end-to-end through the Vega-Lite compiler', () => {
  it('renders no visible bars WITHOUT the fix (certifies the defect)', async () => {
    const bars = await visibleBars(spec());
    expect(bars).toHaveLength(0);
  });

  it('renders one visible bar per episode WITH the fix', async () => {
    const s = spec();
    expect(applyRangedBarFixes(s)).toBeGreaterThan(0);
    const bars = await visibleBars(s);
    expect(bars).toHaveLength(episodes.length);
    // Heights span the y range (peak 50..100 of a 220-px plot), not slivers.
    expect(Math.min(...bars.map(b => b.h))).toBeGreaterThan(50);
  });

  it('keeps the x domain within the data day (not 2012..2026)', async () => {
    const s = spec();
    applyRangedBarFixes(s);
    const { spec: vgSpec } = vl.compile(s);
    const view = new vega.View(vega.parse(vgSpec), { renderer: 'none' });
    await view.runAsync();
    const [lo, hi] = view.scale('x').domain() as Date[];
    const span = new Date(hi).getTime() - new Date(lo).getTime();
    expect(span).toBeLessThan(24 * 3600 * 1000);
    expect(new Date(lo).getUTCFullYear()).toBe(2026);
  });
});
