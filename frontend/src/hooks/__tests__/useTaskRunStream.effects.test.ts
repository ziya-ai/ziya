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

/**
 * A 'held' run must tear down its transport.
 *
 * ``held`` means an infrastructure fault unwound the executor coroutine, so
 * nothing can still be produced for this run: the server writes the status
 * only after ``execute_block`` has raised and the fan-out has been
 * gathered, the WS endpoint (app/server.py) never closes from its own side,
 * and a resume mints a NEW run id rather than reviving this one.  Yet the
 * socket gate and the reconcile effect both asked ``TERMINAL``, which
 * deliberately excludes 'held' for an unrelated reason (the recency
 * exemption in shouldAcceptFetchedRun).  Three leaks followed, and they are
 * not the same leak seen from three angles — each has its own trigger:
 *
 *   A. Held learned from the safety-net POLL (terminal frame dropped, or a
 *      dead-but-open socket): socket stayed open AND the poll kept running.
 *   B. Held ALREADY set when the tile mounted — reopening a chat that holds
 *      a held run, or the launch preflight minting one outright.  No
 *      ``run_completed`` can ever arrive because the run ended before the
 *      client connected, so nothing was ever going to close it.  This is
 *      the dominant case in practice: one idle socket plus one 15 s poll
 *      per mounted tile, for as long as the chat stays open.
 *   C. Held learned from the ``run_completed`` EVENT: ``onmessage`` closes
 *      the socket itself, but only the reconcile effect clears the poll —
 *      so the socket closed and the 15 s poll ran forever.
 *
 * 'paused' is the boundary these tests also pin: it resumes IN PLACE
 * (block_executor._wait_if_paused writes 'running' back onto the same run),
 * so widening teardown to cover it would silence a stream that is genuinely
 * coming back.
 */
