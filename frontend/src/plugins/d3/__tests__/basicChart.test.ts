/**
 * Tests for basicChartPlugin — canHandle and bubble chart rendering.
 */

import { basicChartPlugin } from '../basicChart';

// ── canHandle ────────────────────────────────────────────────────────────────

describe('basicChartPlugin.canHandle', () => {
  it.each(['bar', 'line', 'scatter', 'bubble'])('accepts %s type', (type) => {
    expect(basicChartPlugin.canHandle({ type, data: [] })).toBe(true);
  });

  it('rejects unknown types', () => {
    expect(basicChartPlugin.canHandle({ type: 'pie', data: [] })).toBe(false);
  });

  it('rejects non-object input', () => {
    expect(basicChartPlugin.canHandle('bar')).toBe(false);
    expect(basicChartPlugin.canHandle(null)).toBe(false);
    expect(basicChartPlugin.canHandle(undefined)).toBe(false);
  });
});

// ── Bubble chart ─────────────────────────────────────────────────────────────

/** Minimal D3 mock that tracks method calls without real DOM manipulation. */
function createMockD3() {
  const calls: Array<{ method: string; args: any[] }> = [];

  const chainable: any = new Proxy({}, {
    get(_, prop: string) {
      return (...args: any[]) => {
        calls.push({ method: prop, args });
        return chainable;
      };
    },
  });

  const d3: any = {
    select: () => chainable,
    scaleLinear: () => {
      const s: any = (v: number) => v * 10;
      s.domain = () => s;
      s.range = () => s;
      return s;
    },
    scaleSqrt: () => {
      const s: any = (v: number) => Math.sqrt(v) * 4;
      s.domain = () => s;
      s.range = () => s;
      return s;
    },
    scaleBand: () => {
      const s: any = () => 0;
      s.domain = () => s;
      s.range = () => s;
      s.padding = () => s;
      s.bandwidth = () => 10;
      return s;
    },
    extent: (data: any[], fn: (d: any) => number) => {
      const vals = data.map(fn).filter((v: any) => v != null);
      return [Math.min(...vals), Math.max(...vals)];
    },
    max: (data: any[], fn: (d: any) => number) => Math.max(...data.map(fn)),
    axisBottom: () => () => chainable,
    axisLeft: () => () => chainable,
  };

  return { d3, calls };
}

describe('bubble chart rendering', () => {
  const bubbleSpec = {
    type: 'bubble',
    data: [
      { x: 2, y: 20, size: 15, label: 'α (close)' },
      { x: 12, y: 85, size: 50, label: 'γ (target)' },
      { x: 25, y: 99, size: 100, label: 'θ (edge)' },
    ],
    width: 600,
    height: 400,
  };

  it('renders without throwing (the original bug produced NaN positions)', () => {
    const container = document.createElement('div');
    const { d3 } = createMockD3();
    expect(() => basicChartPlugin.render(container, d3, bubbleSpec, false)).not.toThrow();
  });

  it('does not use scaleBand (bubble needs continuous linear scales)', () => {
    const container = document.createElement('div');
    const { d3 } = createMockD3();
    const spy = jest.spyOn(d3, 'scaleBand');
    basicChartPlugin.render(container, d3, bubbleSpec, false);
    expect(spy).not.toHaveBeenCalled();
  });

  it('uses scaleLinear for both axes', () => {
    const container = document.createElement('div');
    const { d3 } = createMockD3();
    const spy = jest.spyOn(d3, 'scaleLinear');
    basicChartPlugin.render(container, d3, bubbleSpec, false);
    // x-axis + y-axis = at least 2 calls
    expect(spy).toHaveBeenCalledTimes(2);
  });
});
