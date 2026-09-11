/**
 * A d3-style hierarchical spec -- `{type:"treemap", data:{name, children:[...]}}`
 * -- must render.  The model emits this shape because the prompt lists "d3" as
 * a renderer, but no plugin claimed `treemap`, so a first-run user saw:
 *
 *   No compatible plugin found for visualization type "treemap"
 *
 * Plotly renders treemap / sunburst / icicle natively from a flat
 * labels/parents/values table.  Rather than a new d3 renderer, the plotly
 * plugin accepts the nested shape and flattens it.  Sibling name collisions
 * across branches (src/unit vs tests/unit) are the classic hierarchy bug, so
 * identity is the full path, and the visible label is the bare name.
 *
 * Imports the REAL shipped module so the test detects drift.
 */
import {
  isD3HierarchySpec,
  hierarchySpecToPlotly,
} from '../plotlyPreprocessor';
import { plotlyPlugin } from '../plotlyPlugin';

const REPO = {
  type: 'treemap',
  data: {
    name: 'repo',
    children: [
      { name: 'src', children: [{ name: 'api', value: 220 }, { name: 'unit', value: 100 }] },
      { name: 'tests', children: [{ name: 'unit', value: 200 }, { name: 'integration', value: 120 }] },
      { name: 'docs', value: 80 },
    ],
  },
};

describe('isD3HierarchySpec', () => {
  it('accepts the nested treemap shape the model emits', () => {
    expect(isD3HierarchySpec(REPO)).toBe(true);
  });
  it.each(['sunburst', 'icicle'])('accepts %s with the same data shape', (type) => {
    expect(isD3HierarchySpec({ ...REPO, type })).toBe(true);
  });
  it('accepts root/tree as aliases for data', () => {
    expect(isD3HierarchySpec({ type: 'treemap', root: REPO.data })).toBe(true);
    expect(isD3HierarchySpec({ type: 'treemap', tree: REPO.data })).toBe(true);
  });
  it('declines an ordinary plotly spec and a bar chart', () => {
    expect(isD3HierarchySpec({ data: [{ type: 'treemap', labels: ['a'], parents: [''] }] })).toBe(false);
    expect(isD3HierarchySpec({ type: 'bar', data: [{ x: 1 }] })).toBe(false);
    expect(isD3HierarchySpec(null)).toBe(false);
    expect(isD3HierarchySpec('treemap')).toBe(false);
  });
});

describe('hierarchySpecToPlotly', () => {
  it('flattens to one trace with path ids, bare labels, and parent links', () => {
    const out = hierarchySpecToPlotly(REPO);
    expect(out.data).toHaveLength(1);
    const t = out.data[0];
    expect(t.type).toBe('treemap');
    // Two different "unit" nodes survive as distinct ids.
    expect(t.ids).toContain('repo/src/unit');
    expect(t.ids).toContain('repo/tests/unit');
    expect(new Set(t.ids).size).toBe(t.ids.length);
    const i = t.ids.indexOf('repo/tests/unit');
    expect(t.labels[i]).toBe('unit');
    expect(t.parents[i]).toBe('repo/tests');
    expect(t.values[i]).toBe(200);
    // Root has no parent.
    expect(t.parents[t.ids.indexOf('repo')]).toBe('');
  });

  it('honours size as an alias for value and leaves branch values unset', () => {
    const out = hierarchySpecToPlotly({ type: 'sunburst', data: { name: 'r', children: [{ name: 'a', size: 5 }] } });
    const t = out.data[0];
    expect(t.type).toBe('sunburst');
    expect(t.values[t.ids.indexOf('r/a')]).toBe(5);
    // A branch without its own value must not be coerced to 0 -- Plotly sums
    // its children under the default branchvalues.
    expect(t.values[t.ids.indexOf('r')]).toBeUndefined();
  });

  it('carries a title through to the layout', () => {
    const out = hierarchySpecToPlotly({ ...REPO, title: 'Repository size' });
    expect(out.layout?.title?.text ?? out.layout?.title).toBe('Repository size');
  });
});

describe('plotlyPlugin.canHandle (the seam)', () => {
  it('claims the d3-style treemap spec so the registry no longer reports "no compatible plugin"', () => {
    expect(plotlyPlugin.canHandle(REPO)).toBe(true);
  });
  it('still declines a spec no plugin should claim', () => {
    expect(plotlyPlugin.canHandle({ type: 'definitely-not-a-thing', data: [] })).toBe(false);
  });
});