describe('useTaskRunStream held-run teardown', () => {
  // Positive control for the whole describe block.  Every assertion below
  // is of the form "no socket" / "no further fetches", which a harness that
  // silently stopped constructing sockets would satisfy vacuously.  This
  // proves the harness observes a socket when one is genuinely opened.
  it('opens a socket and polls for a running run (control)', async () => {
    mockedGetTaskRun.mockResolvedValue(makeRun('running'));

    renderHook(() => useTaskRunStream('proj-1', 'run-1'));
    await flush();

    expect(FakeWebSocket.instances.length).toBe(1);
    const calls = mockedGetTaskRun.mock.calls.length;
    await act(async () => { jest.advanceTimersByTime(15000); });
    await flush();
    expect(mockedGetTaskRun.mock.calls.length).toBeGreaterThan(calls);
  });

  // Leak B — the dominant case.
  it('does not open a socket when the run is already held on load', async () => {
    mockedGetTaskRun.mockResolvedValue(makeRun('held'));

    renderHook(() => useTaskRunStream('proj-1', 'run-1'));
    await flush();

    expect(FakeWebSocket.instances.length).toBe(0);
  });

  it('does not arm the safety-net poll for a run already held on load', async () => {
    mockedGetTaskRun.mockResolvedValue(makeRun('held'));

    renderHook(() => useTaskRunStream('proj-1', 'run-1'));
    await flush();
    const afterMount = mockedGetTaskRun.mock.calls.length;

    // Four poll intervals.  A held run's record can only change when a
    // human fixes the environment, and that mints a new run id — so every
    // one of these reads is guaranteed to return the same bytes.
    await act(async () => { jest.advanceTimersByTime(60000); });
    await flush();
    expect(mockedGetTaskRun.mock.calls.length).toBe(afterMount);
  });

  // Leak A — held observed through the poll rather than the event.
  it('closes the socket when a watched run becomes held', async () => {
    mockedGetTaskRun
      .mockResolvedValueOnce(makeRun('running'))
      .mockResolvedValue(makeRun('held'));

    renderHook(() => useTaskRunStream('proj-1', 'run-1'));
    await flush();
    expect(FakeWebSocket.instances.length).toBe(1);
    const ws = FakeWebSocket.instances[0];

    await act(async () => { jest.advanceTimersByTime(15000); });
    await flush();

    await waitFor(() => expect(ws.closed).toBe(true));
  });

  it('stops polling when a watched run becomes held', async () => {
    mockedGetTaskRun
      .mockResolvedValueOnce(makeRun('running'))
      .mockResolvedValue(makeRun('held'));

    renderHook(() => useTaskRunStream('proj-1', 'run-1'));
    await flush();

    // One interval to observe 'held'.
    await act(async () => { jest.advanceTimersByTime(15000); });
    await flush();
    const atHold = mockedGetTaskRun.mock.calls.length;

    await act(async () => { jest.advanceTimersByTime(60000); });
    await flush();
    expect(mockedGetTaskRun.mock.calls.length).toBe(atHold);
  });

  /**
   * The trap in this fix.
   *
   * ``ws.onclose`` schedules a reconnect unless ``terminalFetchedRef`` is
   * set, and that ref was previously only set on ``TERMINAL``.  A fix that
   * closes the socket on held WITHOUT also setting it converts one idle
   * socket into an endless reconnect loop (1 s, 2 s, … capped at 10 s) —
   * strictly worse than the leak being removed, and invisible to a test
   * that only asserts the first socket closed.
   */
  it('does not reconnect after tearing down a held run', async () => {
    mockedGetTaskRun
      .mockResolvedValueOnce(makeRun('running'))
      .mockResolvedValue(makeRun('held'));

    renderHook(() => useTaskRunStream('proj-1', 'run-1'));
    await flush();
    expect(FakeWebSocket.instances.length).toBe(1);

    await act(async () => { jest.advanceTimersByTime(15000); });
    await flush();
    await waitFor(() => expect(FakeWebSocket.instances[0].closed).toBe(true));

    // Well past the 10 s backoff ceiling, so a loop would have produced
    // several sockets by now rather than merely one more.
    await act(async () => { jest.advanceTimersByTime(60000); });
    await flush();
    expect(FakeWebSocket.instances.length).toBe(1);
  });

  // Leak C — the event path closed the socket but never the poll.
  it('stops polling when held arrives via the run_completed event', async () => {
    mockedGetTaskRun
      .mockResolvedValueOnce(makeRun('running'))
      .mockResolvedValue(makeRun('held'));

    renderHook(() => useTaskRunStream('proj-1', 'run-1'));
    await flush();
    expect(FakeWebSocket.instances.length).toBe(1);
    const ws = FakeWebSocket.instances[0];

    await act(async () => {
      ws.onmessage?.({
        data: JSON.stringify({
          type: 'run_completed', run_id: 'run-1', status: 'held',
        }),
      });
    });
    await flush();

    // onmessage closes the socket itself, so this half was never broken —
    // asserted to keep the two halves distinguishable if one regresses.
    expect(ws.closed).toBe(true);

    const atHold = mockedGetTaskRun.mock.calls.length;
    await act(async () => { jest.advanceTimersByTime(60000); });
    await flush();
    expect(mockedGetTaskRun.mock.calls.length).toBe(atHold);
  });

  /**
   * Boundary guard: 'paused' must NOT be torn down.
   *
   * Passes before the fix as well as after — it exists to catch a fix that
   * reaches for "not running" instead of "executor unwound".  A paused run
   * resumes onto the SAME run id, so closing its socket would leave the
   * tile blind for the rest of the run with nothing on screen saying so.
   */
  it('keeps the socket and poll alive for a paused run', async () => {
    mockedGetTaskRun
      .mockResolvedValueOnce(makeRun('running'))
      .mockResolvedValue(makeRun('paused'));

    renderHook(() => useTaskRunStream('proj-1', 'run-1'));
    await flush();
    expect(FakeWebSocket.instances.length).toBe(1);
    const ws = FakeWebSocket.instances[0];

    await act(async () => { jest.advanceTimersByTime(15000); });
    await flush();
    const atPause = mockedGetTaskRun.mock.calls.length;

    expect(ws.closed).toBe(false);

    // Still polling: a pause is released by a user action that writes
    // 'running' back onto this same record, so this is the only surface
    // that will ever learn the run came back.
    await act(async () => { jest.advanceTimersByTime(30000); });
    await flush();
    expect(mockedGetTaskRun.mock.calls.length).toBeGreaterThan(atPause);
  });
});