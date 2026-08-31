/**
 * Per-iteration routing for PARALLEL loops.
 *
 * A parallel Repeat runs N iterations concurrently, and the server
 * re-tags each iteration's deltas with the LOOP's block_id so they
 * bucket under the loop rather than the inner task.  That leaves N
 * buckets simultaneously 'running' under one block_id — and the
 * reducer's historical "last bucket for this block that is still
 * running" scan resolves all N of them to the highest index.  Every
 * iteration's text therefore landed in one bucket, which is what made a
 * parallel fan-out render as a single active block with output for only
 * one iteration.
 *
 * The fix is the ``index`` the server now stamps on delta-scoped events:
 * (block_id, index) is the identity, and an exact match on it wins over
 * the last-running scan.  These tests pin both the fix and the fallback,
 * asserting on ``iterations`` — the surface the Live / Tools tabs render
 * from — rather than on any intermediate.
 */

import { accumulateLive, type LiveTaskState } from '../useTaskRunStream';

const EMPTY: LiveTaskState = {
  text: {}, toolCalls: [], events: [], iterations: [],
  variables: {}, blockStatuses: {},
};

function reduce(events: ReadonlyArray<unknown>): LiveTaskState {
  let state = EMPTY;
  const setLive: any = (updater: any) => {
    state = typeof updater === 'function' ? updater(state) : updater;
  };
  for (const e of events) accumulateLive(setLive, e);
  return state;
}

/** The event order a 3-wide parallel fan-out actually produces: all
 * iterations open, then their deltas interleave. */
const FANOUT = [
  { type: 'iteration_started', block_id: 'loop', index: 0 },
  { type: 'iteration_started', block_id: 'loop', index: 1 },
  { type: 'iteration_started', block_id: 'loop', index: 2 },
  { type: 'task_text_delta', block_id: 'loop', index: 0, content: 'A0 ' },
  { type: 'task_text_delta', block_id: 'loop', index: 1, content: 'B0 ' },
  { type: 'task_text_delta', block_id: 'loop', index: 2, content: 'C0 ' },
  { type: 'task_text_delta', block_id: 'loop', index: 0, content: 'A1' },
  { type: 'task_text_delta', block_id: 'loop', index: 1, content: 'B1' },
  { type: 'task_text_delta', block_id: 'loop', index: 2, content: 'C1' },
];

const byIndex = (out: LiveTaskState, index: number) =>
  out.iterations.find(it => it.blockId === 'loop' && it.index === index);

describe('accumulateLive — concurrent iterations of one loop block', () => {
  it('routes interleaved deltas to their own iteration bucket', () => {
    const out = reduce(FANOUT);
    expect(out.iterations).toHaveLength(3);
    expect(byIndex(out, 0)!.streamText).toBe('A0 A1');
    expect(byIndex(out, 1)!.streamText).toBe('B0 B1');
    expect(byIndex(out, 2)!.streamText).toBe('C0 C1');
  });

  it('does not pile every iteration into the highest-index bucket', () => {
    // The specific failure mode, asserted directly: without index
    // routing, bucket 2 absorbed all nine deltas and 0/1 stayed empty.
    const out = reduce(FANOUT);
    for (const i of [0, 1, 2]) {
      expect(byIndex(out, i)!.streamText).not.toBe('');
    }
    expect(byIndex(out, 2)!.streamText).not.toContain('A');
    expect(byIndex(out, 2)!.streamText).not.toContain('B');
  });

  it('keeps tool calls attributed to their own iteration', () => {
    const out = reduce([
      { type: 'iteration_started', block_id: 'loop', index: 0 },
      { type: 'iteration_started', block_id: 'loop', index: 1 },
      { type: 'task_tool_call', block_id: 'loop', index: 0, tool_name: 'read_a' },
      { type: 'task_tool_call', block_id: 'loop', index: 1, tool_name: 'read_b' },
    ]);
    expect(byIndex(out, 0)!.toolCalls.map(t => t.tool_name)).toEqual(['read_a']);
    expect(byIndex(out, 1)!.toolCalls.map(t => t.tool_name)).toEqual(['read_b']);
  });

  it('routes a late delta to its own sealed iteration, not a live sibling', () => {
    // An iteration can seal while a sibling is still streaming.  A delta
    // that arrives after its own iteration_completed still belongs to
    // that iteration — so the exact match must not require 'running'.
    const out = reduce([
      { type: 'iteration_started', block_id: 'loop', index: 0 },
      { type: 'iteration_started', block_id: 'loop', index: 1 },
      { type: 'iteration_completed', block_id: 'loop', index: 0, status: 'passed' },
      { type: 'task_text_delta', block_id: 'loop', index: 0, content: 'late' },
    ]);
    expect(byIndex(out, 0)!.streamText).toBe('late');
    expect(byIndex(out, 1)!.streamText).toBe('');
    expect(byIndex(out, 0)!.status).toBe('passed');
  });

  it('accepts the relay replay shape with an index', () => {
    const out = reduce([
      { type: 'iteration_started', block_id: 'loop', index: 0 },
      { type: 'iteration_started', block_id: 'loop', index: 1 },
      { type: 'task_text_delta_run', block_id: 'loop', index: 1, content: 'replayed' },
    ]);
    expect(byIndex(out, 1)!.streamText).toBe('replayed');
    expect(byIndex(out, 0)!.streamText).toBe('');
  });
});

describe('accumulateLive — fallback when no ordinal is present', () => {
  it('still auto-opens iteration 0 for a bare task block', () => {
    // Back-compat control: a task not wrapped in a loop emits no index.
    const out = reduce([
      { type: 'task_text_delta', block_id: 'plain', content: 'hello' },
    ]);
    expect(out.iterations).toHaveLength(1);
    expect(out.iterations[0]).toMatchObject({
      index: 0, blockId: 'plain', status: 'running', streamText: 'hello',
    });
  });

  it('uses the last-running bucket for indexless serial-loop deltas', () => {
    // A run recorded before the server stamped ``index`` replays without
    // it; those deltas must keep landing in the open iteration rather
    // than being dropped.
    const out = reduce([
      { type: 'iteration_started', block_id: 'loop', index: 0 },
      { type: 'iteration_completed', block_id: 'loop', index: 0, status: 'passed' },
      { type: 'iteration_started', block_id: 'loop', index: 1 },
      { type: 'task_text_delta', block_id: 'loop', content: 'second pass' },
    ]);
    expect(byIndex(out, 1)!.streamText).toBe('second pass');
    expect(byIndex(out, 0)!.streamText).toBe('');
  });

  it('does not mint a bucket for an unknown index on a container', () => {
    // block_status fires for every structural block and is not task-
    // scoped, so it must not create a phantom iteration even when it
    // carries no index.  Guards the phantom-bucket fix.
    const out = reduce([
      { type: 'block_status', block_id: 'group-1', status: 'running' },
    ]);
    expect(out.iterations).toHaveLength(0);
    expect(out.blockStatuses['group-1']).toBe('running');
  });
});
