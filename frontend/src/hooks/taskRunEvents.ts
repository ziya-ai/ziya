/**
 * taskRunEvents — one signal for "a task run's status changed".
 *
 * Why it exists: the conversation list's gear cluster renders from
 * ``TaskBinding.run_status``, and those records are fetched once — on chat
 * open, or on a ``task-binding-created`` event.  Nothing re-read them when
 * a run's status subsequently MOVED, so a run that started ``running`` and
 * then stopped on an infrastructure fault kept the blue spinning gear
 * indefinitely.  The violet, non-animating ``held`` presentation already
 * existed in runStatusVocabulary and was simply never reached, because the
 * datum it switches on never changed on the client.
 *
 * That is worse than a cosmetic staleness: a spinning indicator is how a
 * user decides to keep waiting instead of intervening, which is exactly
 * the wrong decision for a run waiting on the environment being fixed.
 *
 * Deliberately a window event rather than a context value: the producer
 * (``useTaskRunStream``, mounted inside a tile deep in the chat tree) and
 * the consumer (``useTaskBindings``, mounted at the conversation level)
 * have no useful shared ancestor to thread a callback through, and the
 * same window-event pattern already carries TASK_BINDING_EVENT between
 * those two layers.
 */

export const TASK_RUN_STATUS_EVENT = 'task-run-status-changed';

export interface TaskRunStatusChangeDetail {
  runId: string;
  status: string;
  /** Absent when this is the first status observed for the run. */
  previous?: string;
}

/**
 * Suppression window for a repeat of the SAME (run, status).
 *
 * One transition is commonly observed by two surfaces at once — an inline
 * tile and the deck's inspector both watching the same run — and each
 * would otherwise cost a bindings refetch.  Deliberately time-bounded
 * rather than permanent: a run can legitimately re-enter a status it has
 * already held (pause → resume → pause on one run object), and a
 * permanent memo would swallow the second pause.
 */
const DEDUPE_MS = 1000;

/** `${runId}:${status}` -> epoch ms of last announcement. */
const lastAnnounced = new Map<string, number>();

function prune(now: number): void {
  for (const [key, at] of lastAnnounced) {
    if (now - at > DEDUPE_MS) lastAnnounced.delete(key);
  }
}

/**
 * Announce a run's current status.  Returns whether an event was actually
 * dispatched, which is what makes the suppression testable.
 */
export function notifyRunStatusChanged(
  runId: string, status: string, previous?: string,
): boolean {
  if (!runId || !status) return false;
  const now = Date.now();
  prune(now);
  const key = `${runId}:${status}`;
  const seen = lastAnnounced.get(key);
  if (seen !== undefined && now - seen <= DEDUPE_MS) return false;
  lastAnnounced.set(key, now);
  if (typeof window === 'undefined') return false;
  window.dispatchEvent(new CustomEvent<TaskRunStatusChangeDetail>(
    TASK_RUN_STATUS_EVENT, { detail: { runId, status, previous } },
  ));
  return true;
}

/** Test hook: forget every suppression entry. */
export function resetRunStatusNotifications(): void {
  lastAnnounced.clear();
}
