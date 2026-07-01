/**
 * Tests for ``accumulateLive`` iteration-bucketing logic.
 *
 * The reducer maintains per-iteration buckets so the inspector can
 * render iteration delimiters in Live / Tools / Events tabs without
 * losing the cohesive narrative across iterations of repeat blocks.
 *
 * Plan-α boundary semantics:
 *   - run-scoped events (run_started, run_completed) stay on the
 *     flat events timeline only — never bucketed
 *   - iteration_started opens a new bucket for {blockId, index}
 *   - iteration_completed seals the matching bucket with status
 *     and timing/token/signature metadata
 *   - lazy auto-open: a block-scoped event arriving with no running
 *     iteration for that block opens index 0 (covers simple
 *     non-repeat task blocks that don't emit iteration events)
 *   - flat ``text`` / ``toolCalls`` / ``events`` are preserved for
 *     backward compatibility — existing consumers keep working
 */

import { accumulateLive, type LiveTaskState } from '../useTaskRunStream';

const EMPTY: LiveTaskState = { text: {}, toolCalls: [], events: [], iterations: [], variables: {} };

/** Drive the reducer synchronously and return the final state. */
function reduce(events: ReadonlyArray<unknown>): LiveTaskState {
  let state = EMPTY;
  const setLive: any = (updater: any) => {
    state = typeof updater === 'function' ? updater(state) : updater;
  };
  for (const e of events) accumulateLive(setLive, e);
  return state;
}

