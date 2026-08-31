/**
 * Run-status propagation to the conversation list's gear.
 *
 * The defect: the sidebar gear renders from ``TaskBinding.run_status``,
 * fetched once on chat open (or on a binding-created event) and never
 * re-read.  A run that went running -> held on an infrastructure fault
 * therefore kept the blue SPINNING gear indefinitely, while the tile
 * beside it showed the hold correctly from the same snapshot.  The violet
 * non-animating ``held`` presentation already existed in
 * runStatusVocabulary — nothing ever delivered the changed status to it.
 *
 * These tests assert the SEAM at each hop, because both halves were
 * individually correct and simply never met:
 *
 *   1. useTaskRunStream observes ``held`` -> announces it
 *   2. useTaskBindings hears the announcement -> re-reads the bindings
 *   3. the refreshed bindings drive statusClusters to violet + static
 *
 * Hop 3 asserts on the outermost surface (the cluster the sidebar
 * renders), not on an intermediate the test constructed.
 */

import { renderHook, act, waitFor } from '@testing-library/react';
import { useTaskRunStream } from '../useTaskRunStream';
import { useTaskBindings } from '../useTaskBindings';
import {
  TASK_RUN_STATUS_EVENT, notifyRunStatusChanged,
  resetRunStatusNotifications,
  type TaskRunStatusChangeDetail,
} from '../taskRunEvents';
import { statusClusters, RUN_STATUS_FG } from '../../components/TaskCard/runStatusVocabulary';
import * as runApi from '../../services/taskRunApi';
import * as bindingApi from '../../services/taskBindingApi';
import * as projectCtx from '../../context/ProjectContext';
import type { TaskRun } from '../../types/task_run';
import type { TaskBinding } from '../../types/task_binding';

// Module FACTORIES, not bare automocks.  A bare ``jest.mock(path)`` still
// loads the real module so jest can introspect its exports to build the
// mock shape, and ProjectContext's import graph reaches src/utils/db.ts ->
// ``uuid`` v13, which is ESM-only and outside craco's transform scope.  The
// suite then fails to parse with "Unexpected token 'export'" from inside
// node_modules — a failure that reads as a broken test but is really the
// import graph being dragged in.  A factory never loads the real module, so
// the graph is cut here.  Same pattern as TaskCardInlineTile.test.tsx.
jest.mock('../../services/taskRunApi', () => ({
  getTaskRun: jest.fn(),
}));
jest.mock('../../services/taskBindingApi', () => ({
  listBindings: jest.fn(),
}));
jest.mock('../../context/ProjectContext', () => ({
  useProject: jest.fn(),
}));

const mockedGetTaskRun = runApi.getTaskRun as jest.MockedFunction<typeof runApi.getTaskRun>;
const mockedListBindings =
  bindingApi.listBindings as jest.MockedFunction<typeof bindingApi.listBindings>;
const mockedUseProject = projectCtx.useProject as jest.MockedFunction<any>;

// Minimal WebSocket stub — the hook only constructs one, assigns handlers
// and closes it.  Mirrors the harness in useTaskRunStream.effects.test.ts.
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  url: string;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onopen: (() => void) | null = null;
  closed = false;
  constructor(url: string) { this.url = url; FakeWebSocket.instances.push(this); }
  close() { this.closed = true; if (this.onclose) this.onclose(); }
}

const origWS = (global as any).WebSocket;

function run(status: TaskRun['status'], id = 'run-1'): TaskRun {
  return { id, status } as TaskRun;
}

function binding(run_status: string, id = 'b1'): TaskBinding {
  return {
    id, chat_id: 'chat-1', card_id: 'card-1', run_id: 'run-1',
    created_at: 1, run_status,
  } as TaskBinding;
}

/** Collect every status-change event dispatched during a test. */
function captureEvents(): TaskRunStatusChangeDetail[] {
  const seen: TaskRunStatusChangeDetail[] = [];
  const handler = (e: Event) => {
    seen.push((e as CustomEvent<TaskRunStatusChangeDetail>).detail);
  };
  window.addEventListener(TASK_RUN_STATUS_EVENT, handler);
  afterEachCleanups.push(
    () => window.removeEventListener(TASK_RUN_STATUS_EVENT, handler),
  );
  return seen;
}

const afterEachCleanups: Array<() => void> = [];

beforeEach(() => {
  resetRunStatusNotifications();
  FakeWebSocket.instances = [];
  (global as any).WebSocket = FakeWebSocket as any;
  mockedGetTaskRun.mockReset();
  mockedListBindings.mockReset();
  mockedUseProject.mockReturnValue({ currentProject: { id: 'proj-1' } });
});

afterEach(() => {
  while (afterEachCleanups.length) afterEachCleanups.pop()!();
  (global as any).WebSocket = origWS;
  jest.useRealTimers();
});

