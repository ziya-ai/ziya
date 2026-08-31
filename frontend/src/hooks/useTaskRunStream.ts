/**
 * useTaskRunStream — subscribe to a task run's live event stream.
 *
 * Design (see design/task-cards.md §Live observation):
 *   1. Fetch initial snapshot via GET /task-runs/{id}         (source of truth)
 *   2. If non-terminal, open WS /ws/task-runs/{id}             (event stream)
 *   3. On each event, update local state and refetch if needed
 *   4. On run_completed, one final GET to pull the artifact
 *      (which events don't embed) then close the WS
 *
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import type { TaskRun } from '../types/task_run';
import { getTaskRun } from '../services/taskRunApi';
import { notifyRunStatusChanged } from './taskRunEvents';
import {
  emptyLagStats, foldSample, formatLagStats, isLagProbeEnabled, LAG_LOG_EVERY,
} from './streamLagProbe';

// The run's FINAL word: nothing can supersede one of these for this run
// object.  Governs the recency exemption in shouldAcceptFetchedRun and
// nothing else.  Must include 'partial', or a run that finished partway is
// treated as still supersedable by an older snapshot.
//
// Deliberately does NOT govern transport teardown — ``EXECUTOR_STOPPED``
// below does, and is wider.  Conflating the two is what kept a held run's
// socket and poll alive; see that list's docstring.
const TERMINAL: ReadonlyArray<TaskRun['status']> =
  ['done', 'partial', 'failed', 'cancelled'];

/**
 * Statuses for which the EXECUTOR has unwound — i.e. nothing can still
 * be streaming.  Deliberately WIDER than ``TERMINAL`` above: 'held'
 * belongs here (an infra fault unwound the coroutine, so no iteration
 * can still be running) but must NOT join ``TERMINAL``, because that
 * list also governs the recency exemption in shouldAcceptFetchedRun,
 * where a held snapshot legitimately carries no authority — the work
 * continues as a NEW run.  See useTaskRunStream.ordering.test.ts.
 *
 * Two lists rather than one because they answer different questions:
 * "should we stop polling / trust this snapshot?" vs "can anything
 * still be producing output?".
 *
 * TRANSPORT TEARDOWN READS THIS LIST.  Both the socket gate and the
 * reconcile effect previously asked ``TERMINAL``, so a held run kept an
 * open WebSocket and a 15 s REST poll for as long as its tile stayed
 * mounted.  Nothing could ever arrive on either: the server writes 'held'
 * only after execute_block has raised and the fan-out has been gathered,
 * the relay endpoint never closes from its own side, and a resume mints a
 * NEW run id rather than reviving this one.
 *
 * 'paused' is absent on purpose and must stay absent: it resumes IN PLACE
 * (block_executor._wait_if_paused writes 'running' back onto the same
 * record), so its stream is genuinely coming back.
 */
const EXECUTOR_STOPPED: ReadonlyArray<TaskRun['status']> =
  ['done', 'partial', 'failed', 'cancelled', 'held'];

/**
* In-flight observability for a task run.  Accumulated
 * locally from the WS event stream and reset on runId change or via
 * the returned ``clearLive`` callback.  Authoritative run state still
 * comes from the REST snapshot in ``run``.
 */
export interface LiveTaskState {
  /** Accumulated streaming text per task block, keyed by block_id. */
  text: Record<string, string>;
  /** Tool invocations as they are emitted (most recent at the end). */
  toolCalls: Array<{
    block_id?: string;
    tool_name?: string;
    tool_id?: string;
    result_preview?: string;
    ts?: number;
  }>;
  /** Raw event timeline (lifecycle + task_*).  Bounded to MAX_EVENTS. */
  events: Array<{ type: string; ts?: number; [k: string]: unknown }>;
  /**
   * Per-iteration buckets so the inspector can render iteration
   * delimiters in Live / Tools / Events tabs.  An iteration opens
   * on ``iteration_started`` (or lazily on the first event for a
   * block that has none yet — covers simple non-repeat task blocks)
   * and seals on ``iteration_completed``.  Run-scoped events
   * (``run_started``, ``run_completed``) stay on the flat ``events``
   * timeline and are not bucketed.
   *
   * Flat ``text`` / ``toolCalls`` / ``events`` are preserved for
   * backward compatibility — existing inspector code that doesn't
   * know about iterations keeps working.
   */
  iterations: Array<{
    index: number;          // 0-based, monotonic within a block
    blockId?: string;
    streamText: string;
    toolCalls: LiveTaskState['toolCalls'];
    events: LiveTaskState['events'];
    status: 'running' | 'passed' | 'failed';
    durationMs?: number;
    tokens?: number;
    signature?: string;
  }>;
  /**
   * Resolved run-scoped variables surfaced by State blocks via the
   * ``state_applied`` event (values after launch-override merge).
   * Last-write-wins across events so the panel shows the current set.
   * Names-only — prose context is ambient and intentionally NOT here.
   */
  variables: Record<string, unknown>;
  /**
   * Live per-block lifecycle statuses from ``block_status`` events,
   * keyed by block_id: queued / running / done / failed / cancelled /
   * skipped.  Freshest source for the run map; the REST snapshot's
   * block_states carries the durable record across reloads.
   */
  blockStatuses: Record<string, string>;
  /**
   * Latest server-derived progress note (``task_progress`` event) —
   * e.g. "ran run_shell_command: git status".  Freshest source; the
   * tile falls back to run.progress_note from REST when absent.
   *
   * B8a fix: this now holds the latest note of EITHER kind (model or
   * tool-derived), matching the pre-fix behavior for callers that don't
   * care about the distinction.  ``modelProgressNote`` below holds only
   * the model-authored kind, which the tile prefers as its headline —
   * without that split, a rich model note like "reviewed 12/30 diffs;
   * grouping into 3 commits" was overwritten by the very next tool call
   * (usually 1-2 seconds later) with a generic "ran grep: ...", even
   * though the two events carry a ``source`` field that already
   * distinguishes them.
   */
  progressNote?: string;
  /** Latest note where ``source === 'model'`` — see progressNote above. */
  modelProgressNote?: string;
  /** Epoch seconds of the most recent event seen on this stream. */
  lastActivityTs?: number;
}

