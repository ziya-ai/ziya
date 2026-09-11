/**
 * @jest-environment jsdom
 *
 * G-cf9c1c — plotlyPlugin render-path fixes.
 *
 *   D-300  domain-trace geometry stale after container resize. The recovery
 *          path used Plotly.Plots.resize(), which recomputes only the cartesian
 *          plot area and leaves pie/sunburst/scatterpolar/ternary domains pinned
 *          to the stale newPlot-time width. The fix forces a FULL relayout at
 *          the measured container geometry so every trace family re-solves.
 *          (D-214's residual failing spec plotly-w1-14 is a scatterpolar and
 *          shares this root cause.)
 *   D-302  a non-settling trace-family combination (splom-in-grid,
 *          carpet/contourcarpet) hung Plotly.newPlot until the harness wall
 *          clock elapsed and produced a blank capture. The fix bounds newPlot
 *          with a race that throws a fast NAMED diagnostic.
 *
 * Each test asserts the DIRECTION: the pre-fix behaviour (Plots.resize only /
 * an unbounded newPlot await) is what these assertions would fail against.
 */

import { plotlyPlugin, PLOTLY_NEWPLOT_BUDGET_MS } from '../plotlyPlugin';

// jsdom has no layout engine, so clientWidth/clientHeight are 0. Force a
// realistic capture geometry so the relayout branch (w>0 && h>0) is exercised.
const CW = 1280;
const CH = 600;
let widthSpy: any;
let heightSpy: any;

function installPlotlyMock(overrides: Record<string, any> = {}) {
  const mock: any = {
    newPlot: jest.fn().mockResolvedValue(undefined),
    relayout: jest.fn().mockResolvedValue(undefined),
    react: jest.fn().mockResolvedValue(undefined),
    Plots: { resize: jest.fn().mockResolvedValue(undefined) },
    toImage: jest.fn().mockResolvedValue('data:image/png;base64,'),
    ...overrides,
  };
  (window as any).Plotly = mock;
  (window as any).__plotlyLoaded = true;
  (window as any).__plotlyLoading = undefined;
  return mock;
}

