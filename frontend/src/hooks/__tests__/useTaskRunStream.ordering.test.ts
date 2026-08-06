/**
 * Tests for shouldAcceptFetchedRun — the monotonicity guard on the REST
 * snapshot that ``useTaskRunStream`` folds into state.
 *
 * Why the guard exists: ``ws.onmessage`` is an ``async`` handler that
 * awaits ``fetchOnce()``, and WebSocket message handlers are NOT
 * serialized — each invocation returns an unawaited promise.  Several
 * ``getTaskRun`` requests are therefore in flight at once during a busy
 * run, and ``setRun`` previously applied whichever RESOLVED last rather
 * than whichever was REQUESTED last.  A slow earlier GET could overwrite
 * a newer snapshot with an older one, so the run's ``updated_at`` (and
 * with it ``last_activity_at`` / ``progress_note`` / ``status``) could
 * move BACKWARDS.
 *
 * That regression is not merely cosmetic: TaskCardInlineTile's
 * live-progress line now picks between the WS stream and the REST
 * snapshot by comparing ``run.last_activity_at`` against
 * ``live.lastActivityTs``, which assumes the run side advances
 * monotonically.  A regressing snapshot flips that comparison back to a
 * stale value — reintroducing the frozen status line the comparison was
 * added to fix.
 *
 * The guard is a pure function so this can be proven without a
 * WebSocket, fake timers, or a React render.
 */

import { shouldAcceptFetchedRun } from '../useTaskRunStream';
import type { TaskRun } from '../../types/task_run';

/** A minimally-populated run; only the fields the guard reads matter. */
function makeRun(over: Partial<TaskRun> = {}): TaskRun {
  return {
    id: 'run-1',
    status: 'running',
    updated_at: 1000,
    ...over,
  } as TaskRun;
}

describe('shouldAcceptFetchedRun — staleness', () => {
  it('accepts the first snapshot when there is nothing in state yet', () => {
    expect(shouldAcceptFetchedRun(null, makeRun(), 'run-1')).toBe(true);
  });

  it('accepts a strictly newer snapshot', () => {
    expect(shouldAcceptFetchedRun(
      makeRun({ updated_at: 1000 }),
      makeRun({ updated_at: 2000 }),
      'run-1',
    )).toBe(true);
  });

  it('REJECTS an older snapshot — the out-of-order overwrite', () => {
    // The actual bug: a GET issued earlier resolves later and would
    // otherwise clobber the newer state already applied.
    expect(shouldAcceptFetchedRun(
      makeRun({ updated_at: 2000 }),
      makeRun({ updated_at: 1000 }),
      'run-1',
    )).toBe(false);
  });

  it('accepts an equal timestamp', () => {
    // updated_at has millisecond resolution and the backend writes it on
    // every mutation, so two writes can legitimately share a value.
    // Rejecting ties would discard a genuinely newer snapshot whose
    // content changed within the same millisecond.
    expect(shouldAcceptFetchedRun(
      makeRun({ updated_at: 1000 }),
      makeRun({ updated_at: 1000 }),
      'run-1',
    )).toBe(true);
  });

  it('treats a missing updated_at as oldest rather than newest', () => {
    // Coercing absent to 0 (not to Infinity/now) means a malformed or
    // pre-field record can never win against a real one.
    expect(shouldAcceptFetchedRun(
      makeRun({ updated_at: 5000 }),
      makeRun({ updated_at: undefined as unknown as number }),
      'run-1',
    )).toBe(false);
  });

  it('accepts a snapshot with no updated_at when state is also unstamped', () => {
    expect(shouldAcceptFetchedRun(
      makeRun({ updated_at: undefined as unknown as number }),
      makeRun({ updated_at: undefined as unknown as number }),
      'run-1',
    )).toBe(true);
  });
});

