/**
 * G-835d47 / D-302 — a `splom` trace whose `xaxes` / `yaxes` arrays are shorter
 * than its `dimensions` array hangs Plotly.newPlot synchronously (blank 30s
 * capture, svg:0/canvas:0). `sanitizeSplomAxes` repairs the malformed input
 * BEFORE newPlot is reached by dropping the mismatched axis arrays so Plotly
 * auto-generates a consistent N×N axis set.
 *
 * These tests assert the DIRECTION of the fix: without sanitizeSplomAxes the
 * malformed length-1 xaxes/yaxes on a 2-dimension splom would survive into the
 * spec handed to newPlot. Theme-independent (structural), so no per-theme
 * assertion is required for this defect.
 */
import { sanitizeSplomAxes, preprocessPlotlySpec } from '../plotlyPreprocessor';

describe('D-302 sanitizeSplomAxes — mismatched splom axis arrays', () => {
  it('drops xaxes/yaxes when their length does not match dimension count (plotly-w3-06 shape)', () => {
    const data = [
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
    ];
    const out = sanitizeSplomAxes(data);
    // The malformed, hang-triggering arrays must be gone so Plotly regenerates.
    expect(out[0].xaxes).toBeUndefined();
    expect(out[0].yaxes).toBeUndefined();
    // Non-axis fields are preserved untouched.
    expect(out[0].dimensions).toHaveLength(2);
    expect(out[0].marker).toEqual({ size: 6 });
  });

  it('leaves a well-formed splom (axis arrays length == dimensions) UNCHANGED by reference', () => {
    const data = [
      {
        type: 'splom',
        dimensions: [
          { label: 'a', values: [1, 2] },
          { label: 'b', values: [3, 4] },
        ],
        xaxes: ['x', 'x2'],
        yaxes: ['y', 'y2'],
      },
    ];
    const out = sanitizeSplomAxes(data);
    expect(out).toBe(data); // no-op returns input by reference
    expect(out[0].xaxes).toEqual(['x', 'x2']);
  });

  it('leaves a splom with NO axis arrays untouched (Plotly already auto-generates)', () => {
    const data = [
      { type: 'splom', dimensions: [{ label: 'a', values: [1] }, { label: 'b', values: [2] }] },
    ];
    const out = sanitizeSplomAxes(data);
    expect(out).toBe(data);
  });

  it('never touches non-splom traces', () => {
    const data = [{ type: 'scatter', x: [1], y: [2], xaxes: ['x9'] }];
    const out = sanitizeSplomAxes(data);
    expect(out).toBe(data);
    expect(out[0].xaxes).toEqual(['x9']);
  });

  it('the full preprocessor pass repairs the malformed splom end-to-end', () => {
    const spec = {
      data: [
        {
          type: 'splom',
          dimensions: [
            { label: 'd1', values: [1, 3, 2, 5] },
            { label: 'd2', values: [2, 1, 4, 3] },
          ],
          xaxes: ['x3'],
          yaxes: ['y3'],
        },
      ],
      layout: { grid: { rows: 1, columns: 3, pattern: 'independent' } },
    };
    const out = preprocessPlotlySpec(spec as any);
    const splom = (out.data || []).find((t: any) => t.type === 'splom');
    expect(splom).toBeDefined();
    expect(splom.xaxes).toBeUndefined();
    expect(splom.yaxes).toBeUndefined();
  });
});
