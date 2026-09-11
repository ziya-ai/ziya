/**
 * Facet-layout analysis for Vega-Lite specs.
 *
 * Vega-Lite spells faceting two ways and only one of them is a top-level key:
 *
 *   the OPERATOR  { facet: {...}, spec: {...} }
 *   the CHANNELS  { encoding: { row | column | facet: {...} } }
 *
 * vegaSizing only recognised the operator, so a channel-faceted spec was
 * classified as a simple single view and handed width:'container'. Vega-Lite
 * rejects that — "Width 'container' only works for single views and layered
 * views", emitted once per facet cell plus once for the outer spec — and then
 * falls back to a default cell width that overflows the container.
 *
 * The other half of the problem is that a faceted top-level width sizes ONE
 * CELL, not the assembled view. Handing it the full container width scales the
 * whole grid up: measured on vega-lite 6.4, a six-column facet given a 1160px
 * cell width assembled to 7304px.
 */

/** Encoding channels that turn an otherwise single view into a faceted one. */
const FACET_CHANNELS = ['row', 'column', 'facet'] as const;

/**
 * OPENING ESTIMATE of the horizontal chrome a faceted view adds OUTSIDE its
 * cells: legend, facet header labels, y-axis labels and titles, view padding.
 *
 * Only an estimate, and deliberately so — two measured facts make an accurate
 * one impossible at this point in the pipeline:
 *
 *   1. Chrome is a LAYOUT OUTPUT, not a spec property. On vega-lite 6.4 the
 *      same row-faceted spec measured cellWidth + 285px with short legend
 *      labels and cellWidth + 437px with long ones.
 *   2. D3Renderer calls plugin.render() on a DETACHED container (tempContainer
 *      is appended only after the render promise resolves), so the container
 *      width measured there is the 400px floor, not the real width. No
 *      arithmetic on it can be correct.
 *
 * The exact cell width is MEASURED and solved after attachment by
 * ./vegaFacetFit. This constant only has to keep the pre-fit frame sane, which
 * means staying small enough that `400 - estimate` remains usefully positive:
 * an earlier value of 450 underflowed the 400px floor and would have pinned
 * every faceted chart to MIN_FACET_CELL_WIDTH before the fit could run.
 */
export const FACET_CHROME_ESTIMATE = 160;

/** Floor for a derived cell width, so a narrow container still charts. */
export const MIN_FACET_CELL_WIDTH = 120;

/**
 * Columns assumed when cells are laid out horizontally but the facet extent
 * cannot be counted, i.e. the data arrives by URL or by name rather than
 * inline. Guessing low overflows and guessing high wastes space; this errs
 * toward not overflowing.
 */
export const ASSUMED_FACET_COLUMNS = 4;

/**
 * Where a per-cell width has to be written.
 *
 * The operator IGNORES a top-level width: given width:1160 at the top level, a
 * row-faceted operator spec still assembled at the default cell width (463px
 * total). Its width belongs to the inner `spec`.
 */
export type WidthTarget = 'top' | 'spec';

export interface FacetLayout {
  faceted: boolean;
  /** Cells laid out side by side. 1 for row-only faceting. */
  columns: number;
  widthTarget: WidthTarget;
}

/** Narrow to a usable dimension: only a positive finite number qualifies. */
export function positiveNumber(value: any): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
    ? value
    : null;
}

/** Collect row / column / wrapped facet definitions from either spelling. */
function facetFieldDefs(spec: any): { row?: any; column?: any; wrap?: any } {
  const defs: { row?: any; column?: any; wrap?: any } = {};
  const encoding = spec?.encoding;
  if (encoding && typeof encoding === 'object') {
    if (encoding.row) defs.row = encoding.row;
    if (encoding.column) defs.column = encoding.column;
    if (encoding.facet) defs.wrap = encoding.facet;
  }
  const operator = spec?.facet;
  if (operator && typeof operator === 'object') {
    if (operator.row) defs.row = operator.row;
    if (operator.column) defs.column = operator.column;
    // { facet: { field, type } } is the wrapped form of the operator.
    if (!operator.row && !operator.column && operator.field) {
      defs.wrap = operator;
    }
  }
  return defs;
}

/** Distinct values of `field` in an inline dataset, or null if uncountable. */
function distinctInlineValues(spec: any, field: any): number | null {
  const values = spec?.data?.values;
  if (!Array.isArray(values) || typeof field !== 'string' || !field) {
    return null;
  }
  const seen = new Set<any>();
  for (const row of values) {
    if (row && typeof row === 'object') seen.add(row[field]);
  }
  return seen.size > 0 ? seen.size : null;
}

/** True for either faceting spelling. */
export function isFacetedSpec(spec: any): boolean {
  if (!spec || typeof spec !== 'object') return false;
  if (spec.facet) return true;
  const encoding = spec.encoding;
  if (!encoding || typeof encoding !== 'object') return false;
  return FACET_CHANNELS.some((channel) => encoding[channel] != null);
}

/** How a faceted spec's cells lay out, and where its width belongs. */
export function describeFacetLayout(spec: any): FacetLayout {
  if (!isFacetedSpec(spec)) {
    return { faceted: false, columns: 1, widthTarget: 'top' };
  }
  const widthTarget: WidthTarget =
    spec.facet && spec.spec && typeof spec.spec === 'object' ? 'spec' : 'top';

  const defs = facetFieldDefs(spec);
  const horizontal = defs.column ?? defs.wrap;

  // An explicit grid wrap is authoritative. `columns: 0` means "a single row"
  // rather than "zero columns", so it counts only when >= 1.
  const explicit =
    positiveNumber(spec.columns) ?? positiveNumber(defs.wrap?.columns);

  let columns: number;
  if (!horizontal) {
    columns = 1; // row-only faceting stacks vertically
  } else if (explicit !== null) {
    columns = Math.floor(explicit);
  } else {
    columns =
      distinctInlineValues(spec, horizontal.field) ?? ASSUMED_FACET_COLUMNS;
  }
  return { faceted: true, columns: Math.max(1, columns), widthTarget };
}

