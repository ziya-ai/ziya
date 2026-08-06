/**
 * Tests for the terminal iteration seal (B4).
 *
 * The seal answers "can anything still be producing output?".  It used to
 * live ONLY in accumulateLive's ``run_completed`` branch — i.e. it needed
 * the WS event to arrive.  When the terminal state was instead discovered
 * by the safety-net REST poll (exactly the dropped-event case the poll
 * exists to heal, and the case that follows every socket drop), nothing
 * sealed and the Live tab kept a spinner + streaming cursor running under
 * a run that had already finished — the "no indication of completion"
 * symptom.
 *
 * Two layers:
 *   1. Pure helpers (sealRunningIterations / sealLiveForTerminal), whose
 *      reference-stability contract lets callers skip no-op state writes.
 *   2. The hook effect, driven through a REST-discovered terminal so the
 *      WS event is genuinely absent.
 */

import { renderHook, act, waitFor } from '@testing-library/react';
import {
  accumulateLive, sealLiveForTerminal, sealRunningIterations,
  useTaskRunStream, type LiveTaskState,
} from '../useTaskRunStream';
import * as api from '../../services/taskRunApi';
import type { TaskRun } from '../../types/task_run';

jest.mock('../../services/taskRunApi');
const mockedGetTaskRun = api.getTaskRun as jest.MockedFunction<typeof api.getTaskRun>;

const EMPTY: LiveTaskState = {
  text: {}, toolCalls: [], events: [], iterations: [],
  variables: {}, blockStatuses: {},
};

function iter(
  index: number, status: 'running' | 'passed' | 'failed', blockId = 'b1',
): LiveTaskState['iterations'][number] {
  return {
    index, blockId, streamText: 'text', toolCalls: [], events: [], status,
  };
}

// ── Layer 1: pure helpers ───────────────────────────────────────────

describe('sealRunningIterations', () => {
  it('seals a running bucket to passed', () => {
    const out = sealRunningIterations([iter(0, 'running')]);
    expect(out[0].status).toBe('passed');
  });

  it('leaves an already-failed bucket alone', () => {
    // A genuinely failed iteration sealed itself via task_finished(ok=false);
    // overwriting it with the neutral 'passed' would erase a real verdict.
    const out = sealRunningIterations([iter(0, 'failed')]);
    expect(out[0].status).toBe('failed');
  });

  it('seals only the running buckets in a mixed list', () => {
    const out = sealRunningIterations([
      iter(0, 'passed'), iter(1, 'failed'), iter(2, 'running'),
    ]);
    expect(out.map(i => i.status)).toEqual(['passed', 'failed', 'passed']);
  });

  it('returns the SAME reference when nothing needs sealing', () => {
    // Load-bearing: the effect calls setLive(sealLiveForTerminal) on every
    // status change, so a fresh array each time would re-render the whole
    // inspector for no reason (and could loop).
    const input = [iter(0, 'passed')];
    expect(sealRunningIterations(input)).toBe(input);
  });

  it('returns the same reference for an empty list', () => {
    const input: LiveTaskState['iterations'] = [];
    expect(sealRunningIterations(input)).toBe(input);
  });
});

describe('sealLiveForTerminal', () => {
  it('returns the same state object when nothing needs sealing', () => {
    const prev = { ...EMPTY, iterations: [iter(0, 'passed')] };
    expect(sealLiveForTerminal(prev)).toBe(prev);
  });

  it('returns a new state object with buckets sealed when needed', () => {
    const prev = { ...EMPTY, iterations: [iter(0, 'running')] };
    const next = sealLiveForTerminal(prev);
    expect(next).not.toBe(prev);
    expect(next.iterations[0].status).toBe('passed');
  });

  it('preserves every other field untouched', () => {
    const prev: LiveTaskState = {
      ...EMPTY,
      text: { b1: 'hello' },
      toolCalls: [{ tool_name: 'grep' }],
      events: [{ type: 'task_started' }],
      variables: { env: 'prod' },
      blockStatuses: { b1: 'running' },
      progressNote: 'note',
      iterations: [iter(0, 'running')],
    };
    const next = sealLiveForTerminal(prev);
    expect(next.text).toBe(prev.text);
    expect(next.toolCalls).toBe(prev.toolCalls);
    expect(next.events).toBe(prev.events);
    expect(next.variables).toBe(prev.variables);
    expect(next.blockStatuses).toBe(prev.blockStatuses);
    expect(next.progressNote).toBe('note');
  });
});

