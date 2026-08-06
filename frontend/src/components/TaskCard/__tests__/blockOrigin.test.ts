/**
 * Tests for blockOrigin / formatCompletedAt (B9).
 *
 * These answer the reported confusion directly: "I can click around a
 * whole bunch and not be able to tell what is from the past run or the
 * current run."  The root cause is that the focused-block panel showed
 * STATE without WHEN.
 *
 * The load-bearing inference is that a REPLAYED block is exactly
 * ``status === 'skipped'`` WITH an artifact — the shape
 * ``block_executor._skip_on_resume`` writes when it replays a prior
 * attempt's recorded result.  A skipped block with NO artifact is a
 * genuine never-ran (on_failure="stop"), and conflating the two would
 * either claim a replay that never happened or hide one that did.
 */

import { blockOrigin, formatCompletedAt } from '../partialOutcome';
import type { TaskRun, TaskRunBlockState } from '../../../types/task_run';
import type { Artifact } from '../../../types/task_card';

const artifact = (summary = 'prior result'): Artifact => ({
  summary, decisions: [], outputs: [], tokens: 0, tool_calls: 0,
  duration_ms: 0, created_at: 0,
} as Artifact);

function state(
  over: Partial<TaskRunBlockState> = {},
): TaskRunBlockState {
  return {
    block_id: 'b1', block_type: 'task', status: 'done',
    iteration_summaries: [], ...over,
  } as TaskRunBlockState;
}

function run(over: Partial<TaskRun> = {}): TaskRun {
  return {
    id: 'run-1', card_id: 'c1', status: 'done', block_states: {},
    cancel_requested: false, pause_requested: false,
    total_tokens: 0, total_tool_calls: 0,
    created_at: 0, updated_at: 0, ...over,
  } as TaskRun;
}

// ── replay detection ────────────────────────────────────────────────

describe('blockOrigin replay detection', () => {
  it('flags a skipped block WITH an artifact as replayed', () => {
    // This is the shape _skip_on_resume writes: the block did not run in
    // THIS attempt, but it carries the earlier attempt's recorded result
    // so later stages see the same deck state.
    const o = blockOrigin(
      run({ resume_kind: 'retry_from', attempt: 2 }),
      state({ status: 'skipped', artifact: artifact() }),
      'skipped',
    );
    expect(o.replayed).toBe(true);
  });

  it('does NOT flag a skipped block WITHOUT an artifact', () => {
    // A genuine never-ran (a sibling skipped under on_failure="stop").
    // Calling this "replayed from an earlier attempt" would invent a
    // result the user could go looking for and never find.
    const o = blockOrigin(
      run({ resume_kind: 'retry_from', attempt: 2 }),
      state({ status: 'skipped', artifact: null }),
      'skipped',
    );
    expect(o.replayed).toBe(false);
  });

  it('does not flag a normally-executed block', () => {
    const o = blockOrigin(
      run({ resume_kind: 'retry_from', attempt: 2 }),
      state({ status: 'done', artifact: artifact() }),
      'done',
    );
    expect(o.replayed).toBe(false);
  });

  it('does not flag a failed block that ran here', () => {
    const o = blockOrigin(
      run(), state({ status: 'failed', artifact: artifact() }), 'failed',
    );
    expect(o.replayed).toBe(false);
  });

  it('handles a missing block state without throwing', () => {
    // The map can focus a block the run has no state for (a queued block
    // on a run that died early); the panel must still render.
    const o = blockOrigin(run(), undefined, 'queued');
    expect(o.replayed).toBe(false);
    expect(o.displayStatus).toBe('queued');
  });

  it('handles a missing run', () => {
    const o = blockOrigin(null, state(), 'done');
    expect(o.attempt).toBe(1);
    expect(o.replayed).toBe(false);
  });
});

// ── display status ──────────────────────────────────────────────────

describe('blockOrigin display status', () => {
  it('relabels a replayed block instead of showing "skipped"', () => {
    // 'skipped' is true of this run's executor but reads as "no result"
    // to someone looking straight at a result — self-contradictory, and
    // the specific thing that made the panel unreadable on a resume.
    const o = blockOrigin(
      run({ resume_kind: 'continue_from', attempt: 3 }),
      state({ status: 'skipped', artifact: artifact() }),
      'skipped',
    );
    expect(o.displayStatus).toBe('replayed');
    expect(o.displayStatus).not.toBe('skipped');
  });

  it('leaves a genuine skip labelled skipped', () => {
    const o = blockOrigin(
      run(), state({ status: 'skipped', artifact: null }), 'skipped',
    );
    expect(o.displayStatus).toBe('skipped');
  });

  it('passes the live status through untouched for a normal block', () => {
    // The live status (from block_status events) is fresher than the
    // persisted one, so it must not be overwritten by the record.
    const o = blockOrigin(
      run({ status: 'running' }), state({ status: 'queued' }), 'running',
    );
    expect(o.displayStatus).toBe('running');
  });
});

// ── attempt + timing ────────────────────────────────────────────────

describe('blockOrigin attempt and timing', () => {
  it('reports the attempt ordinal of the viewed run', () => {
    const o = blockOrigin(run({ attempt: 4 }), state(), 'done');
    expect(o.attempt).toBe(4);
  });

  it('defaults a pre-lineage record to attempt 1', () => {
    // Runs written before lineage tracking have no ``attempt``; they
    // were all initial launches, so 1 is the truthful default.
    const o = blockOrigin(run({ attempt: undefined }), state(), 'done');
    expect(o.attempt).toBe(1);
  });

  it('surfaces completed_at when recorded', () => {
    const o = blockOrigin(
      run(), state({ completed_at: 1_700_000_000 }), 'done',
    );
    expect(o.completedAt).toBe(1_700_000_000);
  });

  it('reports null when the block has no completion time', () => {
    const o = blockOrigin(run(), state({ completed_at: null }), 'running');
    expect(o.completedAt).toBeNull();
  });
});

// ── formatCompletedAt ───────────────────────────────────────────────

describe('formatCompletedAt', () => {
  it('formats a real timestamp as wall-clock time', () => {
    // Absolute, not relative: "2 minutes ago" is the SAME phrase for a
    // replayed result and a fresh one when a resume lands quickly, which
    // defeats the whole purpose of showing a time at all.
    const out = formatCompletedAt(1_700_000_000);
    expect(out).toBeTruthy();
    expect(out).toMatch(/\d/);
  });

  it('returns null for a missing timestamp rather than a fake one', () => {
    expect(formatCompletedAt(null)).toBeNull();
    expect(formatCompletedAt(undefined)).toBeNull();
  });

  it('returns null for an unparseable value instead of "Invalid Date"', () => {
    expect(formatCompletedAt(Number.NaN)).toBeNull();
  });
});