/**
 * Opening per-cell width for a faceted spec. An authored width is kept — that
 * is the author choosing a cell size — otherwise the estimated chrome is
 * subtracted and the remainder divided across the cells that sit side by side.
 *
 * Corrected by measurement in ./vegaFacetFit once the view is attached, so this
 * only has to be sane, not right.
 */
export function resolveFacetCellWidth(
  spec: any,
  availableWidth: number,
): number {
  const layout = describeFacetLayout(spec);
  const authored =
    layout.widthTarget === 'spec'
      ? (positiveNumber(spec?.spec?.width) ?? positiveNumber(spec?.width))
      : positiveNumber(spec?.width);
  if (authored !== null) return authored;
  const usable = availableWidth - FACET_CHROME_ESTIMATE;
  return Math.max(Math.floor(usable / layout.columns), MIN_FACET_CELL_WIDTH);
}

/**
 * Lift a wrapped facet's `columns` grid directive from where Vega-Lite ignores
 * it to where it honours it (D-234 / G-17).
 *
 * Vega-Lite reads a wrapped facet's grid width ONLY at the TOP LEVEL of the
 * facet-OPERATOR spec ({ facet: {...}, columns: N, spec: {...} }). It silently
 * ignores `columns` when it sits INSIDE the facet field definition
 * (spec.facet.columns) — measured on vega-lite 6.4.2: the operator form with a
 * buried columns compiles to `layout.columns: undefined`, the top-level form to
 * `layout.columns: N`.
 *
 * vegaLitePlugin rewrites the common LLM spelling `encoding: { facet: { field,
 * columns } }` into the operator form by moving the facet field def VERBATIM,
 * which buries `columns` at spec.facet.columns and collapses an N-column wrap
 * (24 cells / 6 columns) into one compressed single row. This hoists the buried
 * value to the operator top level so the grid actually wraps.
 *
 * Only touches the OPERATOR form (spec.facet). The channel form
 * (encoding.facet.columns) IS honoured by Vega-Lite directly and is left alone.
 * An author-set top-level `columns` always wins; the buried duplicate is still
 * removed so it cannot confuse the compiler, but the effective grid is not
 * changed. Non-positive / non-finite buried values are ignored.
 *
 * Mutates in place (matching how the plugin works on its local spec copy) and
 * returns true only when it changed the effective grid width.
 */
export function hoistFacetColumns(spec: any): boolean {
  if (!spec || typeof spec !== 'object') return false;
  const facet = spec.facet;
  if (!facet || typeof facet !== 'object') return false;

  const buried = positiveNumber(facet.columns);
  if (buried === null) return false;

  const authoredTop = positiveNumber(spec.columns);
  if (authoredTop !== null) {
    // Author already chose a top-level grid width; keep it, drop the duplicate.
    delete facet.columns;
    return false;
  }

  spec.columns = Math.floor(buried);
  delete facet.columns;
  return true;
}

/**
 * Mirror a facet OPERATOR's authored per-cell width/height from where
 * Vega-Lite IGNORES them (the operator top level) to where it READS them (the
 * inner `spec`) — the height companion to hoistFacetColumns (D-255 / D-312).
 *
 * A facet operator ({ facet: {...}, spec: {...} }) takes its cell size from the
 * INNER spec; a width/height on the operator top level is silently dropped and
 * the cell falls back to Vega-Lite's 300x300 default (measured on vega-lite
 * 6.4.2: top-level width:110 height:80 with an empty inner spec compiles to
 * child_width=300, child_height=300; the same values in the inner spec compile
 * to 110/80).
 *
 * vegaLitePlugin's `encoding: { facet: {...} }` -> operator rewrite moves the
 * facet field def but leaves the authored top-level `width`/`height` at the top
 * level, so the cell is inflated to the 300px default. For a wrapped high-cell
 * grid that inflation compounds down the rows: a 24-cell / 6-column fan-out
 * authored at 110x80 became a 6x4 grid of 300px-tall cells (~1300px assembled
 * height) that is then clipped by the capture window — the D-312 symptom and
 * the D-255 regression.
 *
 * Copies each authored top-level dimension into the inner spec only when the
 * inner spec does not already carry it (an author-set inner cell size wins).
 * The top-level value is LEFT IN PLACE: the operator ignores it for layout, and
 * removing it would let vega-embed inject its own width when spec.width is
 * absent (the reason applySizing mirrors rather than moves). 'container' is
 * accepted as a legitimate responsive width; any non-positive/non-finite value
 * is ignored.
 *
 * Only acts on the operator form (both spec.facet and spec.spec present), so a
 * pre-rewrite channel-form spec or a simple view is untouched. Mutates in place
 * and returns true only when it moved a dimension.
 */
export function sinkFacetCellSize(spec: any): boolean {
  if (!spec || typeof spec !== 'object') return false;
  const facet = spec.facet;
  const inner = spec.spec;
  if (!facet || typeof facet !== 'object') return false;
  if (!inner || typeof inner !== 'object') return false;

  let changed = false;
  for (const dim of ['width', 'height'] as const) {
    const top = spec[dim];
    const usable = positiveNumber(top) !== null || top === 'container';
    if (usable && inner[dim] === undefined) {
      inner[dim] = top;
      changed = true;
    }
  }
  return changed;
}
