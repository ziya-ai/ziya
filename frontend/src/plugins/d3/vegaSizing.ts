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

/** Vega-Lite composite specs cannot take width:'container' at top level. */
export function isCompositeSpec(spec: any): boolean {
  if (!spec || typeof spec !== 'object') return false;
  return !!(spec.vconcat || spec.hconcat || spec.concat ||
            spec.facet || spec.repeat);
}

/**
 * Resolve the width a spec should be rendered at.
 *
 * Simple specs → 'container' (Vega measures the parent itself).
 * Composite specs → a concrete pixel width, since Vega-Lite requires one
 * per sub-view; an authored value is kept, otherwise the measured
 * container width is used.
 *
 * Returns the value to assign to spec.width. Never mutates the input.
 */
export function resolveSpecWidth(
  spec: any,
  availableWidth: number,
): number | 'container' {
  if (isCompositeSpec(spec)) {
    const authored = spec?.width;
    // Only a positive finite number is a usable per-sub-view width.
    if (typeof authored === 'number' && Number.isFinite(authored) && authored > 0) {
      return authored;
    }
    return availableWidth;
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
  if (authored && typeof authored === 'object') {
    return { ...authored };
  }
  return { type: 'fit', contains: 'content' };
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
): { width: number | 'container'; autosize: any; replacedWidth: number | null } {
  const authoredWidth = spec?.width;
  const width = resolveSpecWidth(spec, availableWidth);
  const autosize = resolveAutosize(spec, width);

  const replacedWidth =
    typeof authoredWidth === 'number' && authoredWidth !== width
      ? authoredWidth
      : null;

  spec.width = width;
  spec.autosize = autosize;
  return { width, autosize, replacedWidth };
}
