/**
 * Tests for replayed-text accumulation in ``accumulateLive``.
 *
 * The task-run relay pushes RAW ``task_text_delta`` events to already
 * connected clients, but records history in a COLLAPSED form: adjacent
 * deltas for the same block are folded into a single
 * ``task_text_delta_run`` entry whose ``content`` is the concatenation
 * of the originals (app/agents/task_run_stream_relay.py ``_record``,
 * covered by tests/test_task_run_stream_relay_history.py).
 *
 * A client that attaches mid-run therefore receives:
 *   1. the collapsed replay of everything streamed before it attached,
 *   2. then raw deltas for everything after.
 *
 * The reducer originally handled only the raw type, so all
 * pre-connection text was dropped from ``live.text`` and from the
 * per-iteration ``streamText`` buckets — the Live tab looked as though
 * the run had started mid-sentence (or produced nothing at all) after
 * any reload or late attach, even though the Events tab still listed
 * the delta traffic.
 *
 * These tests pin both surfaces, and pin that replay is byte-exact:
 * the collapsed form must reassemble to precisely what the raw deltas
 * would have produced, since it is the same text.
 */

import { accumulateLive, type LiveTaskState } from '../useTaskRunStream';

const EMPTY: LiveTaskState = {
  text: {}, toolCalls: [], events: [], iterations: [],
  variables: {}, blockStatuses: {},
};

/** Drive the reducer synchronously and return the final state. */
function reduce(events: ReadonlyArray<unknown>): LiveTaskState {
  let state = EMPTY;
  const setLive: any = (updater: any) => {
    state = typeof updater === 'function' ? updater(state) : updater;
  };
  for (const e of events) accumulateLive(setLive, e);
  return state;
}

describe('accumulateLive — collapsed replay events', () => {
  it('accumulates task_text_delta_run into flat text', () => {
    const out = reduce([
      { type: 'task_text_delta_run', block_id: 'b1', count: 3, content: 'hello world' },
    ]);
    expect(out.text.b1).toBe('hello world');
  });

  it('accumulates task_text_delta_run into the iteration bucket', () => {
    const out = reduce([
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      { type: 'task_text_delta_run', block_id: 'b1', count: 2, content: 'replayed text' },
    ]);
    expect(out.iterations).toHaveLength(1);
    expect(out.iterations[0].streamText).toBe('replayed text');
  });

  it('lazily auto-opens iteration 0 for a replayed run, like a raw delta', () => {
    // Covers a bare (non-repeat) task block, which emits no
    // iteration_started — the replay must still land in a bucket.
    const out = reduce([
      { type: 'task_text_delta_run', block_id: 'b9', count: 5, content: 'body' },
    ]);
    expect(out.iterations).toHaveLength(1);
    expect(out.iterations[0].index).toBe(0);
    expect(out.iterations[0].blockId).toBe('b9');
    expect(out.iterations[0].streamText).toBe('body');
  });

  it('concatenates a replayed prefix with subsequent live raw deltas', () => {
    // The exact mid-run attach sequence: collapsed history, then live.
    const out = reduce([
      { type: 'task_text_delta_run', block_id: 'b1', count: 2, content: 'Alpha beta ' },
      { type: 'task_text_delta', block_id: 'b1', content: 'gamma ' },
      { type: 'task_text_delta', block_id: 'b1', content: 'delta.' },
    ]);
    expect(out.text.b1).toBe('Alpha beta gamma delta.');
    expect(out.iterations[0].streamText).toBe('Alpha beta gamma delta.');
  });

  it('reassembles replayed text byte-exactly, preserving newlines', () => {
    // The relay concatenates verbatim, so a collapsed entry must be
    // indistinguishable from the raw deltas it folded — including the
    // newlines that carry the output's line structure.
    const deltas = ['Line one.\n', 'Line two.\n', 'Line three.'];
    const raw = reduce(
      deltas.map(content => ({ type: 'task_text_delta', block_id: 'b1', content })),
    );
    const replayed = reduce([
      {
        type: 'task_text_delta_run', block_id: 'b1',
        count: deltas.length, content: deltas.join(''),
      },
    ]);
    expect(replayed.text.b1).toBe(raw.text.b1);
    expect(replayed.text.b1).toBe('Line one.\nLine two.\nLine three.');
    expect((replayed.text.b1.match(/\n/g) ?? []).length).toBe(2);
  });

  it('keeps replayed text separated per block', () => {
    const out = reduce([
      { type: 'task_text_delta_run', block_id: 'b1', count: 1, content: 'first' },
      { type: 'task_text_delta_run', block_id: 'b2', count: 1, content: 'second' },
    ]);
    expect(out.text).toEqual({ b1: 'first', b2: 'second' });
  });

  it('ignores a replay entry with no content or no block_id', () => {
    const out = reduce([
      { type: 'task_text_delta_run', block_id: 'b1', count: 0, content: '' },
      { type: 'task_text_delta_run', count: 1, content: 'orphan' },
    ]);
    expect(out.text).toEqual({});
  });

  it('still records replay entries on the flat event timeline', () => {
    // The Events tab reads this timeline and normalises the collapsed
    // shape itself (components/TaskCard/eventLog.ts), so the reducer
    // must not consume the event out of it.
    const out = reduce([
      { type: 'task_text_delta_run', block_id: 'b1', count: 4, content: 'x' },
    ]);
    expect(out.events).toHaveLength(1);
    expect(out.events[0].type).toBe('task_text_delta_run');
  });
});