// ── The signal itself ─────────────────────────────────────────────────
describe('notifyRunStatusChanged', () => {
  it('dispatches the run id, new status and previous status', () => {
    const seen = captureEvents();
    expect(notifyRunStatusChanged('run-1', 'held', 'running')).toBe(true);
    expect(seen).toEqual([
      { runId: 'run-1', status: 'held', previous: 'running' },
    ]);
  });

  it('suppresses a duplicate of the same run+status', () => {
    // Two surfaces watching one run (inline tile + deck inspector) must
    // not cost two bindings refetches for one transition.
    const seen = captureEvents();
    expect(notifyRunStatusChanged('run-1', 'held', 'running')).toBe(true);
    expect(notifyRunStatusChanged('run-1', 'held', 'running')).toBe(false);
    expect(seen).toHaveLength(1);
  });

  it('does not suppress a different status for the same run', () => {
    const seen = captureEvents();
    notifyRunStatusChanged('run-1', 'running');
    notifyRunStatusChanged('run-1', 'held', 'running');
    expect(seen.map(d => d.status)).toEqual(['running', 'held']);
  });

  it('does not suppress the same status on a different run', () => {
    const seen = captureEvents();
    notifyRunStatusChanged('run-1', 'held');
    notifyRunStatusChanged('run-2', 'held');
    expect(seen.map(d => d.runId)).toEqual(['run-1', 'run-2']);
  });

  it('re-announces a status the run re-enters after the window', () => {
    // paused -> running -> paused happens on ONE run object, so the
    // suppression must be time-bounded rather than a permanent memo.
    jest.useFakeTimers();
    const seen = captureEvents();
    notifyRunStatusChanged('run-1', 'paused', 'running');
    jest.advanceTimersByTime(1500);
    notifyRunStatusChanged('run-1', 'paused', 'running');
    expect(seen).toHaveLength(2);
  });
});

// ── Hop 1: the stream hook announces what it observes ─────────────────
describe('useTaskRunStream announces run status', () => {
  /** Flush pending microtasks inside act(). */
  async function flush() {
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  }

  it('announces the first observed status', async () => {
    // Load-bearing for the launch race: the preflight can mint a HELD run
    // before the launch-triggered bindings fetch lands, so a
    // transition-only signal would never fire for exactly the case this
    // whole change exists to fix.
    const seen = captureEvents();
    mockedGetTaskRun.mockResolvedValue(run('held'));
    renderHook(() => useTaskRunStream('proj-1', 'run-1'));
    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]).toEqual({
      runId: 'run-1', status: 'held', previous: undefined,
    });
  });

  it('announces running -> held with the previous status', async () => {
    const seen = captureEvents();
    mockedGetTaskRun.mockResolvedValueOnce(run('running'));
    renderHook(() => useTaskRunStream('proj-1', 'run-1'));
    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0].status).toBe('running');

    // The hold arrives the way it really does: run_completed on the WS,
    // which the hook answers with a refetch.
    mockedGetTaskRun.mockResolvedValue(run('held'));
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
    expect(ws).toBeDefined();
    await act(async () => {
      ws.onmessage?.({ data: JSON.stringify({ type: 'run_completed' }) });
      await Promise.resolve();
    });
    await waitFor(() => expect(seen).toHaveLength(2));
    expect(seen[1]).toEqual({
      runId: 'run-1', status: 'held', previous: 'running',
    });
  });

  it('does not re-announce an unchanged status on refetch', async () => {
    // The safety-net poll refetches on a timer; a snapshot that says the
    // same thing must not cost a bindings refetch every interval.
    const seen = captureEvents();
    mockedGetTaskRun.mockResolvedValue(run('running'));
    const { result } = renderHook(() => useTaskRunStream('proj-1', 'run-1'));
    await waitFor(() => expect(seen).toHaveLength(1));
    await act(async () => { await result.current.refresh(); });
    await flush();
    expect(seen).toHaveLength(1);
  });
});

// ── Hops 2 and 3: bindings re-read, and the gear cluster follows ───────
describe('useTaskBindings refreshes on a status change', () => {
  it('re-reads bindings and the gear cluster becomes held/static', async () => {
    jest.useFakeTimers();
    mockedListBindings
      .mockResolvedValueOnce([binding('running')])
      .mockResolvedValue([binding('held')]);

    const { result } = renderHook(() => useTaskBindings('chat-1'));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(result.current.bindings[0].run_status).toBe('running');

    // Positive control that the pre-fix rendering really was a spinning
    // blue gear, so the assertion below is a change and not a tautology.
    const before = statusClusters(result.current.bindings);
    expect(before[0].status).toBe('running');
    expect(before[0].animate).toBe(true);

    act(() => {
      window.dispatchEvent(new CustomEvent(TASK_RUN_STATUS_EVENT, {
        detail: { runId: 'run-1', status: 'held', previous: 'running' },
      }));
    });
    await act(async () => {
      jest.advanceTimersByTime(300);
      await Promise.resolve(); await Promise.resolve();
    });

    expect(mockedListBindings).toHaveBeenCalledTimes(2);
    expect(result.current.bindings[0].run_status).toBe('held');

    // The outermost surface: what the sidebar actually draws.
    const after = statusClusters(result.current.bindings);
    expect(after).toHaveLength(1);
    expect(after[0].status).toBe('held');
    expect(after[0].animate).toBe(false);
    // Violet, matching 'paused' — "stopped, not broken".  Read from the
    // FOREGROUND map, which is what a glyph on a surface must use.
    expect(after[0].color).toBe(RUN_STATUS_FG.held);
    expect(after[0].hint).toMatch(/infrastructure fault/i);
  });

  it('coalesces a burst of announcements into one re-read', async () => {
    // A chat with several tiles announces once per run on mount; a fetch
    // per announcement would be a request burst for one visible change.
    jest.useFakeTimers();
    mockedListBindings.mockResolvedValue([binding('held')]);
    renderHook(() => useTaskBindings('chat-1'));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(mockedListBindings).toHaveBeenCalledTimes(1);

    act(() => {
      for (const id of ['run-1', 'run-2', 'run-3']) {
        window.dispatchEvent(new CustomEvent(TASK_RUN_STATUS_EVENT, {
          detail: { runId: id, status: 'done' },
        }));
      }
    });
    await act(async () => {
      jest.advanceTimersByTime(300);
      await Promise.resolve(); await Promise.resolve();
    });
    expect(mockedListBindings).toHaveBeenCalledTimes(2);
  });
});
