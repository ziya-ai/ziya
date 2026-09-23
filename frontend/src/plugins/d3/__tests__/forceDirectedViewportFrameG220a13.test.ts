/**
 * G-220a13 — force-directed / d3 SVG render frame must fit the capture viewport.
 *
 * Regression lock for the shared root cause behind D-107 / D-108 / D-368 /
 * D-392 (crop) and D-369 (sub-pixel node collapse). The plugin sized its <svg>
 * at the DECLARED / normalised canvas (up to FORCE_MAX_CANVAS_DIM = 2000px) and
 * leaned on CSS `max-width:100%/height:auto` to shrink-to-container. In the
 * headless harness the container is pinned to the declared width, so that CSS
 * shrink is a no-op and the oversized SVG overflows the ~1280x960 capture
 * viewport and is CROPPED on the right/bottom (D-107/D-108/D-368/D-392); and
 * when the SVG IS downscaled the shrink is invisible to the fit-to-extent scale
 * `fit.k`, so the on-screen legibility floors (which assume on-screen px ==
 * user px * fit.k) are wrong and nodes collapse to sub-pixel dots (D-369).
 *
 * The fix sizes the SVG to a RENDER FRAME that fits inside the actual capture
 * viewport (preserving the declared aspect) and fits the settled graph INTO
 * that frame, so the SVG displays 1:1 and `fit.k` is truthful.
 *
 * Direction: BEFORE the fix render() emits the <svg> at the declared canvas
 * (2000x1500), so `width`/`height`/`viewBox` exceed the viewport — every
 * "oversized" assertion below is RED. AFTER the fix they are framed to
 * <= viewport - margin — GREEN. A sub-viewport canvas (700x500) is unchanged in
 * both, guarding against a regression. Purely geometric, asserted in BOTH themes.
 */
import {
  forceDirectedPlugin,
  fitFrameToViewport,
  FORCE_FRAME_MARGIN,
} from '../forceDirectedPlugin';

// Mock d3 that records .attr()/.style() calls without a real DOM (mirrors the
// G-2ca2a0 harness). currentViewport() reads the global window, which jsdom
// provides; we pin it below for determinism.
function makeMockD3() {
  const record: any = { attrs: [] as Array<[string, any]>, styles: [] as Array<[string, any]> };
  const target: any = function () {};
  const proxy: any = new Proxy(target, {
    get(_t, prop) {
      if (prop === 'zoomIdentity') return proxy;
      const name = String(prop);
      return (...args: any[]) => {
        if (name === 'attr' && args.length >= 1) record.attrs.push([args[0], args[1]]);
        if (name === 'style' && args.length >= 1) record.styles.push([args[0], args[1]]);
        return proxy;
      };
    },
    apply() {
      return proxy;
    },
  });
  return { d3: proxy, record };
}

function runRender(spec: any, isDark = false) {
  const { d3, record } = makeMockD3();
  const cleanup = forceDirectedPlugin.render({} as any, d3, spec, isDark);
  if (typeof cleanup === 'function') cleanup();
  return record;
}

// The <svg> sets viewBox as an array [0,0,w,h]; the arrow-marker sets a string
// viewBox, so pick the array form specifically to identify the root svg.
const svgViewBox = (record: any): any =>
  record.attrs
    .filter((a: [string, any]) => a[0] === 'viewBox' && Array.isArray(a[1]))
    .map((a: [string, any]) => a[1])
    .pop();

// These suites run in the `node` test environment (no `window`), so
// currentViewport() falls back to the golden-tier capture default of 1280x960 —
// which is exactly the viewport we want to assert against, so no pinning needed.
const VIEWPORT_W = 1280;
const VIEWPORT_H = 960;

const makeSpec = (width: number, height: number) => ({
  type: 'force-directed',
  width,
  height,
  nodes: [{ id: 'a' }, { id: 'b' }, { id: 'c' }, { id: 'd' }, { id: 'e' }],
  links: [
    { source: 'a', target: 'b' },
    { source: 'b', target: 'c' },
    { source: 'c', target: 'd' },
    { source: 'd', target: 'e' },
    { source: 'e', target: 'a' },
  ],
});

describe('fitFrameToViewport (pure)', () => {
  it('caps an oversized canvas to <= viewport - margin, preserving aspect', () => {
    const frame = fitFrameToViewport(2000, 1500, VIEWPORT_W, VIEWPORT_H);
    expect(frame.width).toBeLessThanOrEqual(VIEWPORT_W - FORCE_FRAME_MARGIN);
    expect(frame.height).toBeLessThanOrEqual(VIEWPORT_H - FORCE_FRAME_MARGIN);
    // aspect preserved within rounding
    expect(frame.width / frame.height).toBeCloseTo(2000 / 1500, 1);
  });

  it('passes a sub-viewport canvas through unchanged (s === 1)', () => {
    expect(fitFrameToViewport(700, 500, VIEWPORT_W, VIEWPORT_H)).toEqual({ width: 700, height: 500 });
  });
});

describe.each([
  ['light', false],
  ['dark', true],
] as Array<[string, boolean]>)('G-220a13 viewport-fit render frame — %s theme', (_label, isDark) => {
  it('frames an oversized declared canvas (2000x1500) inside the capture viewport', () => {
    const rec = runRender(makeSpec(2000, 1500), isDark);
    const vb = svgViewBox(rec);
    expect(vb).toBeDefined();
    // RED before fix: viewBox was [0,0,2000,1500] (> viewport). GREEN after.
    expect(vb[2]).toBeLessThanOrEqual(VIEWPORT_W - FORCE_FRAME_MARGIN);
    expect(vb[3]).toBeLessThanOrEqual(VIEWPORT_H - FORCE_FRAME_MARGIN);
    // The intrinsic width/height attributes must match the framed viewBox so
    // the SVG displays 1:1 (no CSS downscale that would defeat the fit floors).
    const width = rec.attrs.filter((a: [string, any]) => a[0] === 'width').pop();
    const height = rec.attrs.filter((a: [string, any]) => a[0] === 'height').pop();
    expect(width && width[1]).toBe(vb[2]);
    expect(height && height[1]).toBe(vb[3]);
  });

  it('leaves a sub-viewport declared canvas (700x500) unchanged', () => {
    const rec = runRender(makeSpec(700, 500), isDark);
    const vb = svgViewBox(rec);
    expect(vb).toEqual([0, 0, 700, 500]);
  });
});