describe('accumulateLive iteration bucketing', () => {
  it('lazily auto-opens iteration 0 on first block-scoped event', () => {
    const out = reduce([
      { type: 'task_text_delta', block_id: 'b1', content: 'hello' },
    ]);
    expect(out.iterations).toHaveLength(1);
    expect(out.iterations[0]).toMatchObject({
      index: 0, blockId: 'b1', status: 'running', streamText: 'hello',
    });
  });

  it('opens a new bucket on iteration_started', () => {
    const out = reduce([
      { type: 'iteration_started', block_id: 'b1', index: 0 },
    ]);
    expect(out.iterations).toHaveLength(1);
    expect(out.iterations[0]).toMatchObject({
      index: 0, blockId: 'b1', status: 'running', streamText: '',
    });
  });

  it('seals bucket on iteration_completed with status=passed by default', () => {
    const out = reduce([
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      { type: 'task_text_delta', block_id: 'b1', content: 'work' },
      { type: 'iteration_completed', block_id: 'b1', index: 0, duration_ms: 1234, tokens: 50 },
    ]);
    expect(out.iterations).toHaveLength(1);
    expect(out.iterations[0]).toMatchObject({
      index: 0, blockId: 'b1', status: 'passed',
      streamText: 'work', durationMs: 1234, tokens: 50,
    });
  });

  it('seals bucket as failed when iteration_completed carries status=failed', () => {
    const out = reduce([
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      { type: 'iteration_completed', block_id: 'b1', index: 0, status: 'failed', signature: 'objective_not_met' },
    ]);
    expect(out.iterations[0]).toMatchObject({
      status: 'failed', signature: 'objective_not_met',
    });
  });

  it('routes task_text_delta to the running iteration', () => {
    const out = reduce([
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      { type: 'task_text_delta', block_id: 'b1', content: 'first ' },
      { type: 'task_text_delta', block_id: 'b1', content: 'second' },
    ]);
    expect(out.iterations[0].streamText).toBe('first second');
    // Flat text preserved for backward compat
    expect(out.text['b1']).toBe('first second');
  });

  it('routes task_tool_call to the running iteration', () => {
    const out = reduce([
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      { type: 'task_tool_call', block_id: 'b1', tool_name: 'file_read', tool_id: 't1' },
    ]);
    expect(out.iterations[0].toolCalls).toHaveLength(1);
    expect(out.iterations[0].toolCalls[0]).toMatchObject({ tool_name: 'file_read', tool_id: 't1' });
    // Flat toolCalls preserved
    expect(out.toolCalls).toHaveLength(1);
  });

  it('routes block-scoped non-iteration events into iteration timeline', () => {
    const out = reduce([
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      { type: 'block_started', block_id: 'b1' },  // hypothetical block-scoped event
    ]);
    // Two events in the iteration: started + block_started
    expect(out.iterations[0].events).toHaveLength(2);
  });

  it('preserves flat events timeline regardless of bucketing', () => {
    const out = reduce([
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      { type: 'task_text_delta', block_id: 'b1', content: 'x' },
      { type: 'iteration_completed', block_id: 'b1', index: 0 },
    ]);
    expect(out.events).toHaveLength(3);
    expect(out.events.map(e => e.type)).toEqual([
      'iteration_started', 'task_text_delta', 'iteration_completed',
    ]);
  });

  it('does not bucket run-scoped events (run_started, run_completed)', () => {
    const out = reduce([
      { type: 'run_started' },
      { type: 'run_completed' },
    ]);
    expect(out.iterations).toHaveLength(0);
    expect(out.events).toHaveLength(2);
  });

  it('handles repeat: multiple iterations on the same block', () => {
    const out = reduce([
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      { type: 'task_text_delta', block_id: 'b1', content: 'iter0' },
      { type: 'iteration_completed', block_id: 'b1', index: 0 },
      { type: 'iteration_started', block_id: 'b1', index: 1 },
      { type: 'task_text_delta', block_id: 'b1', content: 'iter1' },
      { type: 'iteration_completed', block_id: 'b1', index: 1 },
    ]);
    expect(out.iterations).toHaveLength(2);
    expect(out.iterations[0]).toMatchObject({ index: 0, status: 'passed', streamText: 'iter0' });
    expect(out.iterations[1]).toMatchObject({ index: 1, status: 'passed', streamText: 'iter1' });
    // Flat text concatenates per the existing contract
    expect(out.text['b1']).toBe('iter0iter1');
  });

  it('routes deltas to the current (last running) iteration when prior is sealed', () => {
    const out = reduce([
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      { type: 'iteration_completed', block_id: 'b1', index: 0 },
      { type: 'iteration_started', block_id: 'b1', index: 1 },
      { type: 'task_text_delta', block_id: 'b1', content: 'goes-to-iter1' },
    ]);
    expect(out.iterations[0].streamText).toBe('');
    expect(out.iterations[1].streamText).toBe('goes-to-iter1');
  });

  it('handles repeat_parallel: concurrent iterations of different blocks', () => {
    // Different block_ids — each gets its own iteration timeline
    const out = reduce([
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      { type: 'iteration_started', block_id: 'b2', index: 0 },
      { type: 'task_text_delta', block_id: 'b1', content: 'one' },
      { type: 'task_text_delta', block_id: 'b2', content: 'two' },
    ]);
    expect(out.iterations).toHaveLength(2);
    const b1 = out.iterations.find(it => it.blockId === 'b1')!;
    const b2 = out.iterations.find(it => it.blockId === 'b2')!;
    expect(b1.streamText).toBe('one');
    expect(b2.streamText).toBe('two');
  });

  it('does not double-bucket on re-emitted iteration_started', () => {
    const out = reduce([
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      { type: 'iteration_started', block_id: 'b1', index: 0 },  // duplicate (defensive)
    ]);
    expect(out.iterations).toHaveLength(1);
    // Both started events still appear in the iteration's event timeline
    expect(out.iterations[0].events).toHaveLength(2);
  });

  it('synthesizes bucket on iteration_completed without prior iteration_started', () => {
    // Defensive: if the server emits a completed without a started
    // (network reorder, replay edge case), we still record the result.
    const out = reduce([
      { type: 'iteration_completed', block_id: 'b1', index: 0, duration_ms: 100 },
    ]);
    expect(out.iterations).toHaveLength(1);
    expect(out.iterations[0]).toMatchObject({
      index: 0, blockId: 'b1', status: 'passed', durationMs: 100,
    });
  });
});
