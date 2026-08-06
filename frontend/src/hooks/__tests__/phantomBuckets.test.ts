/**
 * Tests for the iteration-bucket auto-open gate (B11).
 *
 * ``accumulateLive`` lazily opens an "Iteration 0" bucket when a
 * block-scoped event arrives for a block that has none — the path that
 * lets a bare Task block (one not wrapped in a Repeat/Until, so it never
 * emits ``iteration_started``) still show its streamed output.
 *
 * The gate used to be "any event carrying a block_id", which is far too
 * wide: ``block_status`` is emitted for EVERY structural block, so a
 * Repeat / Group / Parallel / Until minted a phantom bucket for a block
 * that never runs a task directly.  Those buckets are empty by
 * construction, and survived only because the Live and Tools tabs filter
 * empty ones out — the Events tab does not, and ``live.iterations`` was
 * carrying entries that are not iterations either way.
 *
 * The rule under test: only TASK-scoped traffic may CREATE a bucket.
 * Routing into an existing one is unrestricted, and container status must
 * still reach the flat timeline and ``blockStatuses`` — the run map is
 * driven from the latter, so dropping it would blank the map.
 */

import { accumulateLive, type LiveTaskState } from '../useTaskRunStream';

const EMPTY: LiveTaskState = {
  text: {}, toolCalls: [], events: [], iterations: [],
  variables: {}, blockStatuses: {},
};

function drive(events: unknown[]): LiveTaskState {
  let state: LiveTaskState = EMPTY;
  for (const e of events) {
    accumulateLive((updater) => {
      state = typeof updater === 'function' ? updater(state) : updater;
    }, e);
  }
  return state;
}

const containerStatus = (id: string, type: string, status = 'running') =>
  ({ type: 'block_status', block_id: id, block_type: type, status });

// ── containers must not mint buckets ────────────────────────────────

describe('container block_status does not open an iteration bucket', () => {
  it.each(['repeat', 'group', 'parallel', 'until', 'schedule'])(
    'ignores a %s container reporting its own status', (blockType) => {
      const s = drive([containerStatus(`${blockType}-1`, blockType)]);
      expect(s.iterations).toHaveLength(0);
    });

  it('does not accumulate one phantom bucket per container in a nested card', () => {
    // A realistic tree: group > repeat > (task).  Only the task should
    // ever produce a bucket, and only once it actually streams.
    const s = drive([
      containerStatus('group-1', 'group'),
      containerStatus('repeat-1', 'repeat'),
      containerStatus('task-1', 'task'),
    ]);
    expect(s.iterations).toHaveLength(0);
  });

  it('still records container status where the run map reads it', () => {
    // The gate protects the ITERATION model only — dropping the status
    // itself would blank the run map, a far worse outcome.
    const s = drive([
      containerStatus('repeat-1', 'repeat', 'running'),
      containerStatus('group-1', 'group', 'done'),
    ]);
    expect(s.blockStatuses).toEqual({ 'repeat-1': 'running', 'group-1': 'done' });
  });

  it('still records container status on the flat event timeline', () => {
    const s = drive([containerStatus('repeat-1', 'repeat')]);
    expect(s.events.map(e => e.type)).toEqual(['block_status']);
  });
});

// ── the lazy path must still work for a bare task ───────────────────

