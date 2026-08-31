/**
 * Facet-layout analysis for Vega-Lite sizing.
 *
 * REGRESSION UNDER TEST: a spec faceted via `encoding.row` (the channel
 * spelling) was not recognised as composite, so vegaSizing handed it
 * width:'container'. Vega-Lite rejects that with "Width 'container' only works
 * for single views and layered views" — once per facet cell plus once for the
 * outer spec — then falls back to a default cell width. The observed symptom
 * was ten identical warnings, bars running past the right edge, and a missing
 * x-axis.
 *
 * The second half of the defect: a faceted top-level width sizes ONE CELL, not
 * the assembled view. The expectations below are anchored to measurements taken
 * against vega-lite 6.4 by compiling each spec and reading the resulting
 * scenegraph bounds:
 *
 *   row facet,    cell 300/600/900/1160 -> assembled cell + 285 (every time)
 *   row facet,    long legend labels    -> assembled cell + 437 (every time)
 *   column facet x6, cell 1160          -> assembled 7304  (the bug)
 *   column facet x6, cell 193           -> assembled 1533
 *   facet OPERATOR, top-level width 1160 -> assembled 463 (width discarded)
 *
 * NOTE ON THE CHROME ESTIMATE: the width this module derives is only an OPENING
 * estimate. D3Renderer calls plugin.render() on a DETACHED container, so the
 * measured container width is the 400px floor rather than the real width, and
 * no estimate computed here can be accurate. The exact cell width is measured
 * and solved after attachment by ./vegaFacetFit — see that module's tests. What
 * these tests pin is that the estimate is SANE (never negative, never below the
 * floor, never wider than the container it was derived from), not that it is
 * correct.
 *
 * WOULD THESE FAIL PRE-FIX? Yes — the module under test did not exist, so the
 * import itself fails to resolve and the suite cannot run.
 */
import {
  ASSUMED_FACET_COLUMNS,
  FACET_CHROME_ESTIMATE,
  MIN_FACET_CELL_WIDTH,
  describeFacetLayout,
  isFacetedSpec,
  positiveNumber,
  resolveFacetCellWidth,
} from '../vegaFacetLayout';

const CONTAINER_W = 1200;
const USABLE = CONTAINER_W - FACET_CHROME_ESTIMATE;

/** Six distinct facet values, mirroring the reported spec's shape. */
const SIX_DIMS = ['d1', 'd2', 'd3', 'd4', 'd5', 'd6'];
const inlineData = () => ({
  values: SIX_DIMS.flatMap((dim) =>
    ['Ziya', 'Factory', 'Devin'].map((tool) => ({ dim, tool, score: 3 })),
  ),
});

describe('isFacetedSpec', () => {
  it.each(['row', 'column', 'facet'])(
    'recognises the encoding.%s channel spelling', (channel) => {
      // THE regression: these were all classified as simple single views.
      expect(isFacetedSpec({ mark: 'bar', encoding: { [channel]: { field: 'd' } } }))
        .toBe(true);
    });

  it('recognises the facet operator spelling', () => {
    expect(isFacetedSpec({ facet: { row: { field: 'd' } }, spec: { mark: 'bar' } }))
      .toBe(true);
  });

  it('does not treat an ordinary single view as faceted', () => {
    // The positive/negative pair: yOffset grouping is NOT faceting, and is the
    // single-view rewrite that renders correctly with width:'container'.
    expect(isFacetedSpec({
      mark: 'bar',
      encoding: { y: { field: 'd' }, yOffset: { field: 'tool' }, x: { field: 's' } },
    })).toBe(false);
  });

  it('does not treat a layered spec as faceted', () => {
    expect(isFacetedSpec({ layer: [{ mark: 'bar' }] })).toBe(false);
  });

  it('is safe on null / non-object / empty-encoding input', () => {
    expect(isFacetedSpec(null)).toBe(false);
    expect(isFacetedSpec(undefined)).toBe(false);
    expect(isFacetedSpec('nope')).toBe(false);
    expect(isFacetedSpec({ mark: 'bar', encoding: {} })).toBe(false);
  });

  it('ignores a null-valued facet channel rather than counting the key', () => {
    expect(isFacetedSpec({ mark: 'bar', encoding: { row: null } })).toBe(false);
  });
});

