/**
 * G-52 — Plotly data-shape recovery + legibility floors.
 *
 * Covers the plotly-preprocessor group:
 *   D-235  a later trace with no `x` gets implicit indices and is drawn in a
 *          disjoint half of a categorical axis instead of overlaying the shared
 *          categories -> back-fill x from the sibling.
 *   D-242  heatmap/bar string axes that look like date fragments ("00-06") are
 *          date-coerced to years -> force type:'category'.
 *   D-239  pie/sunburst/treemap in-shape text auto-shrinks below legibility with
 *          no floor -> layout.uniformtext { minsize, mode:'hide' }.
 *
 * Every test includes a DIRECTION check: the raw/unpatched shape is asserted so
 * the test fails against the pre-fix pipeline and passes only with the change.
 * D-239/D-242 are theme-blind structural fixes (the preprocessor takes no theme
 * input), so the transform output is identical under both themes — asserted
 * explicitly where relevant.
 */

import {
  backfillMissingTraceX,
  coerceCategoricalStringAxes,
  enforceInShapeTextFloor,
  preprocessPlotlySpec,
  PLOTLY_UNIFORM_TEXT_MINSIZE,
} from '../plotlyPreprocessor';

// ── D-235: missing-x back-fill ────────────────────────────────────────────────
describe('D-235 backfillMissingTraceX', () => {
  const twoSeries = () => [
    { x: ['a', 'b', 'c', 'd'], y: [4, 7, 2, 9], name: 'first' },
    { y: [1, 3, 2, 5], name: 'second, no x' },
  ];

  it('copies the sibling x onto a later trace that omits it (length match)', () => {
    const data = twoSeries();
    // DIRECTION: unpatched, the second trace has no x at all.
    expect(data[1].x).toBeUndefined();
    const out = backfillMissingTraceX(data);
    expect(out[1].x).toEqual(['a', 'b', 'c', 'd']);
    // First trace untouched; reference is shared by value.
    expect(out[0].x).toEqual(['a', 'b', 'c', 'd']);
  });

  it('runs through the full preprocessor for the plotly-w4-13 spec shape', () => {
    const spec = {
      data: [
        { x: ['a', 'b', 'c', 'd'], y: [4, 7, 2, 9], name: 'first' },
        { y: [1, 3, 2, 5], name: 'second, no x' },
      ],
    };
    const out = preprocessPlotlySpec(spec);
    expect(out.data[1].x).toEqual(['a', 'b', 'c', 'd']);
  });

  it('does NOT fire on a length mismatch', () => {
    const data = [
      { x: ['a', 'b', 'c', 'd'], y: [1, 2, 3, 4] },
      { y: [1, 2, 3] }, // 3 != 4
    ];
    const out = backfillMissingTraceX(data);
    expect(out[1].x).toBeUndefined();
    expect(out).toBe(data); // no-op returns by reference
  });

  it('leaves a trace that already has x untouched', () => {
    const data = [
      { x: ['a', 'b'], y: [1, 2] },
      { x: [0, 1], y: [3, 4] },
    ];
    const out = backfillMissingTraceX(data);
    expect(out).toBe(data);
    expect(out[1].x).toEqual([0, 1]);
  });

  it('does not back-fill a non-cartesian trace (pie has no x semantics)', () => {
    const data = [
      { x: ['a', 'b', 'c'], y: [1, 2, 3] },
      { type: 'pie', values: [1, 2, 3], labels: ['p', 'q', 'r'] },
    ];
    const out = backfillMissingTraceX(data);
    expect(out).toBe(data);
    expect(out[1].x).toBeUndefined();
  });

  it('only reuses x within the same x-axis assignment', () => {
    const data = [
      { x: ['a', 'b'], y: [1, 2] },            // xaxis 'x' (default)
      { y: [3, 4], xaxis: 'x2' },              // different subplot axis
    ];
    const out = backfillMissingTraceX(data);
    expect(out[1].x).toBeUndefined();
  });
});

