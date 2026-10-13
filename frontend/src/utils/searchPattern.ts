/**
 * Builds the regex used by conversation search and its highlighters.
 *
 * Mirrors app/storage/chat_search.compile_query: every whitespace run in the
 * query matches any whitespace run in the text, so a query typed with one
 * space finds a log line formatted with two ("⏱️  STALL") or a phrase that
 * wrapped across a newline. Everything else is literal.
 *
 * The server scan, the local IndexedDB fallback, the result-snippet
 * highlighter and the in-conversation highlighter all have to agree on what
 * "matches", otherwise a hit the server returns renders with no highlight.
 * Keep them on this one builder.
 */

const escapeRegExp = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/**
 * Returns the pattern for `query`, or null when the query is blank.
 *
 * @param flags Extra regex flags. 'g' is added for iteration; 'i' is added
 *   unless `caseSensitive`. Pass 'u' if the caller needs unicode mode.
 */
export function buildSearchPattern(
  query: string,
  opts: { caseSensitive?: boolean; global?: boolean } = {}
): RegExp | null {
  const tokens = query.split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return null;
  const body = tokens.map(escapeRegExp).join('\\s+');
  const flags = (opts.global === false ? '' : 'g') + (opts.caseSensitive ? '' : 'i');
  return new RegExp(body, flags);
}

/** Same pattern wrapped in a capture group, for String.split-based highlighting. */
export function buildSearchSplitPattern(query: string, caseSensitive = false): RegExp | null {
  const p = buildSearchPattern(query, { caseSensitive, global: false });
  return p ? new RegExp(`(${p.source})`, p.flags + 'g') : null;
}
