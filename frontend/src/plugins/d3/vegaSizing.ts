/**
 * Pure width/autosize resolution for Vega-Lite specs.
 *
 * Extracted from vegaLitePlugin.render so the sizing decision — the thing
 * that actually broke (charts rendering small and centered) — is testable
 * without standing up vegaEmbed, jsdom layout, and the async plugin
 * pipeline. The plugin calls these; it no longer owns the decision.
 *
 * The governing rule: a fixed pixel width is never correct for a chart
 * embedded in a variable-width conversation. The container is the
 * authority, so an authored numeric width is replaced with Vega-Lite's
 * own 'container' responsive mode rather than being honoured verbatim.
 */

import {
  describeFacetLayout,
  isFacetedSpec,
  positiveNumber,
  resolveFacetCellWidth,
  type WidthTarget,
} from './vegaFacetLayout';

/** Vega-Lite composite specs cannot take width:'container' at top level. */
export function isCompositeSpec(spec: any): boolean {
  if (!spec || typeof spec !== 'object') return false;
  if (spec.vconcat || spec.hconcat || spec.concat || spec.repeat) return true;
  // Faceting has two spellings and only one is a top-level key, so testing
  // spec.facet alone missed channel faceting. See ./vegaFacetLayout.
  return isFacetedSpec(spec);
}

/**
 * Resolve the width a spec should be rendered at.
 *
 * Simple specs → 'container' (Vega measures the parent itself).
 * Faceted specs → a per-CELL pixel width, because a faceted top-level width
 * sizes one cell rather than the assembled view; passing the container width
 * there turned a six-column facet into a 7304px view inside 1160px.
 * Other composite specs → a concrete pixel width, since Vega-Lite requires
 * one per sub-view; an authored value is kept, otherwise the measured
 * container width is used.
 *
 * Returns the value to assign to spec.width. Never mutates the input.
 */
export function resolveSpecWidth(
  spec: any,
  availableWidth: number,
): number | 'container' {
  if (isFacetedSpec(spec)) {
    return resolveFacetCellWidth(spec, availableWidth);
  }
  if (isCompositeSpec(spec)) {
    // Only a positive finite number is a usable per-sub-view width.
    return positiveNumber(spec?.width) ?? availableWidth;
  }
  return 'container';
}

/**
 * Resolve autosize so it AGREES with the resolved width.
 *
 * This is a correctness constraint, not a preference, but in the opposite
 * direction to what this function originally assumed. Vega-Lite does not
 * reject 'fit' with width:'container' — it warns about the *inverse*
 * pairing: "Width 'container' only works well with autosize 'fit' or
 * 'fit-x'".
 *
 * 'pad' sizes the plot area to the full container and then adds axis,
 * label and legend extent OUTSIDE it, so the assembled view is wider than
 * the container it was measured against and overflows (clipped charts, cut
 * axis titles). 'fit-x' instead subtracts that extent from the available
 * width, keeping the total inside the container.
 *
 * fit-x rather than fit: the width is container-driven while height stays a
 * concrete authored pixel value, so only the horizontal axis should be
 * fitted. Plain 'fit' would additionally squeeze the authored height.
 *
 * Returns the value to assign to spec.autosize. Never mutates the input.
 */
export function resolveAutosize(
  spec: any,
  resolvedWidth: number | 'container',
): { type: string; contains: string } {
  if (resolvedWidth === 'container') {
    return { type: 'fit-x', contains: 'padding' };
  }
  const authored = spec?.autosize;
  // A pixel width means the spec is composite, and Vega-Lite rejects every
  // 'fit' variant there: it warns "Autosize 'fit' only works for single views
  // and layered views" and silently rewrites the value to 'pad'. Defaulting to
  // fit/content, or honouring an authored 'fit', therefore produced a warning
  // on every concat/facet spec and did not apply the autosize requested.
  if (authored && typeof authored === 'object' &&
      (authored.type === 'pad' || authored.type === 'none')) {
    return { ...authored };
  }
  return { type: 'pad', contains: 'padding' };
}

/**
 * Apply the small-height floor the plugin uses after width resolution and
 * height defaulting.
 *
 * The plugin ran `if (height && height < 250) height = 300` UNCONDITIONALLY.
 * That floor is correct for a height the plugin itself *defaulted* (a short
 * container yields ~240px, a squashed plot), but WRONG for a height the author
 * supplied: an authored sparkline / wide-and-short height (e.g. 40, 28) was
 * silently inflated to 300px, destroying the requested aspect ratio (D-267).
 * A vconcat sub-view height was already preserved verbatim; this makes the
 * top-level height obey the same author-intent rule.
 *
 * Pure and side-effect-free so it is unit-testable without vegaEmbed/jsdom,
 * mirroring resolveSpecWidth / resolveAutosize.
 *
 * @param height      current spec.height (already defaulted if it was unset)
 * @param wasAuthored true when spec.height came from the author, not the default
 * @returns the height to assign back to spec.height
 */
export function applyHeightFloor(
  height: number,
  wasAuthored: boolean,
  floor = 250,
  flooredTo = 300,
): number {
  // Honour an explicitly authored height verbatim — including small
  // sparkline / wide-and-short heights the old floor destroyed.
  if (wasAuthored) return height;
  return height < floor ? flooredTo : height;
}

/**
 * Apply both resolutions to a spec in place and report what changed.
 *
 * Mutation matches how the plugin already works on its own local spec
 * copy; the report exists so the plugin can log a replaced authored width
 * without re-deriving the comparison.
 */
export function applySizing(
  spec: any,
  availableWidth: number,
): {
  width: number | 'container';
  autosize: any;
  replacedWidth: number | null;
  widthTarget: WidthTarget;
} {
  // The facet operator keeps its cell width in the inner `spec`; Vega-Lite
  // ignores a top-level width there. The value is MIRRORED to the top level
  // rather than moved, because vega-embed injects its own width option
  // whenever spec.width is absent, which would overwrite what we resolved.
  const { widthTarget } = describeFacetLayout(spec);
  const target = widthTarget === 'spec' ? spec.spec : spec;
  const authoredWidth =
    positiveNumber(target?.width) ?? positiveNumber(spec?.width);
  const width = resolveSpecWidth(spec, availableWidth);
  const autosize = resolveAutosize(spec, width);

  const replacedWidth =
    authoredWidth !== null && authoredWidth !== width ? authoredWidth : null;

  target.width = width;
  if (target !== spec) spec.width = width;
  spec.autosize = autosize;
  return { width, autosize, replacedWidth, widthTarget };
}
