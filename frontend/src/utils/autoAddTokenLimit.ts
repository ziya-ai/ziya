/**
 * Per-file token limit for automatically added context files.
 *
 * Files the assistant auto-adds (e.g. files referenced by a generated diff)
 * are filtered through this limit so a single huge file cannot silently
 * blow out the conversation's token budget.  Manually selected files are
 * never filtered.
 */

/** Default per-file cap for auto-added files, in tokens. */
export const DEFAULT_AUTO_ADD_TOKEN_LIMIT = 35000;

export interface TokenLimitFilterResult {
  /** Paths that passed the limit (or whose size is unknown). */
  allowed: string[];
  /** Paths rejected for exceeding the limit, with their token counts. */
  skipped: Array<{ path: string; tokens: number }>;
}

/**
 * Split paths into allowed/skipped by a per-file token limit.
 *
 * - limit <= 0 (or non-finite) disables filtering: everything is allowed.
 * - Unknown sizes (getTokenCount returns 0, NaN, or negative) are allowed —
 *   a file we cannot measure is never blocked.
 * - A file exactly at the limit is allowed.
 */
export function filterByAutoAddTokenLimit(
  paths: string[],
  limit: number,
  getTokenCount: (path: string) => number,
): TokenLimitFilterResult {
  if (!Number.isFinite(limit) || limit <= 0) {
    return { allowed: [...paths], skipped: [] };
  }
  const allowed: string[] = [];
  const skipped: Array<{ path: string; tokens: number }> = [];
  for (const path of paths) {
    const tokens = getTokenCount(path);
    if (Number.isFinite(tokens) && tokens > limit) {
      skipped.push({ path, tokens });
    } else {
      allowed.push(path);
    }
  }
  return { allowed, skipped };
}
