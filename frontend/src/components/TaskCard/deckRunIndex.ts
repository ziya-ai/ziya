/**
 * deckRunIndex — pure helpers behind the deck's per-card run surface.
 *
 * The deck previously reported run history as a bare `run_count` off the
 * card record, which answers "has this ever run?" and nothing else.  The
 * question a user actually opens the deck to ask is "is this still
 * going, and did the last one blow up?" — and that needs the runs
 * themselves.
 *
 * Extracted rather than inlined so the classification exists in exactly
 * one place.  A second copy of "which statuses are live" in the deck is
 * how the deck and the inline tile end up disagreeing about the same
 * run, and the disagreement is invisible until someone is watching both.
 */

import type { RunStatus, TaskRun } from '../../types/task_run';

/**
 * Statuses meaning "the executor has not finished with this run".
 *
 * 'paused' and 'queued' are live deliberately.  Keying on
 * `status === 'running'` alone — the obvious implementation — reports a
 * paused run as finished, which is exactly the state most in need of
 * being visible: it is stopped and waiting on the user.
 */
export const LIVE_STATUSES: ReadonlySet<RunStatus> =
  new Set<RunStatus>(['queued', 'running', 'paused']);

/**
 * Statuses meaning "a human should look at this".
 *
 * 'cancelled' is excluded: the user stopped it on purpose, and badging
 * it beside real failures teaches them to ignore the badge.  'held' is
 * included and is NOT a failure — the environment broke, the run is
 * resumable, and it is precisely the state that otherwise sits
 * unnoticed because nothing in the deck ever mentioned it.
 */
export const ATTENTION_STATUSES: ReadonlySet<RunStatus> =
  new Set<RunStatus>(['failed', 'partial', 'held']);

export function isLiveRun(status: RunStatus | string | null | undefined): boolean {
  return status != null && LIVE_STATUSES.has(status as RunStatus);
}

export function needsAttention(status: RunStatus | string | null | undefined): boolean {
  return status != null && ATTENTION_STATUSES.has(status as RunStatus);
}

/**
 * Ant Tag colour per status.
 *
 * 'held' shares violet with 'paused' rather than red with 'failed', for
 * the same reason the tile does: both mean "stopped, not broken", and a
 * red badge would have the deck assert a verdict on the work that the
 * status does not support.  An unknown status from a newer server falls
 * back to 'default' rather than throwing.
 */
export function deckStatusColor(status: RunStatus | string): string {
  switch (status) {
    case 'running': return 'blue';
    case 'queued': return 'cyan';
    case 'done': return 'green';
    case 'partial': return 'gold';
    case 'failed': return 'red';
    case 'cancelled': return 'orange';
    case 'paused': return 'purple';
    case 'held': return 'purple';
    default: return 'default';
  }
}

/**
 * Group a project's runs by owning card, newest first within each card.
 *
 * Sorted here rather than relying on the server's ordering so the deck's
 * "latest run" is a property of the data, not of response order — the
 * list endpoint does sort by `created_at`, but a caller that filters or
 * merges would silently change what "latest" means.
 *
 * `attempt` breaks a `created_at` tie: two attempts can land in the same
 * millisecond, and falling through to input order there would let the
 * displayed "latest" flicker between renders.
 */
export function indexRunsByCard(runs: TaskRun[]): Map<string, TaskRun[]> {
  const byCard = new Map<string, TaskRun[]>();
  for (const r of runs) {
    const arr = byCard.get(r.card_id);
    if (arr) arr.push(r); else byCard.set(r.card_id, [r]);
  }
  for (const arr of byCard.values()) {
    arr.sort((a, b) =>
      (b.created_at - a.created_at) || ((b.attempt ?? 1) - (a.attempt ?? 1)));
  }
  return byCard;
}

export interface CardRunSummary {
  total: number;
  /** Runs the executor has not finished with (see LIVE_STATUSES). */
  live: number;
  /** Runs a human should look at (see ATTENTION_STATUSES). */
  attention: number;
  /** Histogram, for tooltips that name what the counts are made of. */
  byStatus: Partial<Record<RunStatus, number>>;
  /** Newest run, or null when the card has never run. */
  latest: TaskRun | null;
}

const EMPTY_SUMMARY: CardRunSummary = {
  total: 0, live: 0, attention: 0, byStatus: {}, latest: null,
};

/**
 * Collapse one card's runs into the counts a deck row renders.
 *
 * `live` and `attention` are kept separate rather than reduced to a
 * single "state" because a card can legitimately be both at once — a
 * retry running while the failed attempt it came from is still on
 * record — and a combined badge would hide whichever of the two the
 * user was not looking for.
 *
 * `latest` is a max-fold rather than `runs[0]`, so the answer does not
 * depend on the caller having sorted first.
 */
export function summarizeCardRuns(
  runs: TaskRun[] | undefined | null,
): CardRunSummary {
  if (!runs || runs.length === 0) return { ...EMPTY_SUMMARY, byStatus: {} };
  let live = 0;
  let attention = 0;
  const byStatus: Partial<Record<RunStatus, number>> = {};
  let latest: TaskRun | null = null;
  for (const r of runs) {
    if (isLiveRun(r.status)) live += 1;
    if (needsAttention(r.status)) attention += 1;
    byStatus[r.status] = (byStatus[r.status] ?? 0) + 1;
    if (
      !latest
      || r.created_at > latest.created_at
      || (r.created_at === latest.created_at
          && (r.attempt ?? 1) > (latest.attempt ?? 1))
    ) {
      latest = r;
    }
  }
  return { total: runs.length, live, attention, byStatus, latest };
}

/**
 * True when any card in the index has a live run.
 *
 * Drives the deck's poll gate: an idle deck must issue no requests at
 * all, and a deck watching a running card must not need a manual
 * Refresh to notice it finished.
 */
export function hasLiveRuns(index: Map<string, TaskRun[]>): boolean {
  for (const runs of index.values()) {
    for (const r of runs) if (isLiveRun(r.status)) return true;
  }
  return false;
}
