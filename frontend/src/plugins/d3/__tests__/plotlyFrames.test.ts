/**
 * @jest-environment jsdom
 *
 * Plotly animation frames must reach the plot.
 *
 * A plotly figure carries animation as a top-level `frames` array, driven by
 * layout.updatemenus (Play) and layout.sliders whose steps call
 * `method: "animate"` with frame NAMES. The plugin parsed `frames` (the
 * preprocessor rebuilds the spec as {...spec, data, layout}, so the key
 * survived) but then called the four-argument
 * `Plotly.newPlot(div, data, layout, config)`, which has no slot for frames.
 * Result: the Play button and slider rendered, looked functional, and did
 * nothing -- `animate` looked up frame names in an empty frame store.
 *
 * Fix: after newPlot settles, hand the frames to `Plotly.addFrames`. The
 * resize path uses the four-arg `Plotly.react` (no frames argument), which
 * leaves an existing frame store untouched, so registering them once after
 * newPlot is sufficient. The context-loss re-render goes back through
 * `render()` with the same spec, so frames are re-registered there for free.
 *
 * Each test states what the pre-fix code did so the direction is pinned.
 */

import { plotlyPlugin } from '../plotlyPlugin';

let widthSpy: any;
let heightSpy: any;

function installPlotlyMock(overrides: Record<string, any> = {}) {
  const mock: any = {
    newPlot: jest.fn().mockResolvedValue(undefined),
    addFrames: jest.fn().mockResolvedValue(undefined),
    react: jest.fn().mockResolvedValue(undefined),
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
  (global as any).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  widthSpy = jest.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1280);
  heightSpy = jest.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(600);
});

afterAll(() => {
  widthSpy.mockRestore();
  heightSpy.mockRestore();
});

const frames = [
  { name: '0', data: [{ y: [0, 1, 0] }] },
  { name: '1', data: [{ y: [1, 0, 1] }] },
];

const animatedSpec = {
  type: 'plotly',
  data: [{ type: 'scatter', x: [0, 1, 2], y: [0, 1, 0] }],
  frames,
  layout: {
    sliders: [{
      steps: [
        { label: '0', method: 'animate', args: [['0'], { mode: 'immediate' }] },
        { label: '1', method: 'animate', args: [['1'], { mode: 'immediate' }] },
      ],
    }],
  },
};

const staticSpec = {
  type: 'plotly',
  data: [{ type: 'bar', x: ['a', 'b'], y: [1, 2] }],
};

describe('plotly frames reach the plot', () => {
  it('registers spec.frames with Plotly.addFrames after newPlot', async () => {
    const mock = installPlotlyMock();
    const container = document.createElement('div');
    document.body.appendChild(container);

    await plotlyPlugin.render(container, null, animatedSpec, false);

    expect(mock.newPlot).toHaveBeenCalledTimes(1);
    // Pre-fix: never called; the slider's animate() found no frames.
    expect(mock.addFrames).toHaveBeenCalledTimes(1);
    const [div, passed] = mock.addFrames.mock.calls[0];
    expect(div).toBe((container as any)._plotlyDiv);
    expect(passed).toHaveLength(2);
    expect(passed.map((f: any) => f.name)).toEqual(['0', '1']);
    // Frame order: newPlot must have settled before addFrames (addFrames on
    // a div with no plot throws in real plotly).
    expect(mock.newPlot.mock.invocationCallOrder[0])
      .toBeLessThan(mock.addFrames.mock.invocationCallOrder[0]);

    document.body.removeChild(container);
  });

  it('does not call addFrames for a spec without frames', async () => {
    const mock = installPlotlyMock();
    const container = document.createElement('div');
    document.body.appendChild(container);

    await plotlyPlugin.render(container, null, staticSpec, false);

    expect(mock.newPlot).toHaveBeenCalledTimes(1);
    expect(mock.addFrames).not.toHaveBeenCalled();

    document.body.removeChild(container);
  });

  it('does not call addFrames for an empty frames array', async () => {
    const mock = installPlotlyMock();
    const container = document.createElement('div');
    document.body.appendChild(container);

    await plotlyPlugin.render(container, null, { ...staticSpec, frames: [] }, false);

    expect(mock.addFrames).not.toHaveBeenCalled();

    document.body.removeChild(container);
  });

  it('frames are not smuggled into layout or data by the preprocessor', async () => {
    const mock = installPlotlyMock();
    const container = document.createElement('div');
    document.body.appendChild(container);

    await plotlyPlugin.render(container, null, animatedSpec, false);

    const [, data, layout] = mock.newPlot.mock.calls[0];
    expect(layout.frames).toBeUndefined();
    expect(data.some((t: any) => 'frames' in t)).toBe(false);
    // The slider that drives the frames is still in layout.
    expect(layout.sliders).toHaveLength(1);

    document.body.removeChild(container);
  });

  it('an addFrames rejection does not fail the render (chart still shows)', async () => {
    const mock = installPlotlyMock({
      addFrames: jest.fn().mockRejectedValue(new Error('bad frame')),
    });
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    const container = document.createElement('div');
    document.body.appendChild(container);

    // A malformed frame must degrade to "static chart, no animation", not
    // to an error panel replacing a chart that newPlot already drew.
    await expect(plotlyPlugin.render(container, null, animatedSpec, false)).resolves.toBeUndefined();
    expect(mock.newPlot).toHaveBeenCalledTimes(1);
    expect((container as any)._plotlyDiv).toBeTruthy();
    expect(warn).toHaveBeenCalled();

    warn.mockRestore();
    document.body.removeChild(container);
  });
});
