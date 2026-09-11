/**
 * D-255 (regression) / D-312 / G-fb7bc6 —
 * facet-cell-size-inflated-grid-clipped-to-window
 *
 * Spec vega-lite-w2-10 is a 24-cell fan-out authored in the CHANNEL faceting
 * spelling with a 6-column wrap AND an authored per-cell size:
 *
 *   width: 110, height: 80,
 *   encoding: { facet: { field: 'team', type: 'nominal', columns: 6 } }
 *
 * vegaLitePlugin rewrites the channel form into the facet OPERATOR form
 * ({ facet, spec }). A facet operator reads its per-cell size from the INNER
 * `spec`; a width/height left on the operator TOP LEVEL is IGNORED and the cell
 * falls back to Vega-Lite's 300x300 default (verified on vega-lite 6.4.2:
 * top-level width:110 height:80 with an empty inner spec compiles to
 * child_width=300, child_height=300; the same values inside `spec` compile to
 * 110/80).
 *
 * The rewrite moved the facet field def but left the authored width/height at
 * the top level, so each cell inflated from 80px tall to 300px. hoistFacetColumns
 * had already made the 6-column wrap take effect, so the 24 cells became a 6x4
 * grid of 300px-tall cells (~1300px assembled height) that the ~960px capture
 * window then CLIPPED — top and bottom rows, title and shared x axis lost. That
 * is D-312, and it is what re-broke the previously-verified D-255.
 *
 * ROOT CAUSE (differs from the triage hypothesis, which blamed the width solver
 * in vegaFacetFit.ts): the width fit only ever writes child_width and never
 * touches child_height, so it cannot inflate a cell to 300px tall. The 300px
 * came from the operator ignoring the top-level height. FIX: sinkFacetCellSize
 * mirrors the authored top-level cell size DOWN into the inner spec where the
 * operator honours it.
 *
 * This suite reproduces w2-10 through the exact plugin rewrite, applies the
 * hoist + sink, and asserts the authored cell size lands in the inner spec (the
 * compile precondition for an 80px-tall cell) — and that, without the sink, it
 * stays stranded at the top level against an empty inner spec (the 300px-default
 * cause). Importing sinkFacetCellSize, which does not exist on the unpatched
 * tree, makes every assertion red without the fix and green with it.
 */
import { hoistFacetColumns, sinkFacetCellSize } from '../vegaFacetLayout';

/** vega-lite-w2-10 verbatim: channel-form facet, 6-column wrap, authored 110x80. */
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
 * The encoding.facet -> facet-operator rewrite vegaLitePlugin performs (a
 * faithful copy of the plugin block): the facet field def moves to the top
 * level, mark + non-facet encoding move to the inner spec, and everything else
 * — INCLUDING the authored top-level width/height — stays at the top level.
 */
function rewriteEncodingFacetToOperator(input: any): any {
  const spec = JSON.parse(JSON.stringify(input));
  if (!spec.encoding?.facet) return spec;
  const facetConfig = spec.encoding.facet;
  const { mark, encoding, ...otherProps } = spec;
  const correctedEncoding = { ...encoding };
  delete correctedEncoding.facet;
  return {
    ...otherProps, // preserves the authored top-level width/height (the bug)
    facet: facetConfig,
    spec: { mark, encoding: correctedEncoding },
  };
}

describe('D-312 facet cell size survives channel->operator rewrite (vega-lite-w2-10)', () => {
  it('strands the authored cell size at the top level after the rewrite (the 300px-default cause)', () => {
    const operator = rewriteEncodingFacetToOperator(w2_10ChannelForm());
    hoistFacetColumns(operator);
    // The failure precondition: authored size is at the top (ignored by the
    // operator) while the inner spec — which the operator DOES read — is empty,
    // so Vega-Lite defaults the cell to 300x300.
    expect(operator.width).toBe(110);
    expect(operator.height).toBe(80);
    expect(operator.spec.width).toBeUndefined();
    expect(operator.spec.height).toBeUndefined();
  });

  it('mirrors the authored cell size into the inner spec so the cell stays 110x80', () => {
    const operator = rewriteEncodingFacetToOperator(w2_10ChannelForm());
    hoistFacetColumns(operator);
    const changed = sinkFacetCellSize(operator);

    expect(changed).toBe(true);
    // Cell size now sits where the operator honours it -> 80px-tall cells,
    // 4 rows ~= 320px assembled, well within the capture window (no clipping).
    expect(operator.spec.width).toBe(110);
    expect(operator.spec.height).toBe(80);
    // The 6-column wrap (D-255) still holds: 24 cells / 6 = 4 rows.
    expect(operator.columns).toBe(6);
    expect(Math.ceil(24 / operator.columns)).toBe(4);
  });

  it('does not overwrite an author-set inner cell size', () => {
    const operator = rewriteEncodingFacetToOperator(w2_10ChannelForm());
    operator.spec.width = 200;
    operator.spec.height = 150;
    sinkFacetCellSize(operator);
    // Inner author intent wins; the top-level values are not sunk over it.
    expect(operator.spec.width).toBe(200);
    expect(operator.spec.height).toBe(150);
  });

  it('is a no-op on a channel-form spec (no operator yet) and a simple view', () => {
    const channel = w2_10ChannelForm();
    expect(sinkFacetCellSize(channel)).toBe(false);
    expect(sinkFacetCellSize({ mark: 'bar', width: 300, height: 200 })).toBe(false);
  });
});
