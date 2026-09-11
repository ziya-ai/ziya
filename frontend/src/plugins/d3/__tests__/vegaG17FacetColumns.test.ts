/**
 * G-17 / D-017 — facet grid `columns` must survive the encoding.facet ->
 * facet-operator rewrite.
 *
 * Vega-Lite honours a wrapped facet's grid width ONLY at the TOP LEVEL of the
 * operator spec ({ facet: {...}, columns: N, spec: {...} }); it silently
 * ignores `columns` when it sits INSIDE the facet field definition
 * (spec.facet.columns). vegaLitePlugin rewrites the common LLM spelling
 * `encoding: { facet: { field, columns } }` into the operator form by moving
 * the facet field def verbatim, which buried `columns` at spec.facet.columns
 * and collapsed a 24-cell / 6-column wrap into one compressed row.
 *
 * hoistFacetColumns lifts that buried `columns` to the operator top level.
 *
 * Direction: this suite imports `hoistFacetColumns`, which does not exist on
 * the unpatched tree — the import fails, so every assertion is red without the
 * fix and green with it.
 */
import { hoistFacetColumns } from '../vegaFacetLayout';

describe('G-17 D-017 hoistFacetColumns', () => {
  // The shape vegaLitePlugin produces after moving encoding.facet to the
  // operator form: columns is buried inside the facet field def.
  const operatorSpecWithBuriedColumns = () => ({
    $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
    data: { sequence: { start: 0, stop: 192, step: 1, as: 'n' } },
    facet: { field: 'team', type: 'nominal', columns: 6 },
    spec: {
      mark: 'line',
      width: 110,
      height: 80,
      encoding: {
        x: { field: 'x', type: 'quantitative' },
        y: { field: 'y', type: 'quantitative' },
      },
    },
  });

  it('hoists a buried facet-field columns to the operator top level', () => {
    const spec = operatorSpecWithBuriedColumns();
    const changed = hoistFacetColumns(spec);

    expect(changed).toBe(true);
    // Grid-wrap directive now lives where Vega-Lite reads it...
    expect(spec.columns).toBe(6);
    // ...and no longer where it is ignored.
    expect((spec.facet as any).columns).toBeUndefined();
    // The facet field itself is otherwise untouched.
    expect(spec.facet.field).toBe('team');
    expect(spec.facet.type).toBe('nominal');
  });

  it('never overrides an explicit top-level columns the author already set', () => {
    const spec: any = {
      facet: { field: 'team', type: 'nominal', columns: 6 },
      columns: 3,
      spec: { mark: 'line', encoding: {} },
    };
    const changed = hoistFacetColumns(spec);

    // Author's top-level 3 wins; the buried duplicate is still cleaned up so it
    // cannot confuse Vega-Lite, but the effective grid width is not changed.
    expect(spec.columns).toBe(3);
    expect(spec.facet.columns).toBeUndefined();
    expect(changed).toBe(false);
  });

  it('is a no-op for a non-faceted spec', () => {
    const spec: any = { mark: 'bar', encoding: { x: {}, y: {} } };
    expect(hoistFacetColumns(spec)).toBe(false);
    expect(spec.columns).toBeUndefined();
  });

  it('is a no-op for a faceted spec with no columns directive', () => {
    const spec: any = { facet: { field: 't', type: 'nominal' }, spec: { mark: 'bar' } };
    expect(hoistFacetColumns(spec)).toBe(false);
    expect(spec.columns).toBeUndefined();
  });

  it('ignores a non-positive / non-finite columns value', () => {
    const zero: any = { facet: { field: 't', columns: 0 }, spec: {} };
    expect(hoistFacetColumns(zero)).toBe(false);
    expect(zero.columns).toBeUndefined();

    const nan: any = { facet: { field: 't', columns: Number.NaN }, spec: {} };
    expect(hoistFacetColumns(nan)).toBe(false);
    expect(nan.columns).toBeUndefined();
  });
});
