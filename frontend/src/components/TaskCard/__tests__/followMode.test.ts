/**
 * Tests for follow-mode — the policy that decides which block a live
 * tile focuses on its own.
 *
 * The behaviour being pinned, in order of how easy it is to get wrong:
 *
 *   1. Follow the block RECEIVING OUTPUT, not merely one marked running.
 *      Inside a Repeat/Until the executor re-tags task_text_delta with
 *      the LOOP's block id (block_executor's set_task_iteration_context),
 *      so the loop container holds the stream while the inner task is
 *      what reads as 'running'. Following the inner task would focus an
 *      empty panel next to a row that has the actual output.
 *   2. Never fight a manual click. Once pinned, the policy abstains.
 *   3. Degrade to something useful when live buffers are empty (fresh
 *      attach, reload) instead of focusing nothing.
 */

import {
  canResumeFollowing, followTarget, type FocusMode,
} from '../followMode';
import type { TaskRun, TaskRunBlockState } from '../../../types/task_run';

function blockState(
  id: string, status: string,
): TaskRunBlockState {
  return {
    block_id: id, block_type: 'task', status: status as any,
    iteration_summaries: [],
  };
}

function run(states: Record<string, string> = {}): TaskRun {
  const block_states: Record<string, TaskRunBlockState> = {};
  for (const [id, st] of Object.entries(states)) {
    block_states[id] = blockState(id, st);
  }
  return { id: 'run-1', status: 'running', block_states } as TaskRun;
}

// ── rule 1: follow the stream, not the label ────────────────────────

describe('followTarget — follows the block receiving output', () => {
  it('picks a block that is both streaming and running', () => {
    expect(followTarget(run({ t1: 'running' }), {
      text: { t1: 'some output' },
      blockStatuses: { t1: 'running' },
    })).toBe('t1');
  });

  it('picks the LOOP container, not the inner task, during an iteration', () => {
    // The shape that makes this rule load-bearing: the executor re-tags
    // deltas with the loop's id, so `inner` is 'running' but has no text
    // and `loop-1` is where the output actually lands.  Focusing `inner`
    // would show an empty panel while the visible stream sat elsewhere.
    expect(followTarget(
      run({ 'loop-1': 'running', inner: 'running' }),
      {
        text: { 'loop-1': 'iteration output' },
        blockStatuses: { 'loop-1': 'running', inner: 'running' },
      },
    )).toBe('loop-1');
  });

  it('ignores a block with text whose stage has already finished', () => {
    // Stale text from a completed stage must not out-rank live work:
    // `t1` streamed and finished, `t2` is running now.
    expect(followTarget(
      run({ t1: 'done', t2: 'running' }),
      {
        text: { t1: 'old output', t2: 'new output' },
        blockStatuses: { t1: 'done', t2: 'running' },
      },
    )).toBe('t2');
  });

  it('takes the most recent when several branches stream concurrently', () => {
    // Parallel: any answer beats the whole-run view, and "most recently
    // keyed" is the closest available proxy for "what is happening now".
    expect(followTarget(
      run({ a: 'running', b: 'running' }),
      {
        text: { a: 'A', b: 'B' },
        blockStatuses: { a: 'running', b: 'running' },
      },
    )).toBe('b');
  });
});

// ── rule 3: useful fallbacks ────────────────────────────────────────

describe('followTarget — fallbacks when nothing has streamed', () => {
  it('falls back to a running block before any text arrives', () => {
    // Early in a run, or straight after attaching: pointing at live work
    // beats pointing at an artifact that does not exist yet.
    expect(followTarget(run({ t1: 'running' }), {
      text: {},
      blockStatuses: { t1: 'running' },
    })).toBe('t1');
  });

  it('falls back to the persisted record when live buffers are empty', () => {
    // A reload mid-run: the WS has delivered nothing this session, but
    // the snapshot knows a block is in flight.
    expect(followTarget(run({ t1: 'done', t2: 'running' }), {
      text: {},
      blockStatuses: {},
    })).toBe('t2');
  });

  it('returns null when no block is running anywhere', () => {
    expect(followTarget(run({ t1: 'done' }), {
      text: {}, blockStatuses: {},
    })).toBeNull();
  });

  it('returns null for a missing run rather than throwing', () => {
    expect(followTarget(null, { text: {}, blockStatuses: {} })).toBeNull();
    expect(followTarget(undefined, { text: {}, blockStatuses: {} })).toBeNull();
  });

  it('tolerates absent live maps', () => {
    // Defensive: a caller passing a partially-built live object must not
    // crash the tile.
    expect(followTarget(run({ t1: 'running' }), {} as any)).toBe('t1');
  });

  it('ignores an empty text entry, which is not evidence of streaming', () => {
    // A bucket keyed but empty (opened, nothing yet) must not out-rank
    // the running-block fallback — it carries no output to show.
    expect(followTarget(
      run({ t1: 'running', t2: 'running' }),
      { text: { t1: '' }, blockStatuses: { t1: 'running', t2: 'running' } },
    )).toBe('t2');
  });
});

// ── rule 2: the resume affordance ───────────────────────────────────

describe('canResumeFollowing', () => {
  it('offers the way back when pinned on a live run', () => {
    expect(canResumeFollowing('pinned', true)).toBe(true);
  });

  it('does not offer it while already following', () => {
    expect(canResumeFollowing('following', true)).toBe(false);
  });

  it('does not offer it on a finished run', () => {
    // There is no "now" to follow once the run is over, so the control
    // would promise something it cannot deliver.
    expect(canResumeFollowing('pinned', false)).toBe(false);
    expect(canResumeFollowing('following', false)).toBe(false);
  });

  it.each(['following', 'pinned'] as FocusMode[])(
    'is never true for %s when the run is not live', (mode) => {
      expect(canResumeFollowing(mode, false)).toBe(false);
    });
});
