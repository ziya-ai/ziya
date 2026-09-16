/**
 * Width-aware fitting for the two Vega text elements that are NOT laid out
 * against the available width: the chart title and legend labels.
 *
 *  - Title/subtitle: Vega neither wraps nor shrinks a title. Under
 *    autosize fit-x the SVG is exactly the pane width, so a title longer
 *    than the pane is clipped at both ends. Vega DOES accept an array of
 *    strings as multi-line title text, so we word-wrap a long authored
 *    string into one.
 *  - Legend labels: Vega truncates every legend label at a fixed
 *    `labelLimit` (default 160px) with an ellipsis, regardless of how much
 *    room the legend actually has. We size the limit to the longest label,
 *    bounded by the room the legend's orientation leaves in the pane.
 *
 * Text is measured with a 2D canvas when one is available (the browser), and
 * falls back to a per-character estimate (jsdom / node tests).
 */

export const VEGA_TITLE_FONT_PX = 13;
export const VEGA_SUBTITLE_FONT_PX = 12;
export const VEGA_LEGEND_LABEL_FONT_PX = 10;
/** Vega's built-in legend labelLimit; a fitted value at or below it is a no-op. */
export const VEGA_DEFAULT_LEGEND_LABEL_LIMIT = 160;
/** Used when the pane width is unknown (detached container reporting 0). */
export const LEGEND_LABEL_LIMIT_UNKNOWN_WIDTH_PX = 260;
/** Never shrink a fitted limit below this; a narrower legend is useless. */
export const LEGEND_LABEL_LIMIT_MIN_PX = 80;
/** Symbol + paddings a legend entry spends before its label starts. */
const LEGEND_ENTRY_CHROME_PX = 30;
/** Horizontal pane padding the chart itself does not get to use. */
const PANE_GUTTER_PX = 40;
/** A side legend may take this fraction of the pane before it starves the plot. */
const SIDE_LEGEND_MAX_FRACTION = 0.35;

let _ctx: CanvasRenderingContext2D | null | undefined;
function measureContext(): CanvasRenderingContext2D | null {
  if (_ctx === undefined) {
    _ctx = null;
    try {
      if (typeof document !== 'undefined') {
        _ctx = document.createElement('canvas').getContext('2d') ?? null;
      }
    } catch {
      _ctx = null;
    }
  }
  return _ctx;
}

/** Width of `text` at `fontPx` in a proportional sans face, in CSS px. */
export function estimateTextWidthPx(text: unknown, fontPx: number, bold = false): number {
  const s = String(text ?? '');
  if (!s) return 0;
  const ctx = measureContext();
  if (ctx) {
    ctx.font = `${bold ? 'bold ' : ''}${fontPx}px sans-serif`;
    const w = ctx.measureText(s).width;
    if (Number.isFinite(w) && w > 0) return Math.ceil(w);
  }
  // Average advance of a sans face; bold runs ~8% wider.
  return Math.ceil(s.length * fontPx * (bold ? 0.56 : 0.52));
}

/**
 * Greedy word-wrap. Returns the input unchanged when it already fits (or is
 * not a string), otherwise an array of lines. A single unbreakable word longer
 * than `maxPx` stays on its own line rather than being split mid-word.
 */
export function wrapTextToWidth(
  text: unknown, maxPx: number, fontPx: number, bold = false,
): string | string[] | unknown {
  if (typeof text !== 'string' || !(maxPx > 0)) return text;
  if (estimateTextWidthPx(text, fontPx, bold) <= maxPx) return text;
  const words = text.split(/\s+/).filter(Boolean);
  const lines: string[] = [];
  let cur = '';
  for (const w of words) {
    const cand = cur ? `${cur} ${w}` : w;
    if (cur && estimateTextWidthPx(cand, fontPx, bold) > maxPx) {
      lines.push(cur);
      cur = w;
    } else {
      cur = cand;
    }
  }
  if (cur) lines.push(cur);
  return lines.length > 1 ? lines : text;
}

/**
 * Wrap a spec's top-level title and subtitle to `maxPx`. Only authored
 * STRINGS are touched: an array is the author's own line breaking, and a
 * title carrying `limit` has asked Vega to truncate instead. Returns whether
 * anything changed. Mutates `spec`.
 */
export function wrapSpecTitles(spec: any, maxPx: number): boolean {
  if (!spec || typeof spec !== 'object' || !(maxPx > 0)) return false;
  const t = spec.title;
  if (typeof t === 'string') {
    const wrapped = wrapTextToWidth(t, maxPx, VEGA_TITLE_FONT_PX, true);
    if (wrapped !== t) { spec.title = wrapped; return true; }
    return false;
  }
  if (!t || typeof t !== 'object' || Array.isArray(t)) return false;
  if (typeof t.limit === 'number') return false;
  let changed = false;
  const titlePx = typeof t.fontSize === 'number' && t.fontSize > 0 ? t.fontSize : VEGA_TITLE_FONT_PX;
  const subPx = typeof t.subtitleFontSize === 'number' && t.subtitleFontSize > 0 ? t.subtitleFontSize : VEGA_SUBTITLE_FONT_PX;
  if (typeof t.text === 'string') {
    const w = wrapTextToWidth(t.text, maxPx, titlePx, true);
    if (w !== t.text) { t.text = w; changed = true; }
  }
  if (typeof t.subtitle === 'string') {
    const w = wrapTextToWidth(t.subtitle, maxPx, subPx, false);
    if (w !== t.subtitle) { t.subtitle = w; changed = true; }
  }
  return changed;
}

export interface LegendLabelLimitOptions {
  /** The concrete label strings the legend will show. */
  labels: Array<string | number>;
  /** Pane width in px; <= 0 when unknown. */
  containerWidthPx: number;
  /** Legend orient; defaults to Vega's 'right'. */
  orient?: string;
  /** Legend columns for a top/bottom legend; defaults to 1. */
  columns?: number;
  /** Lower bound that keeps prefixed labels distinguishable (D-313). */
  floorPx?: number;
  fontPx?: number;
}

/** Room a legend of this orientation has for ONE label, given the pane width. */
export function legendLabelRoomPx(containerWidthPx: number, orient = 'right', columns = 1): number {
  if (!(containerWidthPx > 0)) return LEGEND_LABEL_LIMIT_UNKNOWN_WIDTH_PX;
  const horizontal = orient === 'bottom' || orient === 'top';
  const room = horizontal
    ? Math.floor((containerWidthPx - PANE_GUTTER_PX) / Math.max(1, columns)) - LEGEND_ENTRY_CHROME_PX
    : Math.floor(containerWidthPx * SIDE_LEGEND_MAX_FRACTION);
  return Math.max(room, LEGEND_LABEL_LIMIT_MIN_PX);
}

/**
 * labelLimit wide enough for the longest label, capped by the room the legend
 * has, floored by the disambiguation width. Always >= 1 so it never becomes
 * Vega's "no truncation" sentinel (0) by accident.
 */
export function computeLegendLabelLimitPx(opts: LegendLabelLimitOptions): number {
  const fontPx = opts.fontPx ?? VEGA_LEGEND_LABEL_FONT_PX;
  const longest = (opts.labels || []).reduce<number>(
    (m, l) => Math.max(m, estimateTextWidthPx(l, fontPx)), 0,
  ) + 6; // ellipsis slack: Vega compares against the limit, so pad past it
  const room = legendLabelRoomPx(opts.containerWidthPx, opts.orient, opts.columns);
  const floor = Math.min(opts.floorPx ?? 0, room);
  return Math.max(Math.min(longest, room), floor, 1);
}