describe('positiveNumber', () => {
  it.each([0, -50, NaN, Infinity, '600', null, undefined, {}])(
    'rejects %p as a dimension', (bad) => {
      expect(positiveNumber(bad)).toBeNull();
    });

  it('accepts a positive finite number', () => {
    expect(positiveNumber(250)).toBe(250);
  });
});

describe('describeFacetLayout — how many cells sit side by side', () => {
  it('reports one column for row-only faceting (cells stack vertically)', () => {
    const layout = describeFacetLayout({
      data: inlineData(), mark: 'bar',
      encoding: { row: { field: 'dim' }, x: { field: 'score' } },
    });
    expect(layout).toEqual({ faceted: true, columns: 1, widthTarget: 'top' });
  });

  it('counts distinct inline values for column faceting', () => {
    // Getting this wrong by treating it as 1 column is what produced 7304px.
    const layout = describeFacetLayout({
      data: inlineData(), mark: 'bar',
      encoding: { column: { field: 'dim' }, x: { field: 'score' } },
    });
    expect(layout.columns).toBe(SIX_DIMS.length);
  });

  it('honours an explicit top-level columns wrap over the distinct count', () => {
    const layout = describeFacetLayout({
      data: inlineData(), mark: 'bar', columns: 3,
      encoding: { facet: { field: 'dim' }, x: { field: 'score' } },
    });
    expect(layout.columns).toBe(3);
  });

  it('treats columns:0 as "one row", not zero columns', () => {
    // columns:0 is Vega-Lite's "all facets in a single row", so the extent
    // still has to come from the data. A literal 0 would divide by zero.
    const layout = describeFacetLayout({
      data: inlineData(), mark: 'bar', columns: 0,
      encoding: { facet: { field: 'dim' }, x: { field: 'score' } },
    });
    expect(layout.columns).toBe(SIX_DIMS.length);
  });

  it('falls back to the assumed grid width when the extent is uncountable', () => {
    // Data by URL: nothing to count, and guessing 1 column would overflow.
    const layout = describeFacetLayout({
      data: { url: 'https://example.invalid/d.json' }, mark: 'bar',
      encoding: { column: { field: 'dim' }, x: { field: 'score' } },
    });
    expect(layout.columns).toBe(ASSUMED_FACET_COLUMNS);
  });

  it('sends the width to the inner spec for the facet operator', () => {
    // Vega-Lite discards a top-level width here (measured: 1160 -> 463px).
    const layout = describeFacetLayout({
      data: inlineData(),
      facet: { row: { field: 'dim' } },
      spec: { mark: 'bar', encoding: { x: { field: 'score' } } },
    });
    expect(layout.widthTarget).toBe('spec');
    expect(layout.columns).toBe(1);
  });

  it('keeps the width at the top level for the channel spelling', () => {
    const layout = describeFacetLayout({
      data: inlineData(), mark: 'bar',
      encoding: { row: { field: 'dim' }, x: { field: 'score' } },
    });
    expect(layout.widthTarget).toBe('top');
  });

  it('reports not-faceted without inventing a layout', () => {
    expect(describeFacetLayout({ mark: 'bar' }))
      .toEqual({ faceted: false, columns: 1, widthTarget: 'top' });
  });
});

