/**
 * G-CHAT-RECOVERY / D-022 (chat-message-w4-12): recover a near-miss GFM table
 * whose delimiter (separator) row has FEWER columns than its header row.
 *
 * GFM — and marked, the renderer's lexer — require the delimiter row to have
 * exactly as many cells as the header row. When a model emits `--- | ---`
 * under a three-column header, the whole block is rejected as a table and
 * rendered as an ordinary paragraph; and because the shared chat renderer lexes
 * with CommonMark `breaks:false`, the single newlines then collapse every row
 * into one run-on "pipe soup" line (the compounding D-012 failure). Padding the
 * delimiter row up to the header's column count is enough for marked to
 * recognise the table again — data rows that are themselves short are tolerated
 * by GFM and padded with empty cells.
 *
 * The transform is deliberately narrow so it never rewrites unrelated content:
 *
 *   - the header line must contain a `|` and split into at least two cells;
 *   - the immediately following line must be a PURE delimiter row — only the
 *     characters `-`, `:`, `|` and spaces, with at least one `-` — AND must
 *     itself contain a `|`, so a setext heading underline (`---`, which has no
 *     pipe) is never mistaken for a table delimiter;
 *   - it fires ONLY when the delimiter has FEWER cells than the header, and it
 *     only APPENDS `---` cells. A delimiter that already matches (or exceeds)
 *     the header, and every non-table line, is returned byte-for-byte
 *     unchanged, so the pass is idempotent and cannot corrupt a valid table.
 *
 * Cell counting mirrors marked's own row splitting (strip a single optional
 * leading/trailing pipe, then split on unescaped pipes) so the header and
 * delimiter counts are compared on the same basis the lexer uses.
 */

/** Count the cells of a table row the way marked's `splitCells` does. */
function countRowCells(line: string): number {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|')) s = s.replace(/\|$/, '');
  // Split on pipes that are not backslash-escaped.
  return s.split(/(?<!\\)\|/).length;
}

/** True when `line` is a pure GFM delimiter row that carries a pipe. */
function isDelimiterRow(line: string): boolean {
  const s = line.trim();
  if (!s.includes('|')) return false; // a setext `---` underline has no pipe
  if (!s.includes('-')) return false;
  return /^[\s:|-]+$/.test(s);
}

/** True when `line` could be a table header (has a pipe, splits into >= 2). */
function looksLikeHeaderRow(line: string): boolean {
  if (!line.includes('|')) return false;
  if (isDelimiterRow(line)) return false; // a delimiter is not a header
  return countRowCells(line) >= 2;
}

/**
 * Pad a short delimiter row up to `target` cells, preserving its leading
 * indentation and an existing trailing pipe.
 */
function padDelimiterRow(line: string, target: number): string {
  const lead = (line.match(/^\s*/) || [''])[0];
  let body = line.trim();
  const current = countRowCells(body);
  const missing = target - current;
  if (missing <= 0) return line;
  const hadTrailingPipe = body.endsWith('|');
  if (hadTrailingPipe) body = body.replace(/\|\s*$/, '').trimEnd();
  for (let k = 0; k < missing; k++) body += ' | ---';
  if (hadTrailingPipe) body += ' |';
  return lead + body;
}

/**
 * Repair every near-miss GFM table in `markdown` whose delimiter row is one or
 * more columns short of its header row. Returns the input unchanged when there
 * is nothing to repair.
 */
export function repairNearMissTableSeparator(markdown: string): string {
  if (!markdown || markdown.indexOf('|') === -1 || markdown.indexOf('-') === -1) {
    return markdown;
  }
  const lines = markdown.split('\n');
  let changed = false;
  for (let i = 0; i + 1 < lines.length; i++) {
    const header = lines[i];
    const delim = lines[i + 1];
    if (!looksLikeHeaderRow(header)) continue;
    if (!isDelimiterRow(delim)) continue;
    const headerCells = countRowCells(header);
    const delimCells = countRowCells(delim);
    if (delimCells >= headerCells) continue; // already valid (or longer): leave alone
    lines[i + 1] = padDelimiterRow(delim, headerCells);
    changed = true;
    i++; // skip the delimiter we just fixed
  }
  return changed ? lines.join('\n') : markdown;
}
