/**
 * Regression tests for Issue 48 — plotly render hard-hangs to the 300s MCP cap
 * with zero output when a spec carries an astronomical per-trace font.size
 * (e.g. table cells.font.size:1e4) or marker.size (e.g. splom marker.size:1e6).
 *
 * The existing font clamp (sanitizeLayoutGeometry) only covered `layout.font`,
 * so per-trace / nested fonts leaked through unclamped and starved the render
 * thread. `clampExtremeSizes` generalizes the clamp to EVERY font.size in the
 * whole spec plus astronomical marker.size (guarded by `sizeref`).
 *
 * These tests import the REAL module — pre-fix `clampExtremeSizes` does not
 * exist, so the import itself fails against pre-fix code (non-vacuous). They
 * pin BOTH directions: extreme magnitudes are clamped, AND well-formed specs /
 * sizeref-governed markers are left byte-identical (reference-stable no-op).
 */
import {
  clampExtremeSizes,
  preprocessPlotlySpec,
  PLOTLY_MAX_MARKER_SIZE,
  PLOTLY_MAX_FONT_SIZE,
  PLOTLY_MIN_FONT_SIZE,
} from '../plotlyPreprocessor';

describe('clampExtremeSizes — per-trace / nested font.size clamping (Issue 48)', () => {
  it('clamps an astronomical table cells.font.size (1e4 -> max)', () => {
    const spec = {
      data: [{ type: 'table', cells: { values: [['a']], font: { size: 1e4 } } }],
    };
    const out = clampExtremeSizes(spec);
    expect(out.data![0].cells.font.size).toBe(PLOTLY_MAX_FONT_SIZE);
  });

  it('coerces a negative table header.font.size (-10 -> sane default)', () => {
    const spec = {
      data: [{ type: 'table', header: { values: ['A'], font: { size: -10 } } }],
    };
    const out = clampExtremeSizes(spec);
    expect(out.data![0].header.font.size).toBe(12);
  });

  it('clamps a deeply-nested colorbar title font.size', () => {
    const spec = {
      data: [
        {
          type: 'heatmap',
          z: [[1, 2]],
          marker: { colorbar: { title: { font: { size: 99999 } } } },
        },
      ],
    };
    const out = clampExtremeSizes(spec);
    expect(out.data![0].marker.colorbar.title.font.size).toBe(PLOTLY_MAX_FONT_SIZE);
  });

  it('clamps layout.font.size and axis tickfont.size (any *font key, any depth)', () => {
    const spec = {
      data: [{ type: 'scatter', x: [1], y: [1] }],
      layout: {
        font: { size: 1e4 },
        xaxis: { tickfont: { size: 5000 }, titlefont: { size: 0 } },
      },
    };
    const out = clampExtremeSizes(spec);
    expect(out.layout.font.size).toBe(PLOTLY_MAX_FONT_SIZE);
    expect(out.layout.xaxis.tickfont.size).toBe(PLOTLY_MAX_FONT_SIZE);
    expect(out.layout.xaxis.titlefont.size).toBe(12); // 0 -> default
  });

  it('leaves a legible font.size untouched, incl. the exact max boundary', () => {
    const spec = {
      data: [{ type: 'table', cells: { values: [['a']], font: { size: 14 } } }],
      layout: { font: { size: PLOTLY_MAX_FONT_SIZE } },
    };
    const out = clampExtremeSizes(spec);
    expect(out.data![0].cells.font.size).toBe(14);
    expect(out.layout.font.size).toBe(PLOTLY_MAX_FONT_SIZE);
  });

  it('clamps a font.size below the minimum up to the floor', () => {
    const spec = { layout: { font: { size: 0.1 } } };
    const out = clampExtremeSizes(spec);
    expect(out.layout.font.size).toBe(PLOTLY_MIN_FONT_SIZE);
  });
});

