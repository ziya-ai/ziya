/**
 * Quote xychart x-axis band labels the lexer cannot read bare.
 *
 * A bare band label is lexed as alphaNum tokens (letters, digits and
 * + = * # _ . & -), so a time label such as 05:54:15 fails at the first
 * colon: "Expecting ... got 'COLON'" (reported 2026-10-16).  Whitespace
 * between tokens is skipped, so "Q1 2024" would also lose its space.  Such
 * labels are wrapped in double quotes, which the grammar reads as STR.
 * Labels that are already quoted, or that lex cleanly, are left alone, so
 * every definition mermaid parses today is unchanged.
 *
 * Pure function (no DOM) so it can be unit-tested directly.
 */

// keyword, optional axis title, band contents
const X_AXIS_BAND = /^(\s*x-axis\b)([^[]*)\[(.*)\]\s*$/i;
const BARE_OK = /^[A-Za-z0-9+=*#_.&-]+$/;

function splitEntries(inner: string): string[] | null {
  const out: string[] = [];
  let cur = '';
  let inStr = false;
  for (const ch of inner) {
    if (ch === '"') {
      inStr = !inStr;
    } else if (ch === ',' && !inStr) {
      out.push(cur);
      cur = '';
      continue;
    }
    cur += ch;
  }
  if (inStr) return null; // unbalanced quotes: do not guess
  out.push(cur);
  return out;
}

function quoteEntry(entry: string): string {
  const t = entry.trim();
  if (!t || t.startsWith('"') || BARE_OK.test(t)) return entry;
  const lead = (entry.match(/^\s*/) || [''])[0];
  return `${lead}"${t.replace(/"/g, "'")}"`;
}

export function quoteXychartAxisLabels(definition: string): string {
  return definition
    .split('\n')
    .map(line => {
      const m = line.match(X_AXIS_BAND);
      if (!m) return line;
      const [, keyword, title, inner] = m;
      if ((title.match(/"/g) || []).length % 2 !== 0) return line;
      const entries = splitEntries(inner);
      if (!entries) return line;
      const fixed = entries.map(quoteEntry).join(',');
      return fixed === inner ? line : `${keyword}${title}[${fixed}]`;
    })
    .join('\n');
}