/**
 * Event types that indicate a TASK is actually executing, and so may
 * lazily open an iteration bucket for a block that has none yet.
 *
 * The auto-open used to fire for ANY block-scoped event, which meant a
 * container's own lifecycle (``block_status`` for a Repeat, Group,
 * Parallel, Until…) minted a phantom "Iteration 0" bucket for a block
 * that never runs a task directly.  Those buckets are empty by
 * construction — a container emits no text and no tool calls — so they
 * survived only because the inspector filters empty ones out of the Live
 * and Tools tabs.  The Events tab does not filter, and the data was
 * wrong regardless: ``live.iterations`` is the iteration model, and a
 * container is not an iteration.
 *
 * Restricting the auto-open to task-scoped traffic keeps the lazy path
 * doing what it was for — covering a bare Task block that streams
 * without emitting ``iteration_started`` — while a container's status
 * transitions stay where they belong, on the flat event timeline and in
 * ``blockStatuses``.
 */
const TASK_SCOPED_EVENTS: ReadonlySet<string> = new Set([
  'task_started', 'task_text_delta', 'task_text_delta_run',
  'task_tool_call', 'task_progress', 'task_finished',
]);

const EMPTY_LIVE: LiveTaskState = { text: {}, toolCalls: [], events: [], iterations: [], variables: {}, blockStatuses: {} };

/**
 * Seal every still-'running' iteration bucket.
 *
 * Once a run is over, NOTHING can still be executing, so a bucket left
 * at 'running' is a dangling artifact of a lost event — and it is what
 * leaves the Live tab showing a spinner and a streaming cursor under a
 * finished run, with no cue that the work has moved on.
 *
 * Returns the SAME array reference when there is nothing to seal, so a
 * caller can use identity to skip a pointless state update.
 *
 * 'passed' is the neutral seal: a genuinely failed iteration already
 * sealed itself via task_finished(ok=false) / iteration_completed, so
 * this only catches buckets nothing else closed.
 */
export function sealRunningIterations(
  iterations: LiveTaskState['iterations'],
): LiveTaskState['iterations'] {
  if (!iterations.some(it => it.status === 'running')) return iterations;
  return iterations.map(it =>
    it.status === 'running' ? { ...it, status: 'passed' as const } : it);
}

/**
 * Fold a terminal run status into live state, sealing dangling buckets.
 *
 * Exists because the seal previously lived ONLY in the ``run_completed``
 * branch of accumulateLive — i.e. it required the WS event to arrive.
 * When the terminal state is instead discovered by the safety-net REST
 * poll (exactly the dropped-event case the poll was added to heal, and
 * the case that follows any socket drop), no seal ever ran and the Live
 * tab kept a spinner going indefinitely on a run that had finished.
 */
export function sealLiveForTerminal(prev: LiveTaskState): LiveTaskState {
  const iterations = sealRunningIterations(prev.iterations);
  return iterations === prev.iterations ? prev : { ...prev, iterations };
}
const MAX_EVENTS = 500;       // hard cap so a long run can't unbound memory
const MAX_TOOL_CALLS = 200;
const POLL_INTERVAL_MS = 15000; // safety-net REST poll cadence while a WS is open
// Reconnect backoff for an unexpectedly-dropped (non-terminal) socket.
// Capped low: the relay's history buffer makes reconnect cheap, so there
// is no reason to back off as aggressively as a typical retry policy.
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 10000;
/**
 * Action returned by dispatchTaskRunEvent — what the stream loop
 * should do in response to an incoming event.  Extracted as a pure
 * function so the dispatch logic is unit-testable without a real
 * WebSocket or React render.
 */
export type TaskRunStreamAction =
  | { kind: 'refetch' }
  | { kind: 'refetch-and-close' }
  | { kind: 'ignore' };