describe('clampExtremeSizes — marker.size clamping (Issue 48)', () => {
  it('clamps a scalar splom marker.size (1e6 -> max) when no sizeref', () => {
    const spec = { data: [{ type: 'splom', dimensions: [], marker: { size: 1e6 } }] };
    const out = clampExtremeSizes(spec);
    expect(out.data![0].marker.size).toBe(PLOTLY_MAX_MARKER_SIZE);
  });

  it('clamps astronomical array elements and coerces negatives to 0', () => {
    const spec = {
      data: [{ type: 'scatter', x: [1], y: [1], marker: { size: [10, 10, 1e6, -20, 0] } }],
    };
    const out = clampExtremeSizes(spec);
    expect(out.data![0].marker.size).toEqual([10, 10, PLOTLY_MAX_MARKER_SIZE, 0, 0]);
  });

  it('coerces a non-finite scalar marker.size to 0', () => {
    const spec = { data: [{ type: 'scatter', x: [1], y: [1], marker: { size: Infinity } }] };
    const out = clampExtremeSizes(spec);
    expect(out.data![0].marker.size).toBe(0);
  });

  it('GUARD: leaves marker.size UNTOUCHED when sizeref is present (bubble data)', () => {
    const spec = {
      data: [{ type: 'scatter', x: [1], y: [1], marker: { size: 1e6, sizeref: 2000 } }],
    };
    const out = clampExtremeSizes(spec);
    // sizeref means marker.size holds raw data values plotly scales -> keep.
    expect(out.data![0].marker.size).toBe(1e6);
    expect(out).toBe(spec); // reference-stable no-op
  });

  it('GUARD: leaves an in-range marker.size and non-numeric array elements alone', () => {
    const spec = {
      data: [{ type: 'scatter', x: [1], y: [1], marker: { size: [20, null, 'x', 40] } }],
    };
    const out = clampExtremeSizes(spec);
    expect(out.data![0].marker.size).toEqual([20, null, 'x', 40]);
  });
});

describe('clampExtremeSizes — no-op / idempotency guards', () => {
  it('returns a fully well-formed spec by REFERENCE (no collateral mutation)', () => {
    const spec = {
      data: [
        { type: 'scatter', x: [1, 2], y: [3, 4], marker: { size: 12 } },
        { type: 'table', cells: { values: [['a']], font: { size: 14 } } },
      ],
      layout: { font: { size: 16 }, title: 'ok' },
    };
    const out = clampExtremeSizes(spec);
    expect(out).toBe(spec);
  });

  it('is idempotent — clamping a clamped spec changes nothing further', () => {
    const spec = {
      data: [{ type: 'splom', marker: { size: 1e6 } }],
      layout: { font: { size: 1e4 } },
    };
    const once = clampExtremeSizes(spec);
    const twice = clampExtremeSizes(once);
    expect(twice).toEqual(once);
    expect(twice).toBe(once); // second pass is a reference-stable no-op
  });

  it('tolerates null / non-object / missing data', () => {
    expect(clampExtremeSizes(null as any)).toBeNull();
    expect(clampExtremeSizes('x' as any)).toBe('x');
    expect(clampExtremeSizes({} as any)).toEqual({});
  });
});

describe('preprocessPlotlySpec integration — Issue 48 adversarial fields neutralized', () => {
  it('clamps table cell font + splom marker end-to-end via the composed pipeline', () => {
    const spec = {
      data: [
        { type: 'splom', dimensions: [{ label: 'd', values: [1, 2] }], marker: { size: 1e6 } },
        {
          type: 'table',
          header: { values: ['A'], font: { size: -10 } },
          cells: { values: [['a']], font: { size: 1e4 } },
        },
      ],
      layout: { font: { size: 1e4 } },
    };
    const out = preprocessPlotlySpec(spec);
    expect(out.data[0].marker.size).toBe(PLOTLY_MAX_MARKER_SIZE);
    expect(out.data[1].header.font.size).toBe(12);
    expect(out.data[1].cells.font.size).toBe(PLOTLY_MAX_FONT_SIZE);
    expect(out.layout.font.size).toBe(PLOTLY_MAX_FONT_SIZE);
  });
});
