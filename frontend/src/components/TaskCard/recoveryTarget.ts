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

import type { IterationSummary, TaskRun } from '../../types/task_run';
import type { Block } from '../../types/task_card';
// Reused rather than reimplemented: the loop-type list must stay in step
// with the containers that push a binding frame, and a second copy of it
// here would drift silently.  No cycle — runMapModel imports only types.
import { isLoopBlock } from './runMapModel';
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

/**
 * Iteration a mid-loop resume of ``block`` is expected to restart at, or
 * null when there is no ordinal to name.
 *
 * Exists because RecoveryTarget carries a block id, and a loop is one
 * block: the banner could only say "resumes at <loop>", which describes a
 * resume preserving 22 iterations identically to one discarding them.
 * That ambiguity is what let the discard go unnoticed.
 *
 * A PREDICTION of a server-side decision — ``serial_replay_prefix`` in
 * app/utils/resume_targets.py — and deliberately mirrors its rule rather
 * than approximating it:
 *
 *   - a contiguous PREFIX from 0, not a set of banked indices: a serial
 *     loop's ``{{previous}}`` binds the immediate predecessor, so a gap
 *     cannot be skipped past;
 *   - the walk stops at the first index that is not a RETAINED PASS, for
 *     two distinct reasons — a failed iteration is the work being redone,
 *     and a pass past the 50-artifact retention cap would replay as an
 *     empty ``{{previous}}``;
 *   - ``replayed`` records COUNT, unlike in progressCounts: that
 *     exclusion is about not crediting this attempt with a prior one's
 *     work, whereas here the question is what can be replayed again.
 *
 * Two divergences are known, and are why the banner words this as where
 * execution resumes rather than as a promise:
 *
 *   1. The server also consults ``resume_iteration_artifacts``, which is
 *      not on the wire, so a chained resume can land LATER than predicted
 *      (the banner under-states — the safe direction).
 *   2. The server truncates at the first artifact missing from disk, so a
 *      record/disk disagreement can land EARLIER.
 *
 * Null rather than 0 in every inapplicable case, so a caller cannot
 * render "resumes at #0" and dress a from-scratch loop re-run as a
 * mid-loop resume.  A parallel loop is inapplicable by design: the server
 * resumes it by index set, and its iterations receive no ``previous``, so
 * an ordinal would assert an ordering that does not exist.
 */
export function bankedIterationPrefix(
  run: TaskRun | null | undefined,
  block: Block | null | undefined,
): number | null {
  if (!run || !block) return null;
  if (!isLoopBlock(block) || block.repeat_parallel) return null;
  const summaries = run.block_states?.[block.id]?.iteration_summaries;
  if (!summaries?.length) return null;
  // Keyed by the index FIELD, never by array position: summaries are
  // appended as iterations seal, which under a resume is not index order.
  const byIndex = new Map<number, IterationSummary>();
  for (const s of summaries) {
    if (Number.isInteger(s.index) && s.index >= 0) byIndex.set(s.index, s);
  }
  let start = 0;
  for (;;) {
    const s = byIndex.get(start);
    // has_artifact absent means "retained", matching the server's
    // default — records written before the field existed would otherwise
    // predict a prefix of 0 and lose the label entirely.
    if (!s || s.status !== 'passed' || s.has_artifact === false) break;
    start += 1;
  }
  return start > 0 ? start : null;
}