/**
 * Per-file token limit for automatically added context files.
 *
 * Files the assistant auto-adds (e.g. files referenced by a generated diff)
 * are filtered through this limit so a single huge file cannot silently
 * blow out the conversation's token budget.  Manually selected files are
 * never filtered.
 */

/** Default per-file cap for auto-added files, in tokens. */
export const DEFAULT_AUTO_ADD_TOKEN_LIMIT = 12500;

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

/**
 * Aggregate token budget across ALL auto-added files, on top of the
 * per-file limit above.
 *
 * The per-file limit only stops one huge file from blowing the budget in
 * a single add; it does nothing to stop many individually-small files
 * from accumulating without bound over a long working session (a diff
 * referencing five files at 8k tokens each, six times over, silently
 * adds ~240k tokens with no ceiling).  This applies a running-total cap:
 * candidates are accepted greedily, in order, until the budget is spent.
 *
 * - budget <= 0 (or non-finite) disables the check: everything is allowed.
 * - currentTotal is the token sum of files already auto-added (the caller
 *   computes this from its own heritage tracking — this function is pure).
 * - Unknown sizes (0) are allowed and do not consume budget, matching the
 *   per-file filter's "never block what we can't measure" contract.
 */
export function filterByAggregateAutoAddBudget(
  paths: string[],
  currentTotal: number,
  budget: number,
  getTokenCount: (path: string) => number,
): TokenLimitFilterResult {
  if (!Number.isFinite(budget) || budget <= 0) {
    return { allowed: [...paths], skipped: [] };
  }
  const allowed: string[] = [];
  const skipped: Array<{ path: string; tokens: number }> = [];
  let running = currentTotal;
  for (const path of paths) {
    const tokens = getTokenCount(path);
    const measured = Number.isFinite(tokens) && tokens > 0;
    if (measured && running + tokens > budget) {
      skipped.push({ path, tokens });
      continue;
    }
    allowed.push(path);
    if (measured) running += tokens;
  }
  return { allowed, skipped };
}

/** Default aggregate cap across all auto-added files, in tokens. */
export const DEFAULT_AUTO_ADD_AGGREGATE_BUDGET = 100000;