export function dispatchTaskRunEvent(
  evt: unknown,
): TaskRunStreamAction {
  if (!evt || typeof evt !== 'object') return { kind: 'ignore' };
  const type = (evt as { type?: unknown }).type;
  if (typeof type !== 'string') return { kind: 'ignore' };
  switch (type) {
    case 'run_started':
    case 'run_paused':
    case 'run_resumed':
    // Emitted instead of run_resumed when a step credit is spent: the
    // run crossed a boundary but is still held.  Distinct on purpose,
    // and it must refetch — step_budget and the crossed block's status
    // both changed on disk.
    case 'run_stepped':
    case 'iteration_completed':
    case 'block_completed':
      // State has changed — persisted snapshot is the source of
      // truth, so refetch rather than mutate locally.
      return { kind: 'refetch' };
    case 'run_completed':
      // Terminal: the artifact is only available via REST.  Refetch
      // and close the WS — server will disconnect too.
      return { kind: 'refetch-and-close' };
    case 'block_started':
      // A Repeat(for_each) persists its resolved roster size just
      // before emitting this event, so a planned-carrying start IS a
      // persisted-state change: refetch so the run map can show "0/m"
      // before the first iteration completes.  A plain block_started
      // still has nothing persisted to display.
      return typeof (evt as { planned?: unknown }).planned === 'number'
        ? { kind: 'refetch' }
        : { kind: 'ignore' };
    case 'iteration_started':
    case 'whisper_received':
      // No new persisted state to display; swallow.
      return { kind: 'ignore' };
    default:
      return { kind: 'ignore' };
  }
}

/**
 * Decide whether a freshly-fetched snapshot may replace the one in state.
 *
 * ``ws.onmessage`` is an ``async`` handler that awaits ``fetchOnce()``, and
 * WebSocket message handlers are NOT serialized — each invocation returns an
 * unawaited promise.  Several ``getTaskRun`` requests are therefore in flight
 * at once during a busy run, and ``setRun`` would otherwise apply whichever
 * RESOLVED last rather than whichever was REQUESTED last.  A slow earlier GET
 * could then overwrite a newer snapshot with an older one, letting
 * ``updated_at`` — and with it ``status``, ``progress_note`` and
 * ``last_activity_at`` — run backwards.
 *
 * That regression is not merely cosmetic.  TaskCardInlineTile's live-progress
 * line chooses between the WS stream and the REST snapshot by comparing
 * ``run.last_activity_at`` against ``live.lastActivityTs``, which assumes the
 * run side advances monotonically; a regressing snapshot flips that
 * comparison back to a stale value and reintroduces the frozen status line
 * the comparison was added to fix.
 *
 * Pure so the ordering rules are testable without a WebSocket, fake timers,
 * or a React render.
 */
export function shouldAcceptFetchedRun(
  prev: TaskRun | null,
  next: TaskRun,
  expectedRunId: string | undefined,
): boolean {
  // Identity first, and deliberately BEFORE recency: timestamps are only
  // comparable within a single run.  The attempt rail can switch runId while
  // a GET is in flight, so a response for the run we are no longer watching
  // must be discarded outright — accepting it would render one attempt's
  // data under another's heading.
  if (expectedRunId && next.id && next.id !== expectedRunId) return false;
  if (!prev) return true;
  // State still holds the PREVIOUS attempt (selectAttempt just switched).
  // That run may carry a much larger ``updated_at``, so a recency-first
  // ordering would make the new attempt permanently unloadable.
  if (prev.id !== next.id) return true;
  // A terminal status is the run's final word.  Subjecting it to the recency
  // check would let a lost race on the last write leave the tile spinning
  // forever on a run that had already finished — the exact "stuck running"
  // failure the safety-net poll exists to heal.  ``paused`` and ``held`` are
  // excluded by construction: both are non-final for THIS run (held
  // continues as a NEW run), so an older one carries no such authority.
  if (TERMINAL.includes(next.status)) return true;
  // Absent coerces to 0, never to now: a malformed or pre-field record must
  // not be able to win against a real one.  Ties are ACCEPTED —
  // ``updated_at`` has millisecond resolution and the backend stamps it on
  // every mutation, so two writes can legitimately share a value and
  // rejecting ties would discard genuinely newer content.
  const prevTs = prev.updated_at ?? 0;
  const nextTs = next.updated_at ?? 0;
  return nextTs >= prevTs;
}

export interface UseTaskRunStreamResult {
  run: TaskRun | null;
  error: string | null;
  /** True while the initial REST fetch is pending. */
  loading: boolean;
  /** Live accumulated state from the WS event stream. */
  live: LiveTaskState;
  /** Reset all live buffers to empty.  Does not affect ``run``. */
  clearLive: () => void;
  /** Force a REST re-fetch (e.g. after a user action like cancel). */
  refresh: () => Promise<void>;
}

