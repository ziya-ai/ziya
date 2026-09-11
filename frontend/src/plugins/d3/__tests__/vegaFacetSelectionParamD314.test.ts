import {
  isFacetOperatorSpec,
  collectSelectionParamNames,
  neutralizeFacetedSelectionParams,
} from '../vegaLiteParamGuard';

// D-314 / G-14daa0: a point selection param bound to a legend inside a
// FACETED spec is instantiated per facet cell, and the per-cell selection
// signals + legend bindings compile to a runaway dataflow that renders time
// out in both themes. The guard must refuse the per-cell fan-out by dropping
// the selection param(s) and resolving their encoding conditions to the
// matched (selected-state) branch — WITHOUT touching non-faceted specs or
// non-selection variable params.

// The exact spec that timed out (vega-lite-w3-13): facet(columns:2) + layer +
// point selection bound to legend + tooltip + sort.
const facetedSelectionSpec = () => ({
  data: { values: [{ c: 'Alpha', v: 42, q: 'Q1' }, { c: 'Beta', v: 58, q: 'Q1' }] },
  params: [{ name: 'pick', select: { type: 'point', fields: ['c'] }, bind: 'legend' }],
  facet: { field: 'q', type: 'nominal', columns: 2 },
  spec: {
    width: 230,
    height: 150,
    layer: [
      {
        mark: { type: 'bar', tooltip: true },
        encoding: { opacity: { condition: { param: 'pick', value: 1 }, value: 0.3 } },
      },
      {
        mark: { type: 'rule', strokeDash: [4, 3] },
        encoding: { y: { aggregate: 'mean', field: 'v', type: 'quantitative' } },
      },
    ],
    encoding: {
      x: { field: 'c', type: 'nominal', sort: '-y' },
      y: { field: 'v', type: 'quantitative' },
      color: { field: 'c', type: 'nominal' },
    },
  },
});

describe('vegaLiteParamGuard — faceted selection-param fan-out (D-314)', () => {
  it('recognises a facet operator spec and finds its selection params', () => {
    const spec = facetedSelectionSpec();
    expect(isFacetOperatorSpec(spec)).toBe(true);
    const names = collectSelectionParamNames(spec);
    expect(names.has('pick')).toBe(true);
    expect(names.size).toBe(1);
  });

  // The failing-without-the-fix assertion: before neutralization the
  // faceted spec still declares the selection param and still carries the
  // param-driven condition that fans out per cell. After it, neither remains.
  it('strips the faceted selection param and resolves its condition to the matched branch', () => {
    const spec = facetedSelectionSpec();

    // Pre-condition (this is exactly the state that timed out).
    expect(spec.params.some((p: any) => p.name === 'pick')).toBe(true);
    expect(spec.spec.layer[0].encoding.opacity.condition).toBeDefined();

    const { removedParams, resolvedConditions } = neutralizeFacetedSelectionParams(spec);
    expect(removedParams).toBe(1);
    expect(resolvedConditions).toBe(1);

    // The selection param declaration is gone (empty params block removed).
    expect(spec.params).toBeUndefined();

    // The opacity condition resolved to the SELECTED-state branch (value:1),
    // not the dimmed else-branch (0.3), and no longer references any param.
    const opacity = spec.spec.layer[0].encoding.opacity;
    expect(opacity.condition).toBeUndefined();
    expect(opacity.param).toBeUndefined();
    expect(opacity.value).toBe(1);

    // The non-interactive content (rule layer, axes, color) is untouched.
    expect(spec.spec.layer[1].encoding.y.aggregate).toBe('mean');
    expect(spec.spec.encoding.color.field).toBe('c');
  });

  it('leaves a NON-faceted interactive spec completely untouched', () => {
    const spec: any = {
      data: { values: [{ c: 'A', v: 1 }] },
      params: [{ name: 'pick', select: { type: 'point', fields: ['c'] }, bind: 'legend' }],
      mark: 'bar',
      encoding: {
        x: { field: 'c', type: 'nominal' },
        opacity: { condition: { param: 'pick', value: 1 }, value: 0.3 },
      },
    };
    const before = JSON.parse(JSON.stringify(spec));
    const { removedParams } = neutralizeFacetedSelectionParams(spec);
    expect(removedParams).toBe(0);
    expect(spec).toEqual(before);
  });

  it('preserves a variable (non-selection) param even inside a faceted spec', () => {
    const spec: any = {
      params: [{ name: 'size', value: 5, bind: { input: 'range', min: 1, max: 10 } }],
      facet: { field: 'q', type: 'nominal' },
      spec: { mark: 'point', encoding: { size: { value: 5 } } },
    };
    const { removedParams } = neutralizeFacetedSelectionParams(spec);
    expect(removedParams).toBe(0);
    expect(spec.params.some((p: any) => p.name === 'size')).toBe(true);
  });

  it('handles the row/column encoding form of faceting', () => {
    const spec: any = {
      params: [{ name: 'brush', select: { type: 'interval' } }],
      mark: 'point',
      encoding: {
        row: { field: 'q', type: 'nominal' },
        x: { field: 'c', type: 'nominal' },
        size: { condition: { param: 'brush', value: 100 }, value: 20 },
      },
    };
    expect(isFacetOperatorSpec(spec)).toBe(true);
    const { removedParams, resolvedConditions } = neutralizeFacetedSelectionParams(spec);
    expect(removedParams).toBe(1);
    expect(resolvedConditions).toBe(1);
    expect(spec.params).toBeUndefined();
    expect(spec.encoding.size.value).toBe(100);
    expect(spec.encoding.size.condition).toBeUndefined();
  });
});
