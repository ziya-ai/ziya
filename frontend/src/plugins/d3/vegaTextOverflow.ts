/**
 * Post-render correction for data labels clipped at the SVG edge.
 *
 * A text mark anchored near the top of the x domain (`x: hi, align: left`)
 * extends past the plot area by its own pixel length. Under
 * autosize 'fit-x' the total view is bounded by the container, so anything
 * past the SVG's right edge is simply cut off ("evidence PART…"). Vega has
 * no layout pass for mark extents, so the correction has to be measured
 * after render and fed back as view padding: with `contains: 'padding'`,
 * growing the padding shrinks the plot area and pulls the anchor inward.
 *
 * Everything DOM-dependent stays in the plugin; these helpers are pure so
 * the arithmetic is testable. Rects use the getBoundingClientRect shape.
 */

export interface BoxRect {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

export interface SidePadding {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

/** Overrun at or below this is anti-aliasing noise, not clipping. */
export const TEXT_OVERFLOW_TOLERANCE_PX = 1;

/**
 * Never hand more than this fraction of the SVG width to a single side of
 * padding. A label longer than that is a spec problem the plot must not be
 * sacrificed for.
 */
export const TEXT_OVERFLOW_MAX_PAD_FRACTION = 0.4;

/**
 * Largest horizontal overrun of any text rect beyond the SVG rect, per side.
 * Zero-size rects (jsdom, or an SVG not yet laid out) are ignored so the
 * correction is a no-op wherever measurement is meaningless.
 */
export function computeTextOverflow(
  textRects: BoxRect[],
  svgRect: BoxRect,
): { right: number; left: number } {
  let right = 0;
  let left = 0;
  for (const r of textRects) {
    if (!r || !(r.right - r.left > 0)) continue;
    right = Math.max(right, r.right - svgRect.right);
    left = Math.max(left, svgRect.left - r.left);
  }
  const cap = Math.max(0, (svgRect.right - svgRect.left) * TEXT_OVERFLOW_MAX_PAD_FRACTION);
  const clamp = (v: number): number =>
    v > TEXT_OVERFLOW_TOLERANCE_PX ? Math.min(Math.ceil(v), cap) : 0;
  return { right: clamp(right), left: clamp(left) };
}

/** Vega's view.padding() is a number or a partial object; expand to four sides. */
export function normalizePadding(padding: unknown): SidePadding {
  if (typeof padding === 'number' && Number.isFinite(padding)) {
    return { top: padding, right: padding, bottom: padding, left: padding };
  }
  const p = (padding && typeof padding === 'object' ? padding : {}) as Partial<SidePadding>;
  const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
  return { top: num(p.top), right: num(p.right), bottom: num(p.bottom), left: num(p.left) };
}

/** Current padding plus the measured overrun on the sides that need it. */
export function growPadding(
  current: unknown,
  extra: { right: number; left: number },
): SidePadding {
  const p = normalizePadding(current);
  return { ...p, right: p.right + extra.right, left: p.left + extra.left };
}