describe('task-scoped traffic still opens a bucket lazily', () => {
  it('opens one for a bare task that streams without iteration_started', () => {
    // The case the lazy path exists for: a top-level Task block emits no
    // iteration_started, so without the auto-open its output would have
    // nowhere to render.
    const s = drive([
      { type: 'task_text_delta', block_id: 'task-1', content: 'working' },
    ]);
    expect(s.iterations).toHaveLength(1);
    expect(s.iterations[0]).toMatchObject({
      blockId: 'task-1', index: 0, status: 'running', streamText: 'working',
    });
  });

  it('opens one on a tool call alone, with no prose first', () => {
    const s = drive([
      { type: 'task_tool_call', block_id: 'task-1', tool_name: 'grep' },
    ]);
    expect(s.iterations).toHaveLength(1);
    expect(s.iterations[0].toolCalls).toHaveLength(1);
  });

  it("opens one on the relay's collapsed replay shape too", () => {
    // A client attaching mid-run receives task_text_delta_run, not raw
    // deltas; excluding it would leave a reloaded run with empty bodies.
    const s = drive([
      { type: 'task_text_delta_run', block_id: 'task-1',
        content: 'replayed', count: 3 },
    ]);
    expect(s.iterations).toHaveLength(1);
    expect(s.iterations[0].streamText).toBe('replayed');
  });

  it('opens one on task_started', () => {
    const s = drive([{ type: 'task_started', block_id: 'task-1' }]);
    expect(s.iterations).toHaveLength(1);
  });

  it('routes a status event into an ALREADY-open bucket', () => {
    // Creation is gated; ROUTING is not.  Once a bucket exists for a
    // block, that block's own status transitions are legitimate content
    // for it, and dropping them would lose real lifecycle detail.
    const s = drive([
      { type: 'iteration_started', block_id: 'b1', index: 0 },
      containerStatus('b1', 'task', 'running'),
    ]);
    expect(s.iterations).toHaveLength(1);
    expect(s.iterations[0].events.map(e => e.type))
      .toEqual(['iteration_started', 'block_status']);
  });
});

// ── explicit iteration lifecycle is unaffected ──────────────────────

describe('explicit iteration events are unaffected by the gate', () => {
  it('still opens a bucket on iteration_started', () => {
    const s = drive([
      { type: 'iteration_started', block_id: 'inner', index: 0 },
    ]);
    expect(s.iterations).toHaveLength(1);
    expect(s.iterations[0].index).toBe(0);
  });

  it('still seals on iteration_completed', () => {
    const s = drive([
      { type: 'iteration_started', block_id: 'inner', index: 0 },
      { type: 'iteration_completed', block_id: 'inner', index: 0,
        status: 'passed', duration_ms: 120, tokens: 40 },
    ]);
    expect(s.iterations[0]).toMatchObject({
      status: 'passed', durationMs: 120, tokens: 40,
    });
  });

  it('tracks a multi-iteration loop without extra container buckets', () => {
    // The end-to-end shape: the loop container reports its own status
    // around two real iterations of its inner task.
    const s = drive([
      containerStatus('repeat-1', 'repeat', 'running'),
      { type: 'iteration_started', block_id: 'repeat-1', index: 0 },
      { type: 'task_text_delta', block_id: 'repeat-1', content: 'one' },
      { type: 'iteration_completed', block_id: 'repeat-1', index: 0, status: 'passed' },
      { type: 'iteration_started', block_id: 'repeat-1', index: 1 },
      { type: 'task_text_delta', block_id: 'repeat-1', content: 'two' },
      { type: 'iteration_completed', block_id: 'repeat-1', index: 1, status: 'passed' },
      containerStatus('repeat-1', 'repeat', 'done'),
    ]);
    // Exactly two — the container's own status events add none.
    expect(s.iterations).toHaveLength(2);
    expect(s.iterations.map(i => i.streamText)).toEqual(['one', 'two']);
  });
});

// ── the phantom shape, stated directly ──────────────────────────────

describe('phantom bucket regression', () => {
  it('never creates a bucket with no content from status alone', () => {
    // The observable signature of the bug: buckets that exist but carry
    // no text and no tool calls, because nothing ever ran under them.
    const s = drive([
      containerStatus('group-1', 'group'),
      containerStatus('repeat-1', 'repeat'),
      containerStatus('until-1', 'until'),
    ]);
    const empty = s.iterations.filter(
      it => !it.streamText && it.toolCalls.length === 0,
    );
    expect(empty).toHaveLength(0);
  });
});