describe('resolveFacetCellWidth', () => {
  it('reserves chrome for a row facet instead of using the whole container', () => {
    // Measured: assembled = cell + 285 (short legend) or + 437 (long legend).
    // A cell width equal to the container therefore always overflows.
    const width = resolveFacetCellWidth({
      data: inlineData(), mark: 'bar',
      encoding: { row: { field: 'dim' }, x: { field: 'score' } },
    }, CONTAINER_W);
    expect(width).toBe(USABLE);
    expect(width).toBeLessThan(CONTAINER_W);
  });

  it('divides across the columns of a horizontal facet grid', () => {
    const width = resolveFacetCellWidth({
      data: inlineData(), mark: 'bar',
      encoding: { column: { field: 'dim' }, x: { field: 'score' } },
    }, CONTAINER_W);
    expect(width).toBe(Math.floor(USABLE / SIX_DIMS.length));
  });

  it('keeps the resulting grid inside the container', () => {
    // The property that actually matters: cells + reserve must fit. Asserted
    // for both orientations rather than for one hardcoded number.
    for (const channel of ['row', 'column'] as const) {
      const spec = {
        data: inlineData(), mark: 'bar',
        encoding: { [channel]: { field: 'dim' }, x: { field: 'score' } },
      };
      const { columns } = describeFacetLayout(spec);
      const cell = resolveFacetCellWidth(spec, CONTAINER_W);
      expect(cell * columns + FACET_CHROME_ESTIMATE).toBeLessThanOrEqual(CONTAINER_W);
    }
  });

  it('never returns a cell narrower than the floor in a cramped container', () => {
    // Six columns in 300px leaves ~23px per cell once chrome is taken out. A
    // zero or negative cell width makes Vega-Lite emit an empty plot area, and
    // a very small one puts layout into the non-linear regime where assembled
    // width stops tracking cell width — which is what the floor exists to keep
    // unreachable, since vegaFacetFit's linear solve depends on staying above it.
    const width = resolveFacetCellWidth({
      data: inlineData(), mark: 'bar',
      encoding: { column: { field: 'dim' }, x: { field: 'score' } },
    }, 300);
    expect(width).toBe(MIN_FACET_CELL_WIDTH);
  });

  it('holds the floor across every container width, however cramped', () => {
    // The specific arithmetic is an opening estimate that vegaFacetFit
    // corrects by measurement, so pinning one input's exact value only tests
    // the estimate. The invariant that has to survive a change to the estimate
    // is that no container width can drive a cell below the floor — including
    // widths smaller than the chrome estimate itself, where the subtraction
    // goes negative.
    for (const channel of ['row', 'column'] as const) {
      const spec = {
        data: inlineData(), mark: 'bar',
        encoding: { [channel]: { field: 'dim' }, x: { field: 'score' } },
      };
      for (const available of [0, 40, 160, 300, 400, 700, 1200]) {
        const cell = resolveFacetCellWidth(spec, available);
        expect(cell).toBeGreaterThanOrEqual(MIN_FACET_CELL_WIDTH);
      }
    }
  });

  it('keeps an authored cell width — that is the author choosing a cell size', () => {
    const width = resolveFacetCellWidth({
      data: inlineData(), mark: 'bar', width: 220,
      encoding: { column: { field: 'dim' }, x: { field: 'score' } },
    }, CONTAINER_W);
    expect(width).toBe(220);
  });

  it('prefers the inner spec width over a top-level one for the operator', () => {
    // Only the inner value is meaningful to Vega-Lite, so it wins.
    const width = resolveFacetCellWidth({
      data: inlineData(), width: 999,
      facet: { row: { field: 'dim' } },
      spec: { mark: 'bar', width: 260, encoding: { x: { field: 'score' } } },
    }, CONTAINER_W);
    expect(width).toBe(260);
  });

  it('ignores a non-positive authored width and derives one instead', () => {
    const width = resolveFacetCellWidth({
      data: inlineData(), mark: 'bar', width: 0,
      encoding: { row: { field: 'dim' }, x: { field: 'score' } },
    }, CONTAINER_W);
    expect(width).toBe(USABLE);
  });

  it('does not mutate the spec it inspects', () => {
    const spec: any = {
      data: inlineData(), mark: 'bar',
      encoding: { row: { field: 'dim' }, x: { field: 'score' } },
    };
    resolveFacetCellWidth(spec, CONTAINER_W);
    expect(spec.width).toBeUndefined();
  });
});
