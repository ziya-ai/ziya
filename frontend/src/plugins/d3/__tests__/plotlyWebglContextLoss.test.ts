/**
 * @jest-environment jsdom
 *
 * WebGL context lifecycle in plotlyPlugin.
 *
 * Crash log 2026-09-07/08: bursts of 4-5 `gl-shader: Error compiling shader:
 * null` thrown from plotly's draw loop within ~2ms, four times, the last of
 * which took the browser display down. `null` is getShaderInfoLog on a LOST
 * context. Two defects compounded:
 *
 *   1. Plotly plots were never purged — not on re-render (the plugin just did
 *      container.innerHTML = '') and not on unmount (render() returns void and
 *      D3Renderer's plugin path registered no cleanup) — so every WebGL-backed
 *      chart kept its GL context alive until GC, and Chromium's per-page
 *      context ceiling (~16, oldest is lost) was reachable in a long chat.
 *   2. Nothing listened for `webglcontextlost`, so plotly kept drawing on the
 *      dead context and threw once per frame, unbounded.
 *
 * Each test states what the pre-fix code did so the direction is pinned.
 */

import {
  plotlyPlugin,
  teardownPlotlyContainer,
  PLOTLY_MAX_CONTEXT_LOSS_RECOVERIES,
} from '../plotlyPlugin';
import { demoteWebglTracesForCapture } from '../plotlyPreprocessor';

let widthSpy: any;
let heightSpy: any;
let disconnectSpy: jest.Mock;

/**
 * Plotly mock. `newPlot` appends a <canvas> to the plot div when the spec has
 * a WebGL trace, mirroring what real plotly does for *gl / 3D families, so
 * the context-loss listener has something to attach to.
 */
function installPlotlyMock(overrides: Record<string, any> = {}) {
  const mock: any = {
    newPlot: jest.fn().mockImplementation(async (div: HTMLElement, data: any[]) => {
      if (data.some(t => /gl$|3d$|^surface$|^mesh3d$/.test(t.type))) {
        div.appendChild(document.createElement('canvas'));
      }
    }),
    purge: jest.fn(),
    relayout: jest.fn().mockResolvedValue(undefined),
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
  disconnectSpy = jest.fn();
  (global as any).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() { disconnectSpy(); }
  };
  widthSpy = jest.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1280);
  heightSpy = jest.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(600);
});

afterAll(() => {
  widthSpy.mockRestore();
  heightSpy.mockRestore();
});

beforeEach(() => {
  disconnectSpy.mockClear();
});

const glSpec = {
  type: 'plotly',
  data: [{ type: 'scattergl', x: [1, 2, 3], y: [4, 5, 6], mode: 'markers' }],
  layout: { title: 'gl' },
};

const svgSpec = {
  type: 'plotly',
  data: [{ type: 'bar', x: ['a', 'b'], y: [1, 2] }],
};

const flushMicrotasks = async (n = 12) => {
  for (let i = 0; i < n; i++) await Promise.resolve();
};

describe('purge on teardown', () => {
  it('re-rendering onto the same container purges the previous plot div', async () => {
    const mock = installPlotlyMock();
    const container = document.createElement('div');
    document.body.appendChild(container);

    await plotlyPlugin.render(container, null, glSpec, false);
    const firstDiv = (container as any)._plotlyDiv;
    expect(firstDiv).toBeTruthy();
    expect(mock.purge).not.toHaveBeenCalled();

    await plotlyPlugin.render(container, null, glSpec, false);
    // Pre-fix: container.innerHTML = '' dropped the div without purge, so
    // its WebGL context lived on until GC.
    expect(mock.purge).toHaveBeenCalledTimes(1);
    expect(mock.purge).toHaveBeenCalledWith(firstDiv);
    expect(disconnectSpy).toHaveBeenCalledTimes(1);
    // The handle now points at the NEW div, not the purged one.
    expect((container as any)._plotlyDiv).not.toBe(firstDiv);

    document.body.removeChild(container);
  });

  it('exposes __vizCleanup on the container, and calling it purges + disconnects', async () => {
    const mock = installPlotlyMock();
    const container = document.createElement('div');
    document.body.appendChild(container);

    await plotlyPlugin.render(container, null, glSpec, false);
    const cleanup = (container as any).__vizCleanup;
    // Pre-fix: no hook existed; D3Renderer had nothing to call on unmount.
    expect(typeof cleanup).toBe('function');

    const div = (container as any)._plotlyDiv;
    cleanup();
    expect(mock.purge).toHaveBeenCalledWith(div);
    expect(disconnectSpy).toHaveBeenCalledTimes(1);
    expect((container as any)._plotlyDiv).toBeUndefined();
    expect((container as any)._plotlyResizeObserver).toBeUndefined();

    // Idempotent: a second call must not purge again (D3Renderer may run the
    // cleanup list on re-render AND on unmount).
    cleanup();
    expect(mock.purge).toHaveBeenCalledTimes(1);

    document.body.removeChild(container);
  });

  it('teardownPlotlyContainer is a no-op on a container that never held a plot', () => {
    const mock = installPlotlyMock();
    const container = document.createElement('div');
    expect(() => teardownPlotlyContainer(container, mock)).not.toThrow();
    expect(mock.purge).not.toHaveBeenCalled();
  });
});

