/**
 * Regression tests for Issue 26 (plotly):
 *  (1) sanitizeLayoutGeometry — degenerate layout width/height/font.size that
 *      stalls the headless capture must be coerced to sane values.
 *  (2) disambiguateHierarchyLabels — duplicate labels in treemap/sunburst/icicle
 *      traces (without explicit ids) must not be dropped by the d3 "ambiguous"
 *      stratify error; a unique-id hierarchy is synthesized instead.
 *
 * Imports the REAL shipped module (not a local re-implementation) so the test
 * detects drift in production logic.
 */
import {
  sanitizeLayoutGeometry,
  disambiguateHierarchyLabels,
  preprocessPlotlySpec,
  PLOTLY_MAX_DIMENSION,
  PLOTLY_MAX_FONT_SIZE,
} from '../plotlyPreprocessor';

describe('sanitizeLayoutGeometry (Issue 26 — capture-stalling degenerate geometry)', () => {
  it('drops zero width and negative height so Plotly autosizes', () => {
    const out = sanitizeLayoutGeometry({ width: 0, height: -100 });
    expect('width' in out).toBe(false);
    expect('height' in out).toBe(false);
  });

  it('clamps an absurd font.size (1e6) down to the bounded max', () => {
    const out = sanitizeLayoutGeometry({ font: { size: 1e6, family: 'x' } });
    expect(out.font.size).toBe(PLOTLY_MAX_FONT_SIZE);
    expect(out.font.family).toBe('x'); // other font fields preserved
  });

  it('coerces non-finite font.size to a default', () => {
    expect(sanitizeLayoutGeometry({ font: { size: Infinity } }).font.size).toBe(12);
    expect(sanitizeLayoutGeometry({ font: { size: NaN } }).font.size).toBe(12);
    expect(sanitizeLayoutGeometry({ font: { size: -5 } }).font.size).toBe(12);
  });

  it('clamps an astronomically huge width to the bounded max', () => {
    expect(sanitizeLayoutGeometry({ width: 1e12 }).width).toBe(PLOTLY_MAX_DIMENSION);
  });

  it('GUARD: leaves a well-formed layout UNCHANGED (same reference)', () => {
    const good = { width: 800, height: 600, font: { size: 14 } };
    expect(sanitizeLayoutGeometry(good)).toBe(good);
  });

  it('GUARD: a layout with no width/height/font passes through unchanged', () => {
    const good = { title: { text: 'hi' } };
    expect(sanitizeLayoutGeometry(good)).toBe(good);
  });
});

describe('disambiguateHierarchyLabels (Issue 26 — duplicate-root data loss)', () => {
  const dupRootTreemap = {
    type: 'treemap',
    name: 'degenerate-treemap',
    labels: ['root', 'root', 'child-neg', 'child-zero'],
    parents: ['', '', 'root', 'root'],
    values: [100, 100, -30, 0],
  };

  it('synthesizes unique ids and rewrites parents when labels duplicate', () => {
    const [out] = disambiguateHierarchyLabels([dupRootTreemap]);
    // ids must be unique
    expect(new Set(out.ids).size).toBe(out.ids.length);
    expect(out.ids.length).toBe(4);
    // The two roots keep parent "" (top level)
    expect(out.parents[0]).toBe('');
    expect(out.parents[1]).toBe('');
    // Children that referenced "root" now point at the FIRST root's id
    expect(out.parents[2]).toBe(out.ids[0]);
    expect(out.parents[3]).toBe(out.ids[0]);
    // no child was accidentally rooted to the SECOND duplicate
    expect(out.parents[2]).not.toBe(out.ids[1]);
  });

  it('GUARD: a hierarchy trace with UNIQUE labels is returned unchanged', () => {
    const unique = {
      type: 'treemap',
      labels: ['root', 'a', 'b'],
      parents: ['', 'root', 'root'],
    };
    expect(disambiguateHierarchyLabels([unique])[0]).toBe(unique);
  });

  it('GUARD: a trace with author-supplied ids is left untouched', () => {
    const withIds = {
      type: 'sunburst',
      ids: ['x1', 'x2'],
      labels: ['dup', 'dup'],
      parents: ['', 'x1'],
    };
    expect(disambiguateHierarchyLabels([withIds])[0]).toBe(withIds);
  });

  it('GUARD: non-hierarchy traces (bar) pass through unchanged even with dup labels', () => {
    const bar = { type: 'bar', labels: ['dup', 'dup'], parents: ['', ''] };
    expect(disambiguateHierarchyLabels([bar])[0]).toBe(bar);
  });

  it('applies disambiguation to sunburst and icicle as well', () => {
    for (const type of ['sunburst', 'icicle']) {
      const [out] = disambiguateHierarchyLabels([
        { type, labels: ['r', 'r', 'c'], parents: ['', '', 'r'] },
      ]);
      expect(new Set(out.ids).size).toBe(3);
      expect(out.parents[2]).toBe(out.ids[0]);
    }
  });

  it('preserves a parent label never declared as a node (orphan left as-is)', () => {
    const [out] = disambiguateHierarchyLabels([
      { type: 'treemap', labels: ['root', 'root', 'x'], parents: ['', '', 'ghost'] },
    ]);
    expect(out.parents[2]).toBe('ghost');
  });
});

describe('preprocessPlotlySpec end-to-end (Issue 26 adversarial layout + treemap)', () => {
  it('sanitizes degenerate layout AND disambiguates the dup-root treemap together', () => {
    const spec = {
      type: 'plotly',
      data: [
        {
          type: 'treemap',
          labels: ['root', 'root', 'child'],
          parents: ['', '', 'root'],
          values: [1, 1, 1],
        },
      ],
      layout: { width: 0, height: -100, font: { size: 1e6 } },
    };
    const out = preprocessPlotlySpec(spec);
    expect('width' in out.layout).toBe(false);
    expect('height' in out.layout).toBe(false);
    expect(out.layout.font.size).toBe(PLOTLY_MAX_FONT_SIZE);
    expect(new Set(out.data[0].ids).size).toBe(3);
    expect(out.data[0].parents[2]).toBe(out.data[0].ids[0]);
  });
});