beforeAll(() => {
  // Minimal ResizeObserver stub (jsdom lacks one); render() constructs one.
  (global as any).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

// CRA's jest preset sets `resetMocks: true`, which resets every mock —
// including spies — before EACH test. Spies created in beforeAll were
// therefore wiped by the time the first test ran, clientWidth/clientHeight
// read 0, and safeResize() took the Plots.resize branch instead of the
// relayout branch under test. Install them per test.
beforeEach(() => {
  widthSpy = jest
    .spyOn(HTMLElement.prototype, 'clientWidth', 'get')
    .mockReturnValue(CW);
  heightSpy = jest
    .spyOn(HTMLElement.prototype, 'clientHeight', 'get')
    .mockReturnValue(CH);
});

afterEach(() => {
  widthSpy.mockRestore();
  heightSpy.mockRestore();
});

const polarSpec = {
  type: 'plotly',
  data: [{ type: 'scatterpolar', r: [1, 2, 3, 4, 5], theta: [0, 72, 144, 216, 288] }],
  layout: { title: 'radial' },
};

describe('D-300: deferred resize forces a full relayout at measured geometry', () => {
  // A domain-trace figure (scatterpolar) must re-solve its subplot domain at
  // the final width. relayout({width,height}) re-centres only paper-referenced
  // items (title/annotations) and leaves the auto-fitted polar domain pinned to
  // its stale newPlot-time square — the exact residual failure of the earlier
  // relayout-only attempt. The fix routes domain figures through Plotly.react
  // at the measured geometry so supplyDefaults+calc recompute the domain.
  it('re-solves a DOMAIN trace with Plotly.react at measured geometry (not relayout/resize)', async () => {
    jest.useFakeTimers();
    const mock = installPlotlyMock();
    const container = document.createElement('div');
    document.body.appendChild(container);

    await plotlyPlugin.render(container, null, polarSpec, false);
    // Fire the requestAnimationFrame + setTimeout(200) deferred callbacks.
    jest.runOnlyPendingTimers();
    await Promise.resolve();

    expect(mock.react).toHaveBeenCalled();
    const lastArgs = mock.react.mock.calls[mock.react.mock.calls.length - 1];
    expect(lastArgs[0]).toBe((container as any)._plotlyDiv);
    // react(div, data, layout, config): the layout carries the measured size so
    // the polar domain re-fits to the true plot area.
    expect(lastArgs[2]).toMatchObject({ width: CW, height: CH, autosize: false });
    // Direction: neither the relayout-only nor the Plots.resize-only path
    // (both of which left the domain stale) is used for a domain figure.
    expect(mock.relayout).not.toHaveBeenCalled();
    expect(mock.Plots.resize).not.toHaveBeenCalled();

    jest.useRealTimers();
    document.body.removeChild(container);
  });

  // A cartesian-only figure keeps the cheaper relayout path unchanged — the
  // fix is scoped to domain traces, so the ~40 passing cartesian specs are not
  // perturbed.
  it('keeps the relayout path for a CARTESIAN figure (no react)', async () => {
    jest.useFakeTimers();
    const mock = installPlotlyMock();
    const container = document.createElement('div');
    document.body.appendChild(container);

    const cartesianSpec = {
      type: 'plotly',
      data: [{ type: 'bar', x: ['a', 'b', 'c'], y: [1, 2, 3] }],
      layout: { title: 'bars' },
    };
    await plotlyPlugin.render(container, null, cartesianSpec, false);
    jest.runOnlyPendingTimers();
    await Promise.resolve();

    expect(mock.relayout).toHaveBeenCalled();
    const lastArgs = mock.relayout.mock.calls[mock.relayout.mock.calls.length - 1];
    expect(lastArgs[1]).toMatchObject({ width: CW, height: CH, autosize: false });
    expect(mock.react).not.toHaveBeenCalled();

    jest.useRealTimers();
    document.body.removeChild(container);
  });

  it('re-solves the polar domain in BOTH light and dark while keeping the theme (D-214)', async () => {
    for (const isDark of [false, true]) {
      jest.useFakeTimers();
      const mock = installPlotlyMock();
      const container = document.createElement('div');
      document.body.appendChild(container);

      await plotlyPlugin.render(container, null, polarSpec, isDark);
      jest.runOnlyPendingTimers();
      await Promise.resolve();

      // Domain re-solve fired via react at the real geometry in this theme.
      expect(mock.react).toHaveBeenCalled();
      // The themed layout handed to newPlot is preserved through the react
      // re-solve (react receives the same themed layout). Dark explicitly
      // backgrounds the polar subplot (#1e1e1e, so #e0e0e0 radial ticks read at
      // 12.63:1); light keeps the theme paper surface (#ffffff, ticks #333333
      // = 12.63:1) and the polar bg defaults to that surface.
      const layoutArg = mock.newPlot.mock.calls[0][2];
      const reactLayout = mock.react.mock.calls[mock.react.mock.calls.length - 1][2];
      if (isDark) {
        expect(layoutArg.polar.bgcolor).toBe('#1e1e1e');
        expect(reactLayout.polar.bgcolor).toBe('#1e1e1e');
      } else {
        expect(layoutArg.paper_bgcolor).toBe('#ffffff');
        expect(reactLayout.paper_bgcolor).toBe('#ffffff');
      }

      jest.useRealTimers();
      document.body.removeChild(container);
    }
  });
});

describe('D-302: newPlot is bounded so a hung trace-family combo throws a diagnostic', () => {
  it('rejects with a named error instead of hanging when newPlot never settles', async () => {
    jest.useFakeTimers();
    // newPlot never resolves — the pre-fix code would await it forever.
    installPlotlyMock({ newPlot: jest.fn().mockReturnValue(new Promise(() => {})) });
    const container = document.createElement('div');
    document.body.appendChild(container);

    const splomInGrid = {
      type: 'plotly',
      data: [
        { type: 'splom', dimensions: [{ values: [1, 2, 3] }] },
        { type: 'contour', z: [[1, 2], [3, 4]] },
      ],
      layout: { grid: { rows: 1, columns: 3 } },
    };

    const p = plotlyPlugin.render(container, null, splomInGrid, false);
    p.catch(() => { /* asserted below; swallow to avoid unhandled rejection */ });
    // Flush the microtask chain so render() gets past `await loadPlotly()` and
    // constructs the Promise.race (which schedules the budget's setTimeout)...
    for (let i = 0; i < 8; i++) await Promise.resolve();
    // ...then trip the budget timer so the race rejects with the diagnostic.
    jest.advanceTimersByTime(PLOTLY_NEWPLOT_BUDGET_MS + 1);
    await expect(p).rejects.toThrow(/did not settle/i);

    jest.useRealTimers();
    document.body.removeChild(container);
  });
});
