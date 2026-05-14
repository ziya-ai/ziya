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
 * Events are transient; the REST snapshot remains authoritative.  A
 * dropped WS connection degrades to no-updates; callers can surface
 * a "reconnect" button or simply wait for the next component mount.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import type { TaskRun } from '../types/task_run';
import { getTaskRun } from '../services/taskRunApi';

const TERMINAL: ReadonlyArray<TaskRun['status']> = ['done', 'failed', 'cancelled'];

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

  useEffect(() => {
    mountedRef.current = true;
    terminalFetchedRef.current = false;
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

  return { run, error, loading, refresh: fetchOnce };
}