describe('shouldAcceptFetchedRun — identity', () => {
  it('rejects a response for a run the hook is no longer watching', () => {
    // The attempt rail switches runId mid-flight (selectAttempt), so a
    // GET for the previous attempt can land after the switch.  Accepting
    // it would show one attempt's data under another's heading.
    expect(shouldAcceptFetchedRun(
      makeRun(),
      makeRun({ id: 'run-OTHER', updated_at: 9999 }),
      'run-1',
    )).toBe(false);
  });

  it('accepts the new run when state still holds the previous attempt', () => {
    // Immediately after an attempt switch, prev is the OLD run and may
    // carry a much larger updated_at.  Timestamps are only comparable
    // within one run, so identity is checked before recency — otherwise
    // the new attempt could never load.
    expect(shouldAcceptFetchedRun(
      makeRun({ id: 'run-0', updated_at: 9999 }),
      makeRun({ id: 'run-1', updated_at: 1 }),
      'run-1',
    )).toBe(true);
  });

  it('does not enforce identity when no expected id is supplied', () => {
    // Defensive: the hook always passes runId, but the guard must not
    // reject everything if a caller omits it.
    expect(shouldAcceptFetchedRun(
      makeRun(),
      makeRun({ id: 'whatever', updated_at: 2000 }),
      undefined,
    )).toBe(true);
  });

  it('tolerates a response with no id at all', () => {
    expect(shouldAcceptFetchedRun(
      null,
      makeRun({ id: undefined as unknown as string }),
      'run-1',
    )).toBe(true);
  });
});

describe('shouldAcceptFetchedRun — terminal snapshots always win', () => {
  // A terminal status is the run's final word.  If it were subject to the
  // recency check, a lost race on the last write would leave the tile
  // spinning forever on a run that had already finished — the exact
  // "stuck running" failure the safety-net poll exists to heal.
  it.each(['done', 'partial', 'failed', 'cancelled'] as const)(
    'accepts a %s snapshot even when it is older', (status) => {
      expect(shouldAcceptFetchedRun(
        makeRun({ updated_at: 5000 }),
        makeRun({ updated_at: 1, status }),
        'run-1',
      )).toBe(true);
    });

  it('does not extend that exemption to paused, which is resumable', () => {
    expect(shouldAcceptFetchedRun(
      makeRun({ updated_at: 5000 }),
      makeRun({ updated_at: 1, status: 'paused' }),
      'run-1',
    )).toBe(false);
  });

  it('does not extend that exemption to held, which is continuable', () => {
    // 'held' is terminal for the run OBJECT but the work continues in a
    // NEW run, so an older held snapshot carries no authority over
    // fresher state for THIS run.
    expect(shouldAcceptFetchedRun(
      makeRun({ updated_at: 5000 }),
      makeRun({ updated_at: 1, status: 'held' }),
      'run-1',
    )).toBe(false);
  });

  it('still rejects a terminal snapshot for the WRONG run', () => {
    // Identity outranks the terminal exemption: a finished sibling
    // attempt must not terminate the tile watching a live one.
    expect(shouldAcceptFetchedRun(
      makeRun(),
      makeRun({ id: 'run-OTHER', status: 'done', updated_at: 9999 }),
      'run-1',
    )).toBe(false);
  });
});

describe('shouldAcceptFetchedRun — sequences', () => {
  it('converges on the newest snapshot regardless of arrival order', () => {
    // Simulate three concurrent GETs resolving out of order and confirm
    // the folded result is the newest, not the last to land.
    const snapshots = [
      makeRun({ updated_at: 3000 }),
      makeRun({ updated_at: 1000 }),
      makeRun({ updated_at: 2000 }),
    ];
    let state: TaskRun | null = null;
    for (const s of snapshots) {
      if (shouldAcceptFetchedRun(state, s, 'run-1')) state = s;
    }
    expect(state?.updated_at).toBe(3000);
  });

  it('never regresses across an arbitrary interleaving', () => {
    const order = [500, 4000, 100, 4000, 2500, 3999, 4001];
    let state: TaskRun | null = null;
    let lastSeen = -Infinity;
    for (const ts of order) {
      const next = makeRun({ updated_at: ts });
      if (shouldAcceptFetchedRun(state, next, 'run-1')) state = next;
      const now = state?.updated_at ?? 0;
      expect(now).toBeGreaterThanOrEqual(lastSeen);
      lastSeen = now;
    }
    expect(state?.updated_at).toBe(4001);
  });

  it('lets a terminal snapshot land after a newer non-terminal one', () => {
    let state: TaskRun | null = makeRun({ updated_at: 9000 });
    const done = makeRun({ updated_at: 8000, status: 'done' });
    if (shouldAcceptFetchedRun(state, done, 'run-1')) state = done;
    expect(state.status).toBe('done');
  });
});
