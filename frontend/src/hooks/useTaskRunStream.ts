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

const TERMINAL: ReadonlyArray<TaskRun['status']> = ['done', 'failed', 'cancelled'];

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
}

const EMPTY_LIVE: LiveTaskState = { text: {}, toolCalls: [], events: [], iterations: [] };
const MAX_EVENTS = 500;       // hard cap so a long run can't unbound memory
const MAX_TOOL_CALLS = 200;
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
    case 'iteration_started':
    case 'whisper_received':
      // No new persisted state to display; swallow.
      return { kind: 'ignore' };
    default:
      return { kind: 'ignore' };
  }
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

  const fetchOnce = useCallback(async () => {
    if (!projectId || !runId) return;
    try {
      const r = await getTaskRun(projectId, runId);
      if (mountedRef.current) setRun(r);
    } catch (e) {
      if (mountedRef.current) setError(String(e));
    }
  }, [projectId, runId]);

  const clearLive = useCallback(() => setLive(EMPTY_LIVE), []);

  useEffect(() => {
    mountedRef.current = true;
    terminalFetchedRef.current = false;
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
    if (TERMINAL.includes(run.status)) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${window.location.host}/ws/task-runs/${encodeURIComponent(runId)}`;
    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch (e) {
      console.warn('useTaskRunStream: WebSocket ctor failed:', e);
      return;
    }
    wsRef.current = ws;

    ws.onmessage = async (evt) => {
      if (!mountedRef.current) return;
      let data: unknown;
      try { data = JSON.parse(evt.data); } catch { return; }
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
      // onclose handles cleanup; REST fetch already gave caller state.
    };

    ws.onclose = () => {
      if (wsRef.current === ws) wsRef.current = null;
      // Drop without terminal event → fetch once so state reflects
      // whatever the server settled on.
      if (mountedRef.current && !terminalFetchedRef.current) {
        fetchOnce();
      }
    };

    return () => {
      try { ws.close(); } catch { /* ignore */ }
      if (wsRef.current === ws) wsRef.current = null;
    };
    // Intentional: run?.status would cause teardown on every status
    // transition.  We only (re)open when runId changes or we learn
    // of a non-terminal run for the first time.  Terminal-state
    // reopening is prevented by the early-return above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, runId, run != null, fetchOnce]);

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
     } else if (blockId && !isRunScope) {
       // Block-scoped event — route to the current (last running)
       // iteration of that block, opening a synthetic iteration 0 if
       // none exists yet.  Inside Repeat/Until iterations the server
       // re-tags task_text_delta / task_tool_call with the iteration
       // owner's block_id (see app/agents/task_executor.py and
       // block_executor.py iteration-context plumbing) so this match
       // hits the correct bucket.
       let target = -1;
       for (let i = iterations.length - 1; i >= 0; i--) {
         if (iterations[i].blockId === blockId && iterations[i].status === 'running') {
           target = i; break;
         }
       }
       if (target < 0) {
         // No running iteration for this block — auto-open index 0.
         iterations = [...iterations, {
           index: 0, blockId,
           streamText: '', toolCalls: [], events: [],
           status: 'running',
         }];
         target = iterations.length - 1;
       }
       iterations = iterations.map((it, i) => i === target
         ? bucketEventIntoIteration(it, type, e)
         : it);
     }

    if (type === 'task_text_delta') {
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

     return { text, toolCalls, events, iterations };
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
   if (type === 'task_text_delta') {
     const content = typeof e.content === 'string' ? e.content : '';
     if (content) streamText = streamText + content;
   } else if (type === 'task_tool_call') {
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
