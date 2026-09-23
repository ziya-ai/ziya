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

/**
 * What the auto-add filters know about one candidate's size.
 *
 *   number >= 0  measured; counts toward the limits (0 = empty or binary,
 *                which the prompt builder never sends, so it costs nothing)
 *   null         unmeasurable by design: tool-backed files (server -1), or a
 *                path the server cannot read as a file.  Allowed, costs 0.
 *   undefined    NOT MEASURED.  Held back; never allowed through.
 *
 * The old contract treated 0 as "unknown, never block".  Every defect that
 * produced a 0 — no tree node for a file under a .gitignore'd directory,
 * the accurate counter's 50k-token ceiling, an accurate count that had not
 * arrived yet — switched the limiter off for exactly the files it existed
 * to catch, and one session accumulated ~880k tokens of auto-adds while
 * the aggregate budget believed nothing had been spent.
 */
export type TokenMeasure = number | null | undefined;

export interface TokenLimitFilterResult {
  /** Paths that passed the limit (or are unmeasurable by design). */
  allowed: string[];
  /** Paths rejected for exceeding the limit, with their token counts. */
  skipped: Array<{ path: string; tokens: number }>;
  /** Paths held back because no measurement was available. */
  unmeasured: string[];
}

type Measured =
  | { kind: 'measured'; tokens: number }
  | { kind: 'unmeasurable' }
  | { kind: 'unmeasured' };

function classify(m: TokenMeasure): Measured {
  if (m === undefined || (typeof m === 'number' && Number.isNaN(m))) return { kind: 'unmeasured' };
  if (m === null || m < 0) return { kind: 'unmeasurable' };
  return { kind: 'measured', tokens: m };
}

/**
 * Split paths into allowed/skipped/unmeasured by a per-file token limit.
 *
 * - limit <= 0 (or non-finite) disables filtering: everything is allowed.
 * - A file exactly at the limit is allowed.
 * - See TokenMeasure for how null (allowed) and undefined (held) differ.
 */
export function filterByAutoAddTokenLimit(
  paths: string[],
  limit: number,
  getTokenCount: (path: string) => TokenMeasure,
): TokenLimitFilterResult {
  if (!Number.isFinite(limit) || limit <= 0) {
    return { allowed: [...paths], skipped: [], unmeasured: [] };
  }
  const allowed: string[] = [];
  const skipped: Array<{ path: string; tokens: number }> = [];
  const unmeasured: string[] = [];
  for (const path of paths) {
    const c = classify(getTokenCount(path));
    if (c.kind === 'unmeasured') {
      unmeasured.push(path);
    } else if (c.kind === 'measured' && c.tokens > limit) {
      skipped.push({ path, tokens: c.tokens });
    } else {
      allowed.push(path);
    }
  }
  return { allowed, skipped, unmeasured };
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
 * - Unmeasurable (null) paths are allowed and consume no budget; unmeasured
 *   (undefined) paths are held, matching the per-file filter.
 */
export function filterByAggregateAutoAddBudget(
  paths: string[],
  currentTotal: number,
  budget: number,
  getTokenCount: (path: string) => TokenMeasure,
): TokenLimitFilterResult {
  if (!Number.isFinite(budget) || budget <= 0) {
    return { allowed: [...paths], skipped: [], unmeasured: [] };
  }
  const allowed: string[] = [];
  const skipped: Array<{ path: string; tokens: number }> = [];
  const unmeasured: string[] = [];
  let running = currentTotal;
  for (const path of paths) {
    const c = classify(getTokenCount(path));
    if (c.kind === 'unmeasured') {
      unmeasured.push(path);
      continue;
    }
    if (c.kind === 'measured') {
      if (running + c.tokens > budget) {
        skipped.push({ path, tokens: c.tokens });
        continue;
      }
      running += c.tokens;
    }
    allowed.push(path);
  }
  return { allowed, skipped, unmeasured };
}

/** Default aggregate cap across all auto-added files, in tokens. */
export const DEFAULT_AUTO_ADD_AGGREGATE_BUDGET = 100000;

/** One entry of the accurate-count endpoint's `results` map. */
export interface AccurateCountResult {
  accurate_count?: number;
  error?: string;
  timestamp?: number;
}

export interface CachedCount { count: number; timestamp: number }

/**
 * Resolve a TokenMeasure for every candidate BEFORE the filters run.
 *
 * A cached accurate count is used when positive, or when -1 (tool-backed,
 * unmeasurable).  A cached 0 is re-measured: until recently the server
 * returned 0 for any file over 50k tokens and the UI stored it as valid.
 * Everything else goes to the accurate-count endpoint in ONE request — an
 * auto-add batch is a handful of files, not the tree, so waiting for it is
 * cheap and is the only way a file under a .gitignore'd directory (no tree
 * node) can be measured at all.  A per-path server error (not found,
 * outside the project) is unmeasurable: the prompt builder will not send
 * it either.  A failed request leaves the batch unmeasured, except where
 * the caller's fallback estimate is positive.
 *
 * Pure: the caller supplies the cache and the fetcher, and merges the
 * returned `fetched` counts into its own state.
 */
export async function measureForAutoAdd(
  paths: string[],
  cached: Record<string, CachedCount>,
  fetchAccurate: (paths: string[]) => Promise<Record<string, AccurateCountResult>>,
  fallback?: (path: string) => number,
): Promise<{ measure: (path: string) => TokenMeasure; fetched: Record<string, CachedCount> }> {
  const counts: Record<string, TokenMeasure> = {};
  const toFetch = new Set<string>();
  for (const p of paths) {
    const c = cached[p];
    if (c && c.count > 0) counts[p] = c.count;
    else if (c && c.count < 0) counts[p] = null;
    else toFetch.add(p);
  }

  const fetched: Record<string, CachedCount> = {};
  if (toFetch.size > 0) {
    const unique = Array.from(toFetch);
    let results: Record<string, AccurateCountResult> = {};
    let requestOk = true;
    try {
      results = (await fetchAccurate(unique)) || {};
    } catch {
      requestOk = false;
    }
    const now = Math.floor(Date.now() / 1000);
    for (const p of unique) {
      const r = requestOk ? results[p] : undefined;
      if (r) {
        if (r.error) { counts[p] = null; continue; }
        if (typeof r.accurate_count === 'number' && Number.isFinite(r.accurate_count)) {
          counts[p] = r.accurate_count < 0 ? null : r.accurate_count;
          fetched[p] = { count: r.accurate_count, timestamp: r.timestamp ?? now };
          continue;
        }
      }
      const fb = fallback ? fallback(p) : 0;
      counts[p] = Number.isFinite(fb) && fb > 0 ? fb : undefined;
    }
  }
  return { measure: (p) => counts[p], fetched };
}
