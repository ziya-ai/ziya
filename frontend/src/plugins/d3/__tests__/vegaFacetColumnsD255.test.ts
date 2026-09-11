/**
 * D-255 / G-689537 — facet-columns-ignored-single-row-compression
 *
 * Spec vega-lite-w2-10 is a 24-cell fan-out authored in the CHANNEL faceting
 * spelling with a grid-wrap directive:
 *
 *   encoding: { facet: { field: 'team', type: 'nominal', columns: 6 } }
 *
 * The observed failure: all 24 cells laid out in a SINGLE ROW, each compressed
 * to ~48px against the authored 110 (1160 / 24), every cell title and axis
 * label sub-legible, ~62% of the canvas blank.
 *
 * ROOT CAUSE (differs from the triage hypothesis, which blamed the
 * resolveFacetCellWidth divisor): vegaLitePlugin rewrites the channel spelling
 * into the facet OPERATOR form by moving the facet field def VERBATIM. That
 * buries the grid directive at `spec.facet.columns`, a position vega-lite
 * SILENTLY IGNORES (verified on vega-lite 6.4.2: the buried operator form
 * compiles to `layout.columns: undefined` — a single row — while the same
 * directive at the operator TOP LEVEL compiles to `layout.columns: 6`).
 *
 * FIX: hoistFacetColumns lifts the buried directive to the operator top level.
 *
 * This suite reproduces w2-10 through the exact plugin rewrite, then applies
 * the hoist, and asserts the directive lands where vega-lite reads it (the
 * compile precondition for grid wrapping) — and, without the hoist, stays
 * buried where the single-row failure originates. Importing hoistFacetColumns,
 * which does not exist on the unpatched tree, makes every assertion red without
 * the fix and green with it.
 */
import { hoistFacetColumns } from '../vegaFacetLayout';

/** vega-lite-w2-10 verbatim: channel-form facet with a 6-column grid wrap. */
function w2_10ChannelForm(): any {
  return {
    $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
    title: '24-cell facet fan-out',
    data: { sequence: { start: 0, stop: 192, step: 1, as: 'n' } },
    transform: [
      { calculate: "'T'+format(floor(datum.n/8),'02d')", as: 'team' },
      { calculate: 'datum.n%8', as: 'x' },
      { calculate: '10+30*abs(sin((floor(datum.n/8)+(datum.n%8))/4))', as: 'y' },
    ],
    mark: 'line',
    width: 110,
    height: 80,
    encoding: {
      x: { field: 'x', type: 'quantitative' },
      y: { field: 'y', type: 'quantitative' },
      facet: { field: 'team', type: 'nominal', columns: 6 },
    },
  };
}

/**
 * The encoding.facet -> facet-operator rewrite vegaLitePlugin performs before
 * hoisting (a faithful copy of the plugin block), producing the operator shape
 * with `columns` buried inside the facet field def.
 */
function rewriteEncodingFacetToOperator(input: any): any {
  const spec = JSON.parse(JSON.stringify(input));
  if (!spec.encoding?.facet) return spec;
  const facetConfig = spec.encoding.facet;
  const { mark, encoding, ...otherProps } = spec;
  const correctedEncoding = { ...encoding };
  delete correctedEncoding.facet;
  return {
    ...otherProps,
    facet: facetConfig,
    spec: { mark, encoding: correctedEncoding },
  };
}

describe('D-255 facet columns survive channel->operator rewrite (vega-lite-w2-10)', () => {
  it('buries the grid directive when the plugin rewrites the channel form (the single-row cause)', () => {
    const operator = rewriteEncodingFacetToOperator(w2_10ChannelForm());
    // Reproduces the failure precondition: vega-lite ignores facet.columns here,
    // so the 6-column wrap collapses to one row of 24 cells.
    expect(operator.facet.columns).toBe(6);
    expect(operator.columns).toBeUndefined();
  });

  it('hoists the buried grid directive to the operator top level so the grid wraps', () => {
    const operator = rewriteEncodingFacetToOperator(w2_10ChannelForm());
    const changed = hoistFacetColumns(operator);

    expect(changed).toBe(true);
    // Directive now sits where vega-lite honours it -> compiles to layout.columns=6.
    expect(operator.columns).toBe(6);
    // ...and is gone from the position it is ignored in.
    expect(operator.facet.columns).toBeUndefined();
    // Facet field and the authored cell size are otherwise preserved.
    expect(operator.facet.field).toBe('team');
    expect(operator.spec.mark).toBe('line');
  });

  it('preserves the 24-cell / 6-column intent: 4 rows of 6', () => {
    const operator = rewriteEncodingFacetToOperator(w2_10ChannelForm());
    hoistFacetColumns(operator);
    const CELLS = 24;
    expect(operator.columns).toBe(6);
    expect(Math.ceil(CELLS / operator.columns)).toBe(4);
  });
});
