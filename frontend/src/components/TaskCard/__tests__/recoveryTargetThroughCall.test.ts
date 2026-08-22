/**
 * recoveryTarget and a hold inside a called card.
 *
 * This file previously asserted the OPPOSITE of what it asserts now, and
 * the reversal is the point worth recording.
 *
 * The first attempt at fixing "resume from a hold inside a Call" made the
 * client substitute the enclosing Call block's id for the held callee
 * block, because the executor's resume gate could not descend into a Call
 * (a Call has an empty ``body``, so ``_subtree_contains`` reported the
 * target as absent and the whole phase was replayed).  Retrying the Call
 * block was the only thing that would run.
 *
 * That "fix" was rejected on cost.  Re-entering a called card from its own
 * start re-runs everything inside it — on the study that prompted this,
 * a 20-wide auditor fan-out whose 19 successful iterations represented 14
 * hours of work.  A control labelled "resume" that silently re-pays 14
 * hours is worse than one that refuses.
 *
 * So the executor learned to descend instead (``resume_call_chain`` +
 * ``_subtree_contains_any`` in block_executor, ``locate_block`` in
 * resume_targets), and the client's job reverted to the simple one:
 * hand the server ``held_at_block_id`` verbatim and let it resolve.
 *
 * These tests therefore assert NO substitution.  If a future change
 * reintroduces client-side normalization, they fail — which is what
 * should happen, because that change would re-create the cost bug.
 * Label resolution for a callee block is a separate concern and lives in
 * ``findBlockInRun`` (see findBlockInRun.test.ts).
 */

import { recoveryTarget } from '../recoveryTarget';
import type { TaskRun } from '../../../types/task_run';

const CALLEE_ROOT = {
  id: 'b-cl1-root',
  block_type: 'group',
  name: 'CL1',
  body: [
    { id: 'b-recon', block_type: 'task', name: 'Recon', body: [] },
    {
      id: 'b-cf96c4e2',
      block_type: 'repeat',
      name: 'Stage 2: Parallel subsystem auditors',
      body: [{ id: 'b-auditor', block_type: 'task', name: 'Audit', body: [] }],
    },
  ],
} as any;

function run(over: Partial<TaskRun> = {}): TaskRun {
  return {
    id: 'r1',
    card_id: 'c1',
    status: 'held',
    held_at_block_id: 'b-cf96c4e2',
    cancel_requested: false,
    pause_requested: false,
    block_states: {},
    total_tokens: 0,
    total_tool_calls: 0,
    call_snapshots: {
      'call-p1': { target: 'CL1', kind: 'card', root: CALLEE_ROOT },
    },
    created_at: 0,
    updated_at: 0,
    ...over,
  } as TaskRun;
}

describe('recoveryTarget with a hold inside a Call', () => {
  it('returns the CALLEE block id unchanged, not the Call block', () => {
    const t = recoveryTarget(run())!;
    expect(t.blockId).toBe('b-cf96c4e2');
    expect(t.reason).toBe('held');
  });

  it('does not substitute even though the id is absent from the card tree', () => {
    // The distinguishing case.  ``b-cf96c4e2`` exists only inside
    // ``call_snapshots``, so a client tempted to "resolve" it would reach
    // for the Call block.  The server owns that resolution now, and it
    // reaches the block itself rather than the phase wrapping it.
    const t = recoveryTarget(run())!;
    expect(t.blockId).not.toBe('call-p1');
  });

  it('carries no insideCall / normalization metadata', () => {
    // The withdrawn design attached the enclosing call and its block ids
    // so the banner could warn that the callee would be re-run.  With
    // descent implemented there is nothing to warn about: the callee's
    // banked iterations are replayed, so the plain "N will be replayed
    // from record" copy is true as written.
    const t = recoveryTarget(run())! as Record<string, unknown>;
    expect(t.insideCall).toBeUndefined();
  });

  it('is unaffected by call_snapshots being absent', () => {
    const t = recoveryTarget(run({ call_snapshots: undefined }))!;
    expect(t.blockId).toBe('b-cf96c4e2');
  });

  it('still prefers held_at_block_id over a failed block scan', () => {
    // Ordering guard: an infra fault may leave a callee block marked
    // 'failed' as well, and held_at_block_id is the more precise record
    // of where the executor actually unwound.
    const t = recoveryTarget(run({
      block_states: {
        'b-auditor': {
          block_id: 'b-auditor', block_type: 'task', status: 'failed',
          completed_at: 1, iteration_summaries: [],
        } as any,
      },
    }))!;
    expect(t.blockId).toBe('b-cf96c4e2');
  });

  it('falls back to the failed block when no hold was recorded', () => {
    // Proves the test above is asserting precedence rather than passing
    // because the fallback never fires.
    const t = recoveryTarget(run({
      status: 'failed',
      held_at_block_id: null,
      block_states: {
        'b-auditor': {
          block_id: 'b-auditor', block_type: 'task', status: 'failed',
          completed_at: 1, iteration_summaries: [],
        } as any,
      },
    }))!;
    expect(t.blockId).toBe('b-auditor');
    expect(t.reason).toBe('failed');
  });
});
