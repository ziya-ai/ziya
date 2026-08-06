/**
 * Effect-layer tests for useTaskRunStream: the safety-net REST poll and
 * terminal reconciliation added to self-heal a "stuck running" tile when
 * the terminal run_completed WS event is lost.
 *
 * These exercise the React effects (not the pure dispatch/accumulate
 * helpers, which are covered elsewhere) via renderHook with fake timers,
 * a mocked getTaskRun, and a stub WebSocket.
 */

import { renderHook, waitFor, act } from '@testing-library/react';
import { useTaskRunStream } from '../useTaskRunStream';
import * as api from '../../services/taskRunApi';
import type { TaskRun } from '../../types/task_run';

jest.mock('../../services/taskRunApi');

const mockedGetTaskRun = api.getTaskRun as jest.MockedFunction<typeof api.getTaskRun>;

// --- Minimal WebSocket stub -------------------------------------------- //
// The hook only ever calls `new WebSocket(url)`, assigns onmessage/onerror/
// onclose, and calls close().  We record instances so tests can assert
// close() and simulate frames if needed.
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  url: string;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onopen: (() => void) | null = null;
  closed = false;
  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }
  close() { this.closed = true; if (this.onclose) this.onclose(); }
  /** Simulate an unexpected drop — same effect as close() from the
   * hook's point of view, but named for readability at call sites. */
  simulateDrop() { this.closed = true; if (this.onclose) this.onclose(); }
}

// ``id`` must match the runId the hook asked for: shouldAcceptFetchedRun
// discards a snapshot whose id differs from the expected runId (a real
// guard against an in-flight GET for a previous attempt landing under the
// new one).  A hardcoded id therefore made every multi-runId test see
// run===null and no socket — a test bug that looked like a hook bug.
function makeRun(status: TaskRun['status'], id = 'run-1'): TaskRun {
  return { id, status } as TaskRun;
}

const origWS = (global as any).WebSocket;

beforeEach(() => {
  jest.useFakeTimers();
  FakeWebSocket.instances = [];
  (global as any).WebSocket = FakeWebSocket as any;
  mockedGetTaskRun.mockReset();
});

afterEach(() => {
  jest.clearAllTimers();
  jest.useRealTimers();
  (global as any).WebSocket = origWS;
});

