/**
 * G-2ca2a0 — force-directed SVG must shrink-to-container (responsive sizing).
 *
 * Regression lock for the shared root cause behind D-107 / D-108 / D-110 /
 * D-368 (and the crop half of D-081/D-109): the plugin sized its <svg> at the
 * DECLARED / normalised canvas in fixed pixels (e.g. 900x700, 1000x800,
 * 1400x1000) with no responsive scaling. The headless capture viewport is much
 * smaller (~632x464), so the container's overflow:hidden CROPPED the right and
 * bottom off — recorded as "fixed-pixel-svg-exceeds-render-viewport-cropped" /
 * "no-fit-to-extent-nodes-clipped-offscreen". The fit-to-extent transform fits
 * the nodes into the DECLARED CANVAS, not the viewport, so it could never
 * prevent this crop.
 *
 * The fix mirrors every other d3 plugin (graphviz / mermaid / joint / d2): keep
 * viewBox = [0,0,width,height] but add preserveAspectRatio 'xMidYMid meet' and
 * max-width:100% / height:auto so the WHOLE logical canvas scales down to fit
 * whatever viewport captures it.
 *
 * Direction: the unpatched render set NEITHER preserveAspectRatio NOR the
 * responsive max-width/height styles on the <svg>, so every assertion below is
 * RED before the fix and GREEN after. Purely geometric (no colour changes), so
 * it is asserted in BOTH themes to prove it is not theme-coupled.
 */
import { forceDirectedPlugin } from '../forceDirectedPlugin';

// Mock d3 that records BOTH .attr() and .style() calls without a real DOM.
// (The shipped render() only sets preserveAspectRatio / max-width / height:auto
// on the root <svg>, so these names are unambiguous.)
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

const lastAttr = (record: any, name: string): any => {
  const hits = record.attrs.filter((a: [string, any]) => a[0] === name);
  return hits.length ? hits[hits.length - 1][1] : undefined;
};
const lastStyle = (record: any, name: string): any => {
  const hits = record.styles.filter((a: [string, any]) => a[0] === name);
  return hits.length ? hits[hits.length - 1][1] : undefined;
};

// A canvas deliberately larger than the ~632x464 capture window — exactly the
// class that was cropped 1:1 (force-directed-w2-02 = 1000x800, w2-15 = 1400x1000,
// d3-w2-11 = 760x560, etc.).
const oversizedCanvasSpec = {
  type: 'force-directed',
  width: 1000,
  height: 800,
  nodes: [
    { id: 'a' }, { id: 'b' }, { id: 'c' }, { id: 'd' },
  ],
  links: [
    { source: 'a', target: 'b' },
    { source: 'b', target: 'c' },
    { source: 'c', target: 'd' },
  ],
};

describe.each([
  ['light', false],
  ['dark', true],
] as Array<[string, boolean]>)('G-2ca2a0 responsive svg — %s theme', (_label, isDark) => {
  it('adds preserveAspectRatio "xMidYMid meet" so the viewBox scales to fit (was absent pre-fix)', () => {
    const rec = runRender(oversizedCanvasSpec, isDark);
    expect(lastAttr(rec, 'preserveAspectRatio')).toBe('xMidYMid meet');
  });

  it('sizes the svg responsively: max-width 100% + height auto (was fixed px pre-fix)', () => {
    const rec = runRender(oversizedCanvasSpec, isDark);
    expect(lastStyle(rec, 'max-width')).toBe('100%');
    expect(lastStyle(rec, 'height')).toBe('auto');
  });

  it('still carries a viewBox covering the full declared canvas so nothing is left outside it', () => {
    const rec = runRender(oversizedCanvasSpec, isDark);
    // The <svg> sets viewBox as an array [0,0,w,h]; the arrow-marker also sets a
    // (string) viewBox, so search for the svg's array form specifically.
    const svgViewBox = rec.attrs
      .filter((a: [string, any]) => a[0] === 'viewBox' && Array.isArray(a[1]))
      .map((a: [string, any]) => a[1])
      .pop();
    expect(svgViewBox).toBeDefined();
    expect(svgViewBox[0]).toBe(0);
    expect(svgViewBox[1]).toBe(0);
    expect(svgViewBox[2]).toBeGreaterThanOrEqual(632); // wider than the capture viewport
    expect(svgViewBox[3]).toBeGreaterThanOrEqual(464); // taller than the capture viewport
  });
});
