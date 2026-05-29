/**
 * Tests for accumulateLive — the pure reducer that folds task-run
 * stream events into LiveTaskState.  Iteration tracking (Problem 3
 * from the task-card UX feedback) is the focus here: events should
 * route to a per-iteration bucket so the inspector can render
 * iteration-N delimiters in the Live / Tools / Events tabs.
 *
 * The reducer is tested via a shim that mirrors the dispatcher
 * pattern of React's setState — we pass the reducer a factory that
 * produces the next state and verify the output.
 */

import { accumulateLive, type LiveTaskState } from '../useTaskRunStream';

const EMPTY: LiveTaskState = {
  text: {}, toolCalls: [], events: [], iterations: [],
};

/**
 * Apply a sequence of events to an initial state by mimicking
 * React's setLive(prev => next) callable.  Returns the final state.
 */
function applyEvents(
  initial: LiveTaskState,
  events: Array<Record<string, unknown>>,
): LiveTaskState {
  let state = initial;
  for (const evt of events) {
    accumulateLive((updater) => {
      state = typeof updater === 'function' ? (updater as any)(state) : updater;
    }, evt);
  }
  return state;
}

describe('accumulateLive — iteration tracking', () => {
  it('creates a synthetic iteration 0 for events that arrive before iteration_started', () => {
    // Simple (non-repeat) task blocks emit task_text_delta directly
    // without any iteration_started.  We still want a single
    // iteration entry so the inspector can render uniformly.
    const out = applyEvents(EMPTY, [
      { type: 'task_text_delta', block_id: 'b1', content: 'hello ' },
      { type: 'task_text_delta', block_id: 'b1', content: 'world' },
    ]);
    expect(out.iterations).toHaveLength(1);
    expect(out.iterations[0]).toMatchObject({
      index: 0,
      blockId: 'b1',
      streamText: 'hello world',
      status: 'running',
    });
    // Flat backward-compat fields preserved.
    expect(out.text['b1']).toBe('hello world');
  });

  it('opens a new iteration on iteration_started', () => {
    const out = applyEvents(EMPTY, [
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      { type: 'task_text_delta', block_id: 'b1', content: 'first' },
      { type: 'iteration_completed', block_id: 'b1', index: 0, status: 'passed' },
      { type: 'iteration_started', block_id: 'b1', index: 1 },
      { type: 'task_text_delta', block_id: 'b1', content: 'second' },
    ]);
    expect(out.iterations).toHaveLength(2);
    expect(out.iterations[0]).toMatchObject({
      index: 0, streamText: 'first', status: 'passed',
    });
    expect(out.iterations[1]).toMatchObject({
      index: 1, streamText: 'second', status: 'running',
    });
  });

  it('seals iteration on iteration_completed with status and metadata', () => {
    const out = applyEvents(EMPTY, [
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      { type: 'task_text_delta', block_id: 'b1', content: 'output' },
      {
        type: 'iteration_completed', block_id: 'b1', index: 0,
        status: 'passed', signature: 'sig', duration_ms: 1234, tokens: 50,
      },
    ]);
    expect(out.iterations[0]).toMatchObject({
      index: 0,
      streamText: 'output',
      status: 'passed',
      durationMs: 1234,
      tokens: 50,
    });
  });

  it('marks iteration failed when iteration_completed reports failed', () => {
    const out = applyEvents(EMPTY, [
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      { type: 'iteration_completed', block_id: 'b1', index: 0, status: 'failed' },
    ]);
    expect(out.iterations[0].status).toBe('failed');
  });

  it('routes tool calls to the current iteration', () => {
    const out = applyEvents(EMPTY, [
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      {
        type: 'task_tool_call', block_id: 'b1',
        tool_name: 'shell', tool_id: 't1', result_preview: 'ok',
      },
      { type: 'iteration_completed', block_id: 'b1', index: 0, status: 'passed' },
      { type: 'iteration_started', block_id: 'b1', index: 1 },
      {
        type: 'task_tool_call', block_id: 'b1',
        tool_name: 'fetch', tool_id: 't2', result_preview: 'data',
      },
    ]);
    expect(out.iterations[0].toolCalls).toHaveLength(1);
    expect(out.iterations[0].toolCalls[0]).toMatchObject({ tool_name: 'shell' });
    expect(out.iterations[1].toolCalls).toHaveLength(1);
    expect(out.iterations[1].toolCalls[0]).toMatchObject({ tool_name: 'fetch' });
    // Flat list still has both for backward compat.
    expect(out.toolCalls).toHaveLength(2);
  });

  it('routes generic timeline events to the current iteration', () => {
    const out = applyEvents(EMPTY, [
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      { type: 'block_started', block_id: 'b1' }, // arbitrary
      { type: 'iteration_completed', block_id: 'b1', index: 0, status: 'passed' },
      { type: 'iteration_started', block_id: 'b1', index: 1 },
      { type: 'task_tool_call', block_id: 'b1', tool_name: 'x' },
    ]);
    // First iteration captured iteration_started + block_started + iteration_completed
    expect(out.iterations[0].events.length).toBeGreaterThanOrEqual(2);
    // Second captured iteration_started + tool_call
    expect(out.iterations[1].events.length).toBeGreaterThanOrEqual(2);
  });

  it('preserves a flat events timeline alongside the iteration buckets', () => {
    const out = applyEvents(EMPTY, [
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      { type: 'task_text_delta', block_id: 'b1', content: 'a' },
      { type: 'iteration_completed', block_id: 'b1', index: 0, status: 'passed' },
    ]);
    // Backward-compat: existing inspector code reads .events as a flat list.
    expect(out.events.length).toBe(3);
  });

  it('appends to existing iteration when iteration_started repeats with the same index', () => {
    // Defensive: a buggy or replayed event stream might re-emit the
    // start event.  We should not double-bucket — keep using the
    // existing entry.
    const out = applyEvents(EMPTY, [
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      { type: 'task_text_delta', block_id: 'b1', content: 'a' },
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      { type: 'task_text_delta', block_id: 'b1', content: 'b' },
    ]);
    expect(out.iterations).toHaveLength(1);
    expect(out.iterations[0].streamText).toBe('ab');
  });

  it('handles iteration_completed without a matching started (defensive)', () => {
    // Server-side fault tolerance — if started was dropped on the
    // wire and only completed arrived, treat completed as both
    // open + close so nothing is lost downstream.
    const out = applyEvents(EMPTY, [
      { type: 'iteration_completed', block_id: 'b1', index: 0, status: 'passed' },
    ]);
    expect(out.iterations).toHaveLength(1);
    expect(out.iterations[0].status).toBe('passed');
  });

  it('records the run-level events (run_started, run_completed) at top level only', () => {
    // Run-scoped events have no block_id and should not be bucketed
    // into any iteration; they live on the flat .events timeline.
    const out = applyEvents(EMPTY, [
      { type: 'run_started', run_id: 'r1' },
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      { type: 'task_text_delta', block_id: 'b1', content: 'x' },
      { type: 'iteration_completed', block_id: 'b1', index: 0, status: 'passed' },
      { type: 'run_completed', run_id: 'r1', status: 'done' },
    ]);
    // Run-scope events not in any iteration's bucket.
    const allBucketed = out.iterations.flatMap(it => it.events);
    expect(allBucketed.some(e => e.type === 'run_started')).toBe(false);
    expect(allBucketed.some(e => e.type === 'run_completed')).toBe(false);
    // Flat list still has them.
    expect(out.events.some(e => e.type === 'run_started')).toBe(true);
    expect(out.events.some(e => e.type === 'run_completed')).toBe(true);
  });
});