/** Flush pending microtasks (resolved promises) inside act(). */
async function flush() {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

describe('useTaskRunStream safety-net poll', () => {
  it('polls getTaskRun on the interval while the run is non-terminal', async () => {
    mockedGetTaskRun.mockResolvedValue(makeRun('running'));

    renderHook(() => useTaskRunStream('proj-1', 'run-1'));

    // initial mount fetch
    await flush();
    const afterMount = mockedGetTaskRun.mock.calls.length;
    expect(afterMount).toBeGreaterThanOrEqual(1);

    // WS effect should have opened a socket (run is non-terminal)
    expect(FakeWebSocket.instances.length).toBe(1);

    // advance one poll interval → one more fetch
    await act(async () => { jest.advanceTimersByTime(15000); });
    await flush();
    expect(mockedGetTaskRun.mock.calls.length).toBeGreaterThan(afterMount);
  });

  it('stops polling and closes the socket once status is terminal', async () => {
    // first call: running (opens WS + poll); subsequent: done (terminal)
    mockedGetTaskRun
      .mockResolvedValueOnce(makeRun('running'))
      .mockResolvedValue(makeRun('done'));

    renderHook(() => useTaskRunStream('proj-1', 'run-1'));
    await flush();
    expect(FakeWebSocket.instances.length).toBe(1);
    const ws = FakeWebSocket.instances[0];

    // one poll → fetch returns 'done' → reconcile effect fires
    await act(async () => { jest.advanceTimersByTime(15000); });
    await flush();

    await waitFor(() => expect(ws.closed).toBe(true));

    const callsAtTerminal = mockedGetTaskRun.mock.calls.length;

    // further timer advances must NOT trigger more polls (interval cleared)
    await act(async () => { jest.advanceTimersByTime(60000); });
    await flush();
    expect(mockedGetTaskRun.mock.calls.length).toBe(callsAtTerminal);
  });

  it('does not open a socket when the run is already terminal on load', async () => {
    mockedGetTaskRun.mockResolvedValue(makeRun('done'));

    renderHook(() => useTaskRunStream('proj-1', 'run-1'));
    await flush();

    expect(FakeWebSocket.instances.length).toBe(0);

    // no polling either
    const calls = mockedGetTaskRun.mock.calls.length;
    await act(async () => { jest.advanceTimersByTime(60000); });
    await flush();
    expect(mockedGetTaskRun.mock.calls.length).toBe(calls);
  });
});

describe('useTaskRunStream attempt switching (B1 regression)', () => {
  // Regression: switching from a TERMINAL run to a fresh NON-TERMINAL run
  // (attempt rail / resume-from-block) must reopen the WS.  The bug was
  // that ``run`` state still held the old terminal run when the WS-gating
  // effect re-ran for the new runId, so it saw a non-null-but-terminal
  // ``run`` and returned early — permanently, since nothing later caused
  // that effect to run again.
  it('opens a fresh socket when runId changes from a terminal run to a non-terminal one', async () => {
    mockedGetTaskRun.mockImplementation(async (_project: string, runId: string) => {
      if (runId === 'run-old') return makeRun('done', 'run-old');
      return makeRun('running', runId);
    });

    const { rerender } = renderHook(
      ({ runId }) => useTaskRunStream('proj-1', runId),
      { initialProps: { runId: 'run-old' } },
    );
    await flush();
    // Terminal on load — no socket opened.
    expect(FakeWebSocket.instances.length).toBe(0);

    rerender({ runId: 'run-new' });
    await flush();

    // The new, non-terminal run must get its own live socket — this is
    // exactly what B1 broke: without the fix, ``run`` still referenced
    // the OLD terminal run when the WS effect re-ran, and no socket was
    // ever opened for the new attempt.
    expect(FakeWebSocket.instances.length).toBe(1);
  });

  it('opens a fresh socket when switching between two non-terminal runs', async () => {
    mockedGetTaskRun.mockImplementation(
      async (_project: string, runId: string) => makeRun('running', runId),
    );

    const { rerender } = renderHook(
      ({ runId }) => useTaskRunStream('proj-1', runId),
      { initialProps: { runId: 'run-a' } },
    );
    await flush();
    expect(FakeWebSocket.instances.length).toBe(1);

    rerender({ runId: 'run-b' });
    await flush();

    // A second socket for the new run — the first must have been closed
    // by the effect's own cleanup, and a new one opened for run-b.
    expect(FakeWebSocket.instances.length).toBe(2);
    expect(FakeWebSocket.instances[0].closed).toBe(true);
    expect(FakeWebSocket.instances[1].closed).toBe(false);
  });
});

describe('useTaskRunStream WS reconnect (B2)', () => {
  it('reopens the socket after an unexpected drop while the run is non-terminal', async () => {
    mockedGetTaskRun.mockResolvedValue(makeRun('running'));

    renderHook(() => useTaskRunStream('proj-1', 'run-1'));
    await flush();
    expect(FakeWebSocket.instances.length).toBe(1);

    // Simulate an unexpected drop (relay hiccup) — not a close() the
    // hook itself requested.
    FakeWebSocket.instances[0].simulateDrop();
    await flush();

    // Reconnect is scheduled with backoff, not opened synchronously.
    expect(FakeWebSocket.instances.length).toBe(1);

    await act(async () => { jest.advanceTimersByTime(1000); });
    await flush();

    expect(FakeWebSocket.instances.length).toBe(2);
  });

  it('does not reconnect after a drop once the run has gone terminal', async () => {
    mockedGetTaskRun
      .mockResolvedValueOnce(makeRun('running'))
      .mockResolvedValue(makeRun('done'));

    renderHook(() => useTaskRunStream('proj-1', 'run-1'));
    await flush();
    expect(FakeWebSocket.instances.length).toBe(1);

    // Drive the run to terminal via the safety-net poll, which also
    // clears any pending reconnect (this is the fix to the terminal
    // reconciliation effect).
    await act(async () => { jest.advanceTimersByTime(15000); });
    await flush();
    await waitFor(() => expect(FakeWebSocket.instances[0].closed).toBe(true));

    // Advancing well past the reconnect backoff window must not open a
    // second socket — the run is done.
    await act(async () => { jest.advanceTimersByTime(30000); });
    await flush();
    expect(FakeWebSocket.instances.length).toBe(1);
  });

  it('backs off with increasing delay across consecutive drops', async () => {
    mockedGetTaskRun.mockResolvedValue(makeRun('running'));

    renderHook(() => useTaskRunStream('proj-1', 'run-1'));
    await flush();
    expect(FakeWebSocket.instances.length).toBe(1);

    // First drop: reconnect after ~1000ms (attempt 1).
    FakeWebSocket.instances[0].simulateDrop();
    await flush();
    await act(async () => { jest.advanceTimersByTime(999); });
    await flush();
    expect(FakeWebSocket.instances.length).toBe(1);
    await act(async () => { jest.advanceTimersByTime(1); });
    await flush();
    expect(FakeWebSocket.instances.length).toBe(2);

    // Second consecutive drop without an intervening successful message:
    // backoff should be longer (attempt 2 → ~2000ms), not reset to 1000ms.
    FakeWebSocket.instances[1].simulateDrop();
    await flush();
    await act(async () => { jest.advanceTimersByTime(1999); });
    await flush();
    expect(FakeWebSocket.instances.length).toBe(2);
    await act(async () => { jest.advanceTimersByTime(1); });
    await flush();
    expect(FakeWebSocket.instances.length).toBe(3);
  });
});