// ── accumulateLive still seals on the WS event ──────────────────────

describe('accumulateLive run_completed seal (regression guard)', () => {
  function drive(events: unknown[]): LiveTaskState {
    let state: LiveTaskState = EMPTY;
    for (const e of events) {
      accumulateLive((updater) => {
        state = typeof updater === 'function' ? updater(state) : updater;
      }, e);
    }
    return state;
  }

  it('seals a dangling bucket when run_completed arrives', () => {
    const state = drive([
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      { type: 'task_text_delta', block_id: 'b1', content: 'work' },
      { type: 'run_completed', status: 'done' },
    ]);
    expect(state.iterations).toHaveLength(1);
    expect(state.iterations[0].status).toBe('passed');
  });
});

// ── Layer 2: the effect, with the WS event ABSENT ───────────────────

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
  /** Deliver a frame as the server would. */
  emit(evt: unknown) {
    if (this.onmessage) this.onmessage({ data: JSON.stringify(evt) });
  }
}

function makeRun(status: TaskRun['status'], id = 'run-1'): TaskRun {
  return { id, status } as TaskRun;
}

const origWS = (global as any).WebSocket;

describe('useTaskRunStream seals live buffers on a REST-discovered terminal', () => {
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

  async function flush() {
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  }

  it('seals a running iteration when the poll (not the WS) sees the terminal', async () => {
    mockedGetTaskRun
      .mockResolvedValueOnce(makeRun('running'))
      .mockResolvedValue(makeRun('done'));

    const { result } = renderHook(() => useTaskRunStream('proj-1', 'run-1'));
    await flush();
    expect(FakeWebSocket.instances.length).toBe(1);

    // Open an iteration over the wire, then let the socket go silent —
    // no run_completed frame is EVER delivered.
    await act(async () => {
      FakeWebSocket.instances[0].emit(
        { type: 'iteration_started', block_id: 'b1', index: 0 });
      FakeWebSocket.instances[0].emit(
        { type: 'task_text_delta', block_id: 'b1', content: 'working' });
    });
    await flush();
    expect(result.current.live.iterations[0].status).toBe('running');

    // The safety-net poll discovers 'done'.
    await act(async () => { jest.advanceTimersByTime(15000); });
    await flush();

    await waitFor(() => {
      expect(result.current.live.iterations[0].status).toBe('passed');
    });
  });

  it('also seals for held, which TERMINAL deliberately excludes', async () => {
    // 'held' is excluded from TERMINAL because of the recency exemption in
    // shouldAcceptFetchedRun (see useTaskRunStream.ordering.test.ts) — but
    // the executor HAS unwound, so nothing can still stream.  Without the
    // separate EXECUTOR_STOPPED list, a held run kept its spinner forever.
    mockedGetTaskRun
      .mockResolvedValueOnce(makeRun('running'))
      .mockResolvedValue(makeRun('held'));

    const { result } = renderHook(() => useTaskRunStream('proj-1', 'run-1'));
    await flush();

    await act(async () => {
      FakeWebSocket.instances[0].emit(
        { type: 'iteration_started', block_id: 'b1', index: 0 });
      FakeWebSocket.instances[0].emit(
        { type: 'task_text_delta', block_id: 'b1', content: 'working' });
    });
    await flush();
    expect(result.current.live.iterations[0].status).toBe('running');

    await act(async () => { jest.advanceTimersByTime(15000); });
    await flush();

    await waitFor(() => {
      expect(result.current.live.iterations[0].status).toBe('passed');
    });
  });

  it('does not seal while the run is still live', async () => {
    mockedGetTaskRun.mockResolvedValue(makeRun('running'));

    const { result } = renderHook(() => useTaskRunStream('proj-1', 'run-1'));
    await flush();

    await act(async () => {
      FakeWebSocket.instances[0].emit(
        { type: 'iteration_started', block_id: 'b1', index: 0 });
    });
    await flush();

    await act(async () => { jest.advanceTimersByTime(45000); });
    await flush();

    // Still running — a premature seal would claim the work finished.
    expect(result.current.live.iterations[0].status).toBe('running');
  });
});
