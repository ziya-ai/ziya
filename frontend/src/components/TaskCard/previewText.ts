/**
 * Pure truncation helper for the TaskRunInspector previews.
 *
 * The d3 task post-mortem highlighted two compounding UI problems
 * for tool-call output:
 *
 *   1. Each preview was rendered in its own 120px-tall scrollable
 *      box, *inside* the 320px scrollable inspector body.  78 such
 *      nested scroll containers in one run → unreadable.
 *
 *   2. The previews were full-length even when the user only
 *      wanted to skim, forcing a click-and-scroll dance per call.
 *
 * The fix is a CSS-only outer scroll with previews truncated to
 * a small line/char budget by default, expandable inline on
 * click.  This module owns the truncation math so the React layer
 * is just rendering.
 *
 * Truncation cuts at the earlier of:
 *   - ``maxLines``  : line breaks include LF and CRLF.  A trailing
 *                     newline counts as a line boundary.
 *   - ``maxChars``  : raw character count (UTF-16 code units, same
 *                     as JavaScript ``.length`` — adequate for a
 *                     UI hint, not a security boundary).
 *
 * Both limits are clamped to at least 1 so callers can't
 * accidentally produce an empty preview from a non-empty input.
 */

export interface PreviewResult {
  /** The truncated text safe to render. */
  shown: string;
  /** True iff truncation actually occurred. */
  truncated: boolean;
  /** Total line count of the original input (≥ 0). */
  fullLines: number;
  /** Total char count of the original input. */
  fullChars: number;
}

export function truncatePreview(
  text: string,
  maxLines: number,
  maxChars: number,
): PreviewResult {
  if (typeof text !== 'string' || text.length === 0) {
    return { shown: '', truncated: false, fullLines: 0, fullChars: 0 };
  }
  const lineCap = Math.max(1, Math.floor(maxLines));
  const charCap = Math.max(1, Math.floor(maxChars));

  // Line count: split on \n then collapse \r at end of each line.
  // Use the resulting array length as line count.  This treats a
  // trailing newline as creating an empty final "line" — matches
  // what users see in pre-rendered terminal output.
  const allLines = text.split('\n');
  const fullLines = allLines.length;
  const fullChars = text.length;

  // Apply line cap first, then char cap, taking the shorter result.
  let lineCut = text;
  if (fullLines > lineCap) {
    lineCut = allLines.slice(0, lineCap).join('\n');
  }
  let shown = lineCut;
  if (shown.length > charCap) {
    shown = shown.slice(0, charCap);
  }
  const truncated = shown.length < fullChars;
  return { shown, truncated, fullLines, fullChars };
}