describe('webglcontextlost recovery', () => {
  it('purges the dead plot and re-renders once with *gl traces demoted to SVG', async () => {
    const mock = installPlotlyMock();
    const container = document.createElement('div');
    document.body.appendChild(container);

    await plotlyPlugin.render(container, null, glSpec, false);
    expect(mock.newPlot).toHaveBeenCalledTimes(1);
    expect(mock.newPlot.mock.calls[0][1][0].type).toBe('scattergl');
    const deadDiv = (container as any)._plotlyDiv;
    const canvas = deadDiv.querySelector('canvas') as HTMLCanvasElement;
    expect(canvas).toBeTruthy();

    const events: any[] = [];
    container.addEventListener('plotly-context-lost', (e: any) => events.push(e.detail));

    canvas.dispatchEvent(new Event('webglcontextlost'));
    await flushMicrotasks();

    // Pre-fix: nothing listened; plotly kept drawing on the lost context and
    // threw `gl-shader: ... null` every frame.
    expect(mock.purge).toHaveBeenCalledWith(deadDiv);
    expect(mock.newPlot).toHaveBeenCalledTimes(2);
    // The recovery render must not allocate a new GL context.
    expect(mock.newPlot.mock.calls[1][1][0].type).toBe('scatter');
    // Data survives the demotion.
    expect(mock.newPlot.mock.calls[1][1][0].x).toEqual([1, 2, 3]);
    expect(events).toEqual([{ recovered: true }]);
    // The recovered SVG plot has no canvas -> nothing left to lose.
    expect(container.querySelector('canvas')).toBeNull();

    document.body.removeChild(container);
  });

  it('gives up with a static notice after the recovery budget (3D has no SVG fallback)', async () => {
    const mock = installPlotlyMock();
    const container = document.createElement('div');
    document.body.appendChild(container);

    const spec3d = {
      type: 'plotly',
      data: [{ type: 'scatter3d', x: [1], y: [2], z: [3] }],
    };
    await plotlyPlugin.render(container, null, spec3d, false);

    for (let i = 0; i < PLOTLY_MAX_CONTEXT_LOSS_RECOVERIES; i++) {
      const canvas = container.querySelector('canvas') as HTMLCanvasElement;
      expect(canvas).toBeTruthy();
      canvas.dispatchEvent(new Event('webglcontextlost'));
      await flushMicrotasks();
      // Retried as-is: scatter3d has no `gl` suffix to strip.
      expect(mock.newPlot).toHaveBeenCalledTimes(2 + i);
      expect(mock.newPlot.mock.calls[1 + i][1][0].type).toBe('scatter3d');
    }

    const events: any[] = [];
    container.addEventListener('plotly-context-lost', (e: any) => events.push(e.detail));
    const lastCanvas = container.querySelector('canvas') as HTMLCanvasElement;
    const lastDiv = (container as any)._plotlyDiv;
    lastCanvas.dispatchEvent(new Event('webglcontextlost'));
    await flushMicrotasks();

    expect(mock.purge).toHaveBeenLastCalledWith(lastDiv);
    // No further newPlot: an unbounded retry would re-allocate a GL context
    // each time and re-trip the ceiling that lost it.
    expect(mock.newPlot).toHaveBeenCalledTimes(1 + PLOTLY_MAX_CONTEXT_LOSS_RECOVERIES);
    expect(container.querySelector('.plotly-context-lost')).not.toBeNull();
    expect(container.querySelector('canvas')).toBeNull();
    expect(events).toEqual([{ recovered: false }]);

    document.body.removeChild(container);
  });

  it('installs no listener for SVG-only plots (no canvas to lose)', async () => {
    const mock = installPlotlyMock();
    const container = document.createElement('div');
    document.body.appendChild(container);

    await plotlyPlugin.render(container, null, svgSpec, false);
    expect(container.querySelector('canvas')).toBeNull();
    expect(mock.newPlot).toHaveBeenCalledTimes(1);

    document.body.removeChild(container);
  });
});

describe('demoteWebglTracesForCapture(force)', () => {
  it('force=true demotes without navigator.webdriver', () => {
    expect((navigator as any).webdriver).not.toBe(true);
    const out = demoteWebglTracesForCapture(
      [{ type: 'scattergl' }, { type: 'heatmapgl' }, { type: 'bar' }],
      true,
    );
    expect(out.map(t => t.type)).toEqual(['scatter', 'heatmap', 'bar']);
  });

  it('default still leaves *gl alone in the interactive UI', () => {
    const out = demoteWebglTracesForCapture([{ type: 'scattergl' }]);
    expect(out[0].type).toBe('scattergl');
  });
});