export function useTaskRunStream(
  projectId: string | undefined,
  runId: string | undefined,
): UseTaskRunStreamResult {
  const [run, setRun] = useState<TaskRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [live, setLive] = useState<LiveTaskState>(EMPTY_LIVE);
  const wsRef = useRef<WebSocket | null>(null);
  const mountedRef = useRef<boolean>(true);
  const terminalFetchedRef = useRef<boolean>(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Pending reconnect timer (B2).  A dropped-but-not-terminal socket must
  // not leave the client silently degraded to the 15s REST poll for the
  // rest of the run — reconnecting is cheap because the relay's history
  // buffer lets a fresh connection replay everything it missed.
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptRef = useRef<number>(0);
  // Diagnostic receipt-lag accounting.  A ref (not state) so the probe
  // can never itself cause the re-render storm it exists to measure.
  // Inert unless localStorage['ziya.debug.taskRunLag'] === '1'.
  const lagRef = useRef(emptyLagStats());
  // Last status this hook announced, per runId.  Scoped to the run so
  // switching attempts cannot make the new run's first snapshot look like
  // a transition out of the previous run's terminal status.
  const announcedRef = useRef<{ runId?: string; status?: string }>({});


  const fetchOnce = useCallback(async () => {
    if (!projectId || !runId) return;
    try {
      const r = await getTaskRun(projectId, runId);
      // Guarded rather than unconditional: concurrent in-flight GETs resolve
      // in completion order, so an unguarded setRun can regress the snapshot.
      // Applied inside the updater so the comparison reads the CURRENT state
      // — reading ``run`` from the closure would compare against whatever was
      // current when this callback was created, which is the same race again.
      if (mountedRef.current) {
        setRun(prev => (shouldAcceptFetchedRun(prev, r, runId) ? r : prev));
      }
    } catch (e) {
      if (mountedRef.current) setError(String(e));
    }
  }, [projectId, runId]);

  const clearLive = useCallback(() => setLive(EMPTY_LIVE), []);

  /**
   * Publish the run's status to the rest of the app.
   *
   * The conversation list's gear reads ``TaskBinding.run_status``, which is
   * fetched once per chat open; without this the row kept the status the
   * run had at that moment — most damagingly a blue spinning "running"
   * gear for a run that had since been held on an infrastructure fault,
   * while the tile two pixels away showed the hold correctly from this
   * very snapshot.
   *
   * The FIRST observation is announced too, not only transitions.  A hold
   * can land within milliseconds of launch (the launch preflight mints a
   * held run outright), so the bindings fetch triggered by the launch can
   * read ``queued`` and the tile's first snapshot already be ``held`` —
   * a transition-only signal never fires and the row stays wrong.  The
   * cost of announcing mounts is bounded by the dedupe in taskRunEvents
   * plus the consumer's coalescing, not by the number of tiles.
   */
  useEffect(() => {
    if (!runId || !run || !run.status) return;
    // Same stale-object guard the socket effect uses below: inside the
    // commit that changes runId, ``run`` may still be the previous run.
    if (run.id && run.id !== runId) return;
    const prev = announcedRef.current;
    announcedRef.current = { runId, status: run.status };
    if (prev.runId === runId && prev.status === run.status) return;
    notifyRunStatusChanged(
      runId, run.status, prev.runId === runId ? prev.status : undefined,
    );
  }, [runId, run]);

  useEffect(() => {
    // Reset ``run`` to null on every runId change, BEFORE the fetch below
    // lands.  Without this, switching attempts (the rail, resume-from)
    // leaves ``run`` holding the PREVIOUS (often terminal) run's object
    // while ``runId`` has already moved to the new one.  The WS-gating
    // effect below re-runs on that same tick, sees a non-null ``run``
    // whose status is terminal, and returns early — permanently, since
    // its deps ([projectId, runId, run != null, fetchOnce]) don't change
    // again once the new run's snapshot lands.  The tile then shows a
    // frozen view of the new attempt until a full remount.
    setRun(null);
    mountedRef.current = true;
    terminalFetchedRef.current = false;
    lagRef.current = emptyLagStats();
    setLive(EMPTY_LIVE);              // reset live buffers per runId
    if (!projectId || !runId) return () => { mountedRef.current = false; };
    setLoading(true);
    setError(null);
    (async () => {
      await fetchOnce();
      if (mountedRef.current) setLoading(false);
    })();
    return () => { mountedRef.current = false; };
  }, [projectId, runId, fetchOnce]);

  useEffect(() => {
    if (!projectId || !runId || !run) return;
    // Only act on the run we are actually watching.  ``setRun(null)`` in
    // the reset effect above is batched, so within the SAME commit as a
    // runId change this effect still closes over the PREVIOUS run object
    // while ``runId`` has already advanced — it would otherwise open a
    // socket for the new id while deciding liveness from the old run's
    // status.  That is the same stale-object confusion B1 is about, one
    // commit earlier, and it also spawned a transient socket that was
    // closed again microseconds later.
    if (run.id && run.id !== runId) return;
    // EXECUTOR_STOPPED, not TERMINAL: a run already 'held' when first
    // observed — reopening a chat that holds one, or the launch preflight
    // minting one outright — has nothing left to stream and no
    // ``run_completed`` still to come, since it ended before this client
    // connected.  Gating on TERMINAL opened a socket and armed a 15 s poll
    // for it, per mounted tile, for as long as the chat stayed open.
    //
    // This gate is evaluated on runId change only, never on a status
    // transition (see the deps note at the end of this effect), so it
    // covers the already-stopped case alone; a run that stops WHILE being
    // watched is torn down by the reconcile effect below.
    if (EXECUTOR_STOPPED.includes(run.status)) return;

    // Safety-net poll: the terminal run_completed WS event can be lost
    // (dropped relay frame, dead-but-open socket, backgrounded tab),
    // leaving the tile stuck "running" indefinitely even though the
    // backend already wrote a terminal status.  Poll the REST snapshot
    // periodically; the reconcile effect below closes everything once
    // it observes a terminal status, whatever the source.
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(() => {
      if (mountedRef.current) fetchOnce();
    }, POLL_INTERVAL_MS);

    let disposed = false;

    const openSocket = () => {
      if (disposed || !mountedRef.current) return;
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const url = `${protocol}//${window.location.host}/ws/task-runs/${encodeURIComponent(runId)}`;
      let ws: WebSocket;
      try {
        ws = new WebSocket(url);
      } catch (e) {
        console.warn('useTaskRunStream: WebSocket ctor failed:', e);
        scheduleReconnect();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        // A successful connection proves the transport is healthy again;
        // don't let a prior failure's backoff linger into the next drop.
        reconnectAttemptRef.current = 0;
      };

      ws.onmessage = async (evt) => {
      if (!mountedRef.current) return;
      let data: unknown;
      try { data = JSON.parse(evt.data); } catch { return; }
      // Receipt-lag probe.  Measured BEFORE accumulateLive so the sample
      // reflects transport + queue delay, not our own render cost.  If
      // lag grows monotonically the stream is backlogged (batching is
      // the fix); if it stays near zero while events are sparse, frames
      // are being dropped instead (a transport problem).
      if (isLagProbeEnabled()) {
        lagRef.current = foldSample(lagRef.current, data);
        if (lagRef.current.total % LAG_LOG_EVERY === 0) {
          console.log(
            `📡 TASK_RUN_LAG[${(runId ?? '').slice(0, 8)}] `
            + formatLagStats(lagRef.current),
          );
        }
      }
      // Accumulate live observability *before* dispatch — even
      // ignored events (block_started, whisper_received, task_*)
      // populate the timeline.
      accumulateLive(setLive, data);
      const action = dispatchTaskRunEvent(data);
      if (action.kind === 'refetch') {
        await fetchOnce();
      } else if (action.kind === 'refetch-and-close') {
        if (!terminalFetchedRef.current) {
          terminalFetchedRef.current = true;
          await fetchOnce();
        }
        try { ws.close(); } catch { /* ignore */ }
      }
      };

      ws.onerror = () => {
        // onclose handles cleanup and reconnect scheduling below; REST
        // fetch already gave the caller a state snapshot in the meantime.
      };

      ws.onclose = () => {
        if (wsRef.current === ws) wsRef.current = null;
        if (disposed || !mountedRef.current || terminalFetchedRef.current) return;
        // Drop without a terminal event → fetch once so state reflects
        // whatever the server settled on, THEN try to reconnect.  A
        // dropped-but-not-terminal socket (relay hiccup, idle-timeout
        // proxy, laptop sleep/wake) previously left the client silently
        // degraded to the 15s REST poll for the remainder of the run —
        // no more streaming text, tool calls, or progress notes, with
        // nothing on screen indicating anything had changed.  Since the
        // relay keeps a bounded replay buffer per run, reconnecting is
        // cheap and recovers the live buffers as if nothing happened.
        fetchOnce();
        scheduleReconnect();
      };
    };

    const scheduleReconnect = () => {
      if (disposed || !mountedRef.current) return;
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      const attempt = reconnectAttemptRef.current + 1;
      reconnectAttemptRef.current = attempt;
      const delay = Math.min(RECONNECT_BASE_MS * attempt, RECONNECT_MAX_MS);
      reconnectRef.current = setTimeout(() => {
        reconnectRef.current = null;
        openSocket();
      }, delay);
    };

    reconnectAttemptRef.current = 0;
    openSocket();

    return () => {
      disposed = true;
      if (reconnectRef.current) { clearTimeout(reconnectRef.current); reconnectRef.current = null; }
      if (wsRef.current) {
        try { wsRef.current.close(); } catch { /* ignore */ }
        // Cleared here rather than compared against a captured ``ws``:
        // the socket variable is now scoped inside ``openSocket``, so a
        // reconnect replaces wsRef.current and the effect-level cleanup
        // has no single socket to identify.  ``disposed`` already stops
        // onclose from scheduling another reconnect.
        wsRef.current = null;
      }
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
    // Intentional: run?.status would cause teardown on every status
    // transition.  We only (re)open when runId changes or we learn
    // of a non-terminal run for the first time.  Terminal-state
    // reopening is prevented by the early-return above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, runId, run != null, fetchOnce]);

  // Terminal reconciliation: whenever ``run`` reaches a terminal status
  // — via WS event refetch, safety-net poll, or onclose refetch — stop
  // polling and close the socket.  This decouples "run is terminal"
  // from "we received the run_completed event", so a dropped terminal
  // event still self-heals within one poll interval instead of leaving
  // the tile stuck "running" forever.
  useEffect(() => {
    if (!run) return;
    // ONE condition — EXECUTOR_STOPPED — for both sealing and teardown.
    // These were two blocks, sealing on EXECUTOR_STOPPED but tearing down
    // on TERMINAL, which asserted two incompatible things about a held
    // run: that nothing could still be executing (so seal the Live
    // buckets) and that something might still arrive (so keep the socket
    // and the 15 s poll).  The first is the correct one, so what the
    // second bought was an idle socket and a poll re-reading a record
    // only a human can change, every 15 s, per mounted tile.
    //
    // Done here rather than only in the ``run_completed`` event branch
    // because this effect is the one place that observes the run ending
    // however that was learned — WS event, safety-net poll, or the fetch
    // after a socket drop.  Without it the cases the poll exists to heal
    // (lost terminal frame, dead-but-open socket) left the Live tab
    // spinning on a finished run; and because that event branch closes
    // the socket but never the poll, for a held run learned that way this
    // is the only thing that stops the poll at all.
    if (EXECUTOR_STOPPED.includes(run.status)) {
      setLive(sealLiveForTerminal);
      // Load-bearing, not bookkeeping: ``ws.onclose`` schedules a
      // reconnect unless this is set, so closing the socket below
      // without it would trap a held run in a 1s→10s reconnect loop —
      // strictly worse than the idle socket it replaces.
      terminalFetchedRef.current = true;
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      // A reconnect may already be scheduled from a drop that happened
      // moments before this terminal snapshot arrived; without clearing
      // it, the timer would fire and open a socket for a run that is
      // already done.
      if (reconnectRef.current) { clearTimeout(reconnectRef.current); reconnectRef.current = null; }
      if (wsRef.current) {
        try { wsRef.current.close(); } catch { /* ignore */ }
        wsRef.current = null;
      }
    }
  }, [run?.status]);

  return { run, error, loading, live, clearLive, refresh: fetchOnce };
}

/**
 * Fold a single inbound event into LiveTaskState.  Pure with respect
 * to its ``setLive`` arg (no module-level state) so the helper is
 * testable without React.  Unknown event shapes are stored in the
 * timeline but otherwise ignored.
 */
 export function accumulateLive(
  setLive: React.Dispatch<React.SetStateAction<LiveTaskState>>,
  evt: unknown,
): void {
  if (!evt || typeof evt !== 'object') return;
  const e = evt as { type?: unknown; [k: string]: unknown };
  const type = typeof e.type === 'string' ? e.type : null;
  if (!type) return;

  setLive(prev => {
    // Always record on the timeline (bounded).
    const events = prev.events.length >= MAX_EVENTS
      ? [...prev.events.slice(prev.events.length - MAX_EVENTS + 1), e as { type: string; ts?: number; [k: string]: unknown }]
      : [...prev.events, e as { type: string; ts?: number; [k: string]: unknown }];
    let text = prev.text;
    let toolCalls = prev.toolCalls;
     let iterations = prev.iterations;

     // ── Iteration bucketing ──────────────────────────────────────
     // Run-scoped events (no block_id) never go into a bucket.  Block-
     // scoped events route to the current iteration of that block;
     // iterations open on iteration_started and seal on
     // iteration_completed.  Lazy auto-open: the first block-scoped
     // event for a block that has no iteration yet creates index 0,
     // covering non-repeat task blocks that don't emit started/done.
     const blockId = typeof e.block_id === 'string' ? e.block_id : undefined;
     const isRunScope = !blockId && (type === 'run_started' || type === 'run_completed');

    // Terminal backstop: once the run completes/fails/cancels, NOTHING
    // can still be executing.  Seal every still-'running' iteration so
    // the Live tab can never show a RUNNING block under a done run
    // (covers dropped task_finished events, parallel-iteration races,
    // and cancellation where no per-iteration terminal event fires).
    // 'passed' is the neutral seal — a genuinely failed task already
    // sealed itself via task_finished(ok=false)/iteration_completed
    // above; this only catches buckets left dangling.
    if (type === 'run_completed' || type === 'run_failed') {
      iterations = sealRunningIterations(iterations);
    }

     const findIterIdx = (predicate: (it: LiveTaskState['iterations'][number]) => boolean): number =>
       iterations.findIndex(predicate);

     if (type === 'iteration_started' && blockId) {
       const idx = typeof e.index === 'number' ? e.index : 0;
       const existing = findIterIdx(it => it.blockId === blockId && it.index === idx);
       if (existing < 0) {
         iterations = [...iterations, {
           index: idx, blockId,
           streamText: '', toolCalls: [], events: [e as any],
           status: 'running',
         }];
       } else {
         // Re-emitted started event — append the event but don't double-bucket.
         iterations = iterations.map((it, i) => i === existing
           ? { ...it, events: [...it.events, e as any] }
           : it);
       }
     } else if (type === 'iteration_completed' && blockId) {
       const idx = typeof e.index === 'number' ? e.index : 0;
       const status: 'passed' | 'failed' = e.status === 'failed' ? 'failed' : 'passed';
       const durationMs = typeof e.duration_ms === 'number' ? e.duration_ms : undefined;
       const tokens = typeof e.tokens === 'number' ? e.tokens : undefined;
       const signature = typeof e.signature === 'string' ? e.signature : undefined;
       const existing = findIterIdx(it => it.blockId === blockId && it.index === idx);
       if (existing < 0) {
         // Defensive: completed without started — synthesize the bucket.
         iterations = [...iterations, {
           index: idx, blockId,
           streamText: '', toolCalls: [], events: [e as any],
           status, durationMs, tokens, signature,
         }];
       } else {
         iterations = iterations.map((it, i) => i === existing
           ? {
               ...it,
               status, durationMs, tokens, signature,
               events: [...it.events, e as any],
             }
           : it);
       }
     } else if (type === 'task_finished' && blockId) {
       // A bare task block (not wrapped in Repeat/Until) emits
       // task_finished, NOT iteration_completed — the latter is only
       // emitted by the loop executors.  Without sealing here, an
       // auto-opened iteration for an unwrapped top-level task stays
       // 'running' forever even though the run goes terminal (the
       // "header says done, block stuck RUNNING" bug).  Seal the
       // matching running iteration using the task's own ok flag.
       const status: 'passed' | 'failed' = e.ok === false ? 'failed' : 'passed';
       const durationMs = typeof e.duration_ms === 'number' ? e.duration_ms : undefined;
       const tokens = typeof e.tokens === 'number' ? e.tokens : undefined;
       let target = -1;
       for (let i = iterations.length - 1; i >= 0; i--) {
         if (iterations[i].blockId === blockId && iterations[i].status === 'running') {
           target = i; break;
         }
       }
       if (target >= 0) {
         iterations = iterations.map((it, i) => i === target
           ? {
               ...it, status,
               durationMs: it.durationMs ?? durationMs,
               tokens: it.tokens ?? tokens,
               events: [...it.events, e as any],
             }
           : it);
       }
       // No running bucket → task_finished without a prior open
       // iteration; the timeline already recorded it via `events`.
     } else if (blockId && !isRunScope) {
       // Block-scoped event — route to the current (last running)
       // iteration of that block.  Inside Repeat/Until iterations the
       // server re-tags task_text_delta / task_tool_call with the
       // iteration owner's block_id (see app/agents/task_executor.py and
       // block_executor.py iteration-context plumbing) so this match
       // hits the correct bucket.
       //
       // When the event carries an ``index``, that pair is exact and is
       // preferred: a PARALLEL Repeat has N buckets simultaneously
       // 'running' under one block_id, and the last-running scan below
       // resolves all N to the highest index — so every iteration's
       // text landed in one bucket and the fan-out rendered as a single
       // active block.  The exact match deliberately does NOT require
       // 'running': a delta that arrives after its own iteration sealed
       // still belongs to that iteration, not to a live sibling.
       const evtIndex = typeof e.index === 'number' ? e.index : undefined;
       let target = -1;
       if (evtIndex !== undefined) {
         target = findIterIdx(
           it => it.blockId === blockId && it.index === evtIndex,
         );
       }
       if (target < 0) {
         // No ordinal (a bare task, or a pre-fix run being replayed):
         // fall back to the historical last-running scan.
         for (let i = iterations.length - 1; i >= 0; i--) {
           if (iterations[i].blockId === blockId && iterations[i].status === 'running') {
             target = i; break;
           }
         }
       }
       // Creation is gated; ROUTING into an existing bucket is not.
       //
       // ``block_status`` is emitted for EVERY structural block, so an
       // unconditional auto-open minted a phantom "Iteration 0" for
       // every Repeat / Group / Parallel / Until in the card — blocks
       // that never run a task directly.  Those buckets are empty by
       // construction (a container emits no text and no tool calls) and
       // survived only because the Live and Tools tabs filter empty ones
       // out; ``live.iterations`` was still carrying entries that are
       // not iterations.
       //
       // Once a bucket DOES exist for a block, that block's own status
       // transitions are legitimate content for it, so only creation
       // consults TASK_SCOPED_EVENTS.  Either way the flat timeline and
       // ``blockStatuses`` below record the event.
       if (target < 0 && TASK_SCOPED_EVENTS.has(type)) {
         iterations = [...iterations, {
           index: 0, blockId,
           streamText: '', toolCalls: [], events: [],
           status: 'running',
         }];
         target = iterations.length - 1;
       }
       if (target >= 0) {
         iterations = iterations.map((it, i) => i === target
           ? bucketEventIntoIteration(it, type, e)
           : it);
       }
     }

    // Mirror of the backend seam (app/agents/task_executor.py,
    // ``tool_display`` branch): no text event crosses a tool boundary, so
    // naive concatenation welds the sentence before the call onto the one
    // after it.  The backend fix repairs ``full_text`` / the persisted
    // summary, but live text is accumulated here from the raw deltas and
    // needs the same break.  Only applied when the text doesn't already end
    // in a newline, so a model that emitted its own break is untouched.
    if (type === 'task_tool_call') {
      const tcBlock = typeof e.block_id === 'string' ? e.block_id : '';
      const soFar = tcBlock ? (prev.text[tcBlock] ?? '') : '';
      if (soFar && !/\n$/.test(soFar)) {
        text = { ...prev.text, [tcBlock]: soFar + '\n\n' };
      }
    }

    // ``task_text_delta_run`` is the relay's REPLAY shape for the same
    // content.  The server folds adjacent same-block deltas into one
    // entry as it records history (app/agents/task_run_stream_relay.py
    // ``_record``), concatenating ``content`` verbatim, while LIVE pushes
    // stay raw.  A client therefore receives the collapsed form for
    // everything that streamed before it attached and raw deltas after —
    // never both for the same text, so accepting both cannot double-count.
    // Handling only the raw type silently discarded every character
    // produced before the WebSocket connected: the Live tab appeared to
    // start mid-run (or empty) on any reload or late attach.
    if (type === 'task_text_delta' || type === 'task_text_delta_run') {
      const blockId = typeof e.block_id === 'string' ? e.block_id : '';
      const content = typeof e.content === 'string' ? e.content : '';
      if (blockId && content) {
        text = { ...prev.text, [blockId]: (prev.text[blockId] ?? '') + content };
      }
    } else if (type === 'task_tool_call') {
      const call = {
        block_id: typeof e.block_id === 'string' ? e.block_id : undefined,
        tool_name: typeof e.tool_name === 'string' ? e.tool_name : undefined,
        tool_id: typeof e.tool_id === 'string' ? e.tool_id : undefined,
        result_preview: typeof e.result_preview === 'string' ? e.result_preview : undefined,
        ts: typeof e.ts === 'number' ? e.ts : undefined,
      };
      toolCalls = prev.toolCalls.length >= MAX_TOOL_CALLS
        ? [...prev.toolCalls.slice(prev.toolCalls.length - MAX_TOOL_CALLS + 1), call]
        : [...prev.toolCalls, call];
    }

    // state_applied: merge resolved variable values (last-write-wins)
    // so the running card shows the current run-scoped set.  Prose
    // context is ambient and deliberately not surfaced here.
    let variables = prev.variables;
    if (type === 'state_applied' && e.values && typeof e.values === 'object') {
      variables = { ...prev.variables, ...(e.values as Record<string, unknown>) };
    }

    // Live-progress surface: every event is proof of life; a
    // task_progress event additionally carries a display note.
    const lastActivityTs = typeof e.ts === 'number' ? e.ts : Date.now() / 1000;
    // block_status: per-block lifecycle transition for the run map
    // (running / done / failed / cancelled / skipped).  Last-write-wins.
    let blockStatuses = prev.blockStatuses;
    if (type === 'block_status' && typeof e.block_id === 'string'
        && typeof e.status === 'string') {
      blockStatuses = { ...prev.blockStatuses, [e.block_id]: e.status };
    }

    let progressNote = prev.progressNote;
    let modelProgressNote = prev.modelProgressNote;
    if (type === 'task_progress' && typeof e.note === 'string' && e.note) {
      progressNote = e.note;
      if (e.source === 'model') {
        modelProgressNote = e.note;
      }
    }

     return {
       text, toolCalls, events, iterations, variables, blockStatuses,
       progressNote, modelProgressNote, lastActivityTs,
     };
  });
}

 /**
  * Append a block-scoped event to a single iteration bucket,
  * threading task_text_delta into ``streamText`` and task_tool_call
  * into ``toolCalls``.  Other event types only land on the
  * iteration's ``events`` timeline.
  */
 function bucketEventIntoIteration(
   it: LiveTaskState['iterations'][number],
   type: string,
   e: { [k: string]: unknown },
 ): LiveTaskState['iterations'][number] {
   let streamText = it.streamText;
   let toolCalls = it.toolCalls;
   // Both the live raw delta and the relay's collapsed replay entry
   // carry block-scoped text in ``content``; the per-iteration bucket
   // needs the replayed form too or a reloaded run shows empty
   // iteration bodies while the Events tab still lists the traffic.
   if (type === 'task_text_delta' || type === 'task_text_delta_run') {
     const content = typeof e.content === 'string' ? e.content : '';
     if (content) streamText = streamText + content;
   } else if (type === 'task_tool_call') {
     // Same seam as the flat ``text`` map above — the per-iteration bucket
     // concatenates independently, so it needs its own break.
     if (streamText && !/\n$/.test(streamText)) {
       streamText = streamText + '\n\n';
     }
     toolCalls = [...toolCalls, {
       block_id: typeof e.block_id === 'string' ? e.block_id : undefined,
       tool_name: typeof e.tool_name === 'string' ? e.tool_name : undefined,
       tool_id: typeof e.tool_id === 'string' ? e.tool_id : undefined,
       result_preview: typeof e.result_preview === 'string' ? e.result_preview : undefined,
       ts: typeof e.ts === 'number' ? e.ts : undefined,
     }];
   }
   return {
     ...it,
     streamText, toolCalls,
     events: [...it.events, e as any],
   };
 }
