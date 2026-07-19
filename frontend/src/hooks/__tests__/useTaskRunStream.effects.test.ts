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
  closed = false;
  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }
  close() { this.closed = true; if (this.onclose) this.onclose(); }
}

function makeRun(status: TaskRun['status']): TaskRun {
  return { id: 'run-1', status } as TaskRun;
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
