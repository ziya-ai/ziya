/**
 * @jest-environment jsdom
 */
/**
 * D-302 — the two plotly.js SYNCHRONOUS Plotly.newPlot hangs that survived the
 * earlier attempts (a newPlot Promise.race, which a sync hang ignores; and
 * sanitizeSplomAxes, which removed the mismatched axes but left the
 * splom+layout.grid conflict). The blackboard isolation renders pinned the real
 * triggers:
 *   - plotly-w3-06: splom coexisting with an explicit layout.grid (splom alone
 *                   renders) — the grid/subplot allocator hangs.
 *   - plotly-w3-07: contourcarpet over a carpet — the contour path tracer loops.
 *
 * `neutralizeCaptureHangCombos` runs BEFORE newPlot (so, unlike a timer, it can
 * actually remove the offending input) and only under the headless capture gate
 * (navigator.webdriver), mirroring demoteWebglTracesForCapture. These tests
 * assert the DIRECTION of the fix: WITHOUT the pass the hang-triggering shape
 * reaches newPlot unchanged; WITH it (force=true here, since jsdom sets no
 * webdriver flag) the offending ingredient is removed while the rest survives.
 * Structural / theme-independent, so no per-theme assertion is required.
 */
import {
  neutralizeCaptureHangCombos,
  preprocessPlotlySpec,
} from '../plotlyPreprocessor';

// The real plotly-w3-06 shape (contour + histogram2d + splom in a 1x3 grid),
// trimmed to the structure that matters for the hang.
const w306 = () => ({
  data: [
    { type: 'contour', z: [[1, 2], [3, 4]], xaxis: 'x', yaxis: 'y' },
    { type: 'histogram2d', x: [1, 2, 3], y: [1, 2, 3], xaxis: 'x2', yaxis: 'y2' },
    {
      type: 'splom',
      dimensions: [
        { label: 'd1', values: [1, 3, 2, 5] },
        { label: 'd2', values: [2, 1, 4, 3] },
      ],
      xaxes: ['x3'],
      yaxes: ['y3'],
      marker: { size: 6 },
    },
  ],
  layout: { grid: { rows: 1, columns: 3, pattern: 'independent' }, xaxis3: { domain: [0.76, 1] } },
});

// The real plotly-w3-07 shape (scatterternary + carpet + contourcarpet).
const w307 = () => ({
  data: [
    { type: 'scatterternary', a: [0.6, 0.2], b: [0.2, 0.6], c: [0.2, 0.2], mode: 'markers' },
    { type: 'carpet', carpet: 'c1', a: [0, 1, 2, 0, 1, 2], b: [1, 1, 1, 2, 2, 2], y: [1, 2, 3, 1.5, 2.6, 3.4], xaxis: 'x2', yaxis: 'y2' },
    { type: 'contourcarpet', carpet: 'c1', a: [0, 1, 2, 0, 1, 2], b: [1, 1, 1, 2, 2, 2], z: [1, 2, 3, 4, 5, 6], xaxis: 'x2', yaxis: 'y2' },
  ],
  layout: { ternary: { domain: { x: [0, 0.45] } }, xaxis2: { domain: [0.58, 1] } },
});

describe('D-302 neutralizeCaptureHangCombos — splom + layout.grid (w3-06)', () => {
  it('drops layout.grid when a splom coexists with it (the surviving hang ingredient)', () => {
    const out = neutralizeCaptureHangCombos(w306() as any, true);
    expect(out.layout.grid).toBeUndefined();
    // The splom (and the other traces) are preserved — only the grid is removed.
    expect((out.data || []).some((t: any) => t.type === 'splom')).toBe(true);
    expect(out.data).toHaveLength(3);
    // Other layout keys are untouched.
    expect(out.layout.xaxis3).toEqual({ domain: [0.76, 1] });
  });

  it('leaves a splom that has NO layout.grid unchanged by reference', () => {
    const spec = { data: [{ type: 'splom', dimensions: [{ label: 'a', values: [1] }] }], layout: {} };
    const out = neutralizeCaptureHangCombos(spec as any, true);
    expect(out).toBe(spec);
  });
});

describe('D-302 neutralizeCaptureHangCombos — carpet + contourcarpet (w3-07)', () => {
  it('drops the carpet-family traces so the ternary still renders', () => {
    const out = neutralizeCaptureHangCombos(w307() as any, true);
    const types = (out.data || []).map((t: any) => t.type);
    expect(types).toEqual(['scatterternary']);
    expect(types).not.toContain('carpet');
    expect(types).not.toContain('contourcarpet');
  });

  it('leaves a lone carpet (no contourcarpet) untouched by reference', () => {
    const spec = { data: [{ type: 'carpet', carpet: 'c1', a: [0], b: [1], y: [1] }], layout: {} };
    const out = neutralizeCaptureHangCombos(spec as any, true);
    expect(out).toBe(spec);
  });
});

describe('D-302 neutralizeCaptureHangCombos — capture gating', () => {
  it('is a NO-OP off the capture path (no webdriver, force omitted)', () => {
    // jsdom leaves navigator.webdriver falsy, so without force this must not fire.
    const spec = w306();
    const out = neutralizeCaptureHangCombos(spec as any);
    expect(out).toBe(spec);
    expect(out.layout.grid).toBeDefined();

    const spec2 = w307();
    const out2 = neutralizeCaptureHangCombos(spec2 as any);
    expect(out2).toBe(spec2);
    expect((out2.data || []).some((t: any) => t.type === 'contourcarpet')).toBe(true);
  });

  it('fires under a simulated headless-capture navigator.webdriver flag', () => {
    const nav = navigator as any;
    const had = Object.prototype.hasOwnProperty.call(nav, 'webdriver');
    const prev = nav.webdriver;
    try {
      Object.defineProperty(nav, 'webdriver', { value: true, configurable: true });
      const out = neutralizeCaptureHangCombos(w307() as any); // no force — gate must pass
      expect((out.data || []).map((t: any) => t.type)).toEqual(['scatterternary']);
    } finally {
      if (had) Object.defineProperty(nav, 'webdriver', { value: prev, configurable: true });
      else delete nav.webdriver;
    }
  });
});

describe('D-302 full preprocessor wiring', () => {
  it('preprocessPlotlySpec removes both hang triggers under a simulated capture', () => {
    const nav = navigator as any;
    const had = Object.prototype.hasOwnProperty.call(nav, 'webdriver');
    const prev = nav.webdriver;
    try {
      Object.defineProperty(nav, 'webdriver', { value: true, configurable: true });
      const g = preprocessPlotlySpec(w306() as any);
      expect(g.layout.grid).toBeUndefined();
      const c = preprocessPlotlySpec(w307() as any);
      expect((c.data || []).map((t: any) => t.type)).toEqual(['scatterternary']);
    } finally {
      if (had) Object.defineProperty(nav, 'webdriver', { value: prev, configurable: true });
      else delete nav.webdriver;
    }
  });
});