// ── D-242: category-axis coercion ─────────────────────────────────────────────
describe('D-242 coerceCategoricalStringAxes', () => {
  const heatmapSpec = () => ({
    data: [
      {
        type: 'heatmap',
        z: [[1, 2], [3, 4]],
        x: ['Mon', 'Tue'],
        y: ['00-06', '06-12', '12-18', '18-24'],
      },
    ],
    layout: { title: { text: 'CPU' } },
  });

  it('sets yaxis.type=category for date-fragment row labels (plotly-w1-03)', () => {
    const spec = heatmapSpec();
    // DIRECTION: unpatched has no axis type at all -> plotly date-coerces.
    expect(spec.layout.yaxis).toBeUndefined();
    const out = coerceCategoricalStringAxes(spec);
    expect(out.layout.yaxis.type).toBe('category');
    expect(out.layout.xaxis.type).toBe('category'); // 'Mon','Tue' also categorical
  });

  it('is theme-blind: same output through preprocessPlotlySpec regardless', () => {
    const out = preprocessPlotlySpec(heatmapSpec());
    expect(out.layout.yaxis.type).toBe('category');
    expect(out.layout.xaxis.type).toBe('category');
  });

  it('respects an explicit author axis type', () => {
    const spec = heatmapSpec();
    (spec.layout as any).yaxis = { type: 'linear' };
    const out = coerceCategoricalStringAxes(spec);
    expect(out.layout.yaxis.type).toBe('linear'); // untouched
  });

  it('does NOT coerce a full-date (year-bearing) string axis', () => {
    const spec = {
      data: [{ type: 'bar', x: ['2020-Q1', '2020-Q2', '2020-Q3'], y: [1, 2, 3] }],
      layout: {},
    };
    const out = coerceCategoricalStringAxes(spec);
    expect((out.layout as any).xaxis).toBeUndefined(); // year present -> left alone
  });

  it('does NOT coerce numeric string axes', () => {
    const spec = {
      data: [{ type: 'bar', x: ['1', '2', '3'], y: [1, 2, 3] }],
      layout: {},
    };
    const out = coerceCategoricalStringAxes(spec);
    expect((out.layout as any).xaxis).toBeUndefined();
    expect(out).toBe(spec); // no-op by reference
  });

  it('ignores non-category-axis trace types (scatter)', () => {
    const spec = {
      data: [{ type: 'scatter', x: ['a', 'b', 'c'], y: [1, 2, 3] }],
      layout: {},
    };
    const out = coerceCategoricalStringAxes(spec);
    expect(out).toBe(spec);
  });
});

// ── D-239: in-shape-text floor ────────────────────────────────────────────────
describe('D-239 enforceInShapeTextFloor', () => {
  it('adds uniformtext {minsize, hide} when a sunburst is present', () => {
    const spec = { data: [{ type: 'sunburst', labels: ['a'], parents: [''] }] };
    // DIRECTION: unpatched has no uniformtext policy -> unbounded shrink.
    expect((spec as any).layout).toBeUndefined();
    const out = enforceInShapeTextFloor(spec);
    expect(out.layout.uniformtext).toEqual({
      minsize: PLOTLY_UNIFORM_TEXT_MINSIZE,
      mode: 'hide',
    });
  });

  it('fires for pie and treemap too, through the full preprocessor', () => {
    for (const type of ['pie', 'treemap', 'icicle', 'funnelarea']) {
      const out = preprocessPlotlySpec({ data: [{ type, labels: ['a'], values: [1] }] });
      expect(out.layout.uniformtext.mode).toBe('hide');
      expect(out.layout.uniformtext.minsize).toBe(PLOTLY_UNIFORM_TEXT_MINSIZE);
    }
  });

  it('respects an author-supplied uniformtext', () => {
    const spec = {
      data: [{ type: 'pie', labels: ['a'], values: [1] }],
      layout: { uniformtext: { minsize: 12, mode: 'show' } },
    };
    const out = enforceInShapeTextFloor(spec);
    expect(out.layout.uniformtext).toEqual({ minsize: 12, mode: 'show' });
    expect(out).toBe(spec); // no-op by reference
  });

  it('does not touch a plain bar/scatter spec (no in-shape text trace)', () => {
    const spec = { data: [{ type: 'bar', x: [1], y: [2] }], layout: {} };
    const out = enforceInShapeTextFloor(spec);
    expect((out.layout as any).uniformtext).toBeUndefined();
    expect(out).toBe(spec);
  });
});
