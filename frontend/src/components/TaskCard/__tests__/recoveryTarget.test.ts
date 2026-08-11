/**
 * Tests for recoveryTarget — which block a stopped run resumes from.
 *
 * The null cases carry as much weight as the hits: a banner offering
 * "resume from the failure" on a run that had no failure would be
 * inventing a target, and the wrong target silently re-runs the wrong
 * work.
 */

import { recoveryTarget } from '../recoveryTarget';
import type { TaskRun, TaskRunBlockState } from '../../../types/task_run';

const bs = (
  id: string, status: string, completedAt?: number,
): TaskRunBlockState => ({
  block_id: id, block_type: 'task', status: status as any,
  completed_at: completedAt ?? null,
  iteration_summaries: [],
});

const mkRun = (over: Partial<TaskRun> = {}): TaskRun => ({
  id: 'r1', card_id: 'c1', status: 'failed',
  cancel_requested: false, pause_requested: false,
  block_states: {}, total_tokens: 0, total_tool_calls: 0,
  created_at: 0, updated_at: 0,
  ...over,
});

describe('recoveryTarget', () => {
  it('returns null with no run', () => {
    expect(recoveryTarget(null)).toBeNull();
    expect(recoveryTarget(undefined)).toBeNull();
  });

  it('prefers held_at_block_id on a held run', () => {
    // More precise than inferring from status, and an infra fault may
    // leave no block marked failed at all.
    const run = mkRun({
      status: 'held', held_at_block_id: 'b3',
      block_states: { b1: bs('b1', 'done') },
    });
    expect(recoveryTarget(run)).toEqual({ blockId: 'b3', reason: 'held' });
  });

  it('picks the earliest failed block on a failed run', () => {
    // Under on_failure=stop the first failure ended the run; later
    // failed entries are containers propagating it upward, so resuming
    // from one of those would target a wrapper.
    const run = mkRun({
      block_states: {
        b1: bs('b1', 'done', 10),
        b2: bs('b2', 'failed', 20),
        wrap: bs('wrap', 'failed', 30),
      },
    });
    expect(recoveryTarget(run)).toEqual({ blockId: 'b2', reason: 'failed' });
  });

  it('still reports reason=held when a held run lacks held_at_block_id', () => {
    // Older records have no held_at_block_id.  The reason describes why
    // the RUN stopped, not how we located the block — an infra fault
    // asks the user to fix infrastructure either way.
    const run = mkRun({
      status: 'held',
      block_states: { b1: bs('b1', 'failed', 5) },
    });
    expect(recoveryTarget(run)).toEqual({ blockId: 'b1', reason: 'held' });
  });

  it('returns null for a done run', () => {
    // Nothing stopped it, so there is no natural resume point.  The
    // map's per-row buttons still allow a deliberate re-run.
    const run = mkRun({
      status: 'done', block_states: { b1: bs('b1', 'done') },
    });
    expect(recoveryTarget(run)).toBeNull();
  });

  it('returns null when nothing failed and nothing was held', () => {
    const run = mkRun({
      status: 'cancelled',
      block_states: { b1: bs('b1', 'done'), b2: bs('b2', 'skipped') },
    });
    expect(recoveryTarget(run)).toBeNull();
  });

  it('returns null with no block_states at all', () => {
    // Nothing to replay, so resuming is indistinguishable from a
    // restart and the banner would be a lie.
    expect(recoveryTarget(mkRun({ block_states: {} }))).toBeNull();
  });

  it('finds the failure on a cancelled run that had one', () => {
    const run = mkRun({
      status: 'cancelled',
      block_states: { b1: bs('b1', 'failed', 7) },
    });
    expect(recoveryTarget(run)).toEqual({ blockId: 'b1', reason: 'failed' });
  });
});
