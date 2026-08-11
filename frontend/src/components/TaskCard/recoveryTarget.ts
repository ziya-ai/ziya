/**
 * recoveryTarget — which block a stopped run should be resumed from.
 *
 * Exists because the per-row resume buttons in the run map answered the
 * wrong question.  They offer "resume from THIS block" on every row
 * equally, which is correct as a capability and useless as guidance: a
 * user arriving at a run that died wants the ONE block that ended it,
 * not N identical choices.  Worse, the map is suppressed entirely for a
 * single-node card (``rows.length <= 1``), so on the commonest card
 * shape those buttons have no host at all and the only visible control
 * is Restart — which discards every completed block.
 *
 * This picks the natural target so a tile-level banner can name it.
 * The per-row buttons remain for deliberate, non-obvious choices (e.g.
 * re-running from earlier than the failure).
 */

import type { TaskRun } from '../../types/task_run';
import { firstFailedBlock } from './partialOutcome';

export interface RecoveryTarget {
  blockId: string;
  /**
   * Why this block was chosen.  Drives the wording, because the two
   * ask for different responses: an infrastructure fault means "fix
   * the credential and continue", a failure means "the work itself
   * went wrong".
   */
  reason: 'held' | 'failed';
}

/**
 * The block a stopped run should resume from, or null when there is no
 * natural one.
 *
 * Null cases are deliberate rather than incidental:
 *   - A ``done`` run stopped at no block; offering "resume from the
 *     failure" would be inventing one.  The map's per-row buttons still
 *     allow a deliberate re-run.
 *   - A run with no ``block_states`` never recorded a stage, so there is
 *     nothing to replay and resuming is indistinguishable from a
 *     restart.
 *
 * ``held_at_block_id`` outranks the failed-block scan because a held run
 * records exactly where its executor unwound, which is more precise
 * than inferring from status — and an infra fault may leave no block
 * marked ``failed`` at all.
 */
export function recoveryTarget(
  run: TaskRun | null | undefined,
): RecoveryTarget | null {
  if (!run) return null;
  if (run.status === 'held' && run.held_at_block_id) {
    return { blockId: run.held_at_block_id, reason: 'held' };
  }
  const failed = firstFailedBlock(run);
  if (failed?.block_id) {
    // A held run whose held_at_block_id is absent (older record) still
    // reads as 'held': the reason describes the RUN's stop cause, not
    // how we located the block.
    return {
      blockId: failed.block_id,
      reason: run.status === 'held' ? 'held' : 'failed',
    };
  }
  return null;
}
