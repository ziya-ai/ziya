/**
 * runControls — derives which run controls apply, and whether a run is
 * under manual (held) control.
 *
 * Extracted from TaskCardInlineTile because the central rule is
 * counter-intuitive and easy to regress: a stepped run's ``status``
 * legitimately blips ``paused → running → paused`` on every step,
 * because the executor really is running while it crosses the block
 * the credit bought.  So a "held" indicator keyed on
 * ``status === 'paused'`` flickers, and — worse — the Resume button
 * disappears mid-step, stranding the user in step mode with no way
 * back to run-to-completion.
 *
 * The stable signal is ``pause_requested``: the step endpoint leaves it
 * SET (that is the whole difference between step and resume), so it
 * stays true across the blip and only clears on an actual resume.
 * Everything here keys off it.
 */

import type { TaskRun } from '../../types/task_run';

// 'partial' is terminal — the executor has unwound.  Omitting it here
// would leave the tile offering Pause/Step/Cancel on a run that no
// longer exists, and withhold the forward actions that matter most.
// 'held' is terminal for the same reason: an infra fault unwinds the
// coroutine, so there is no executor left to pause or step.  It is the
// case that most needs canResumeFromBlock, so misclassifying it as live
// would both offer three controls that do nothing and withhold the one
// that does.
const TERMINAL = ['done', 'partial', 'failed', 'cancelled', 'held'];

/**
 * True when the run is over and nothing can still be producing output.
 *
 * Exported so display surfaces (the inspector's completion banner, its
 * streaming-cursor flags) share ONE definition with the controls layer
 * rather than re-deriving it.  The inspector previously carried its own
 * ``['done','failed','cancelled']``, which omitted 'partial' and 'held'
 * — so the two statuses most in need of a "this is over, stop watching"
 * cue were the two that never got one.
 */
export function isRunOver(status: string | null | undefined): boolean {
  return status != null && TERMINAL.includes(status);
}

export interface RunControls {
  /** Run reached a terminal state; nothing is controllable. */
  isTerminal: boolean;
  /**
   * Under manual control — paused at a boundary OR mid-step.  Stable
   * across the per-step status blip, unlike ``status === 'paused'``.
   */
  isHeld: boolean;
  /** Held AND actually stopped: the executor is idle at a boundary. */
  isAtBoundary: boolean;
  /**
   * Held but the executor is still inside a block — either a pause
   * that hasn't reached its boundary yet, or a step in flight.  The
   * two are indistinguishable from the run record alone (a consumed
   * credit leaves no trace), so the caller supplies the wording.
   */
  isSettling: boolean;
  /** Unspent step credits. */
  stepCredits: number;
  canPause: boolean;
  canStep: boolean;
  canResume: boolean;
  canCancel: boolean;
  /**
   * Whether per-block "resume from here" applies.  True only on a
   * TERMINAL run — the opposite of every other flag here, and the
   * reason this is not folded into ``canResume``: that button clears a
   * pause flag on a live executor, whereas this launches a NEW run that
   * replays completed blocks.  A live run cannot use it (the server
   * 409s, since resuming a running run would double-execute its
   * remaining blocks).
   *
   * Requires ``card_snapshot``: the server 422s without it, because the
   * live card's block ids may no longer match this run's block_states.
   */
  canResumeFromBlock: boolean;
  /**
   * Whether per-block "continue past here" applies.  Same gating as
   * ``canResumeFromBlock`` — they are two modes of one endpoint — but
   * kept as a separate flag so a future policy can offer one without
   * the other (e.g. withholding continue on a run with no progress,
   * where accepting a non-existent outcome is meaningless).
   */
  canContinueFromBlock: boolean;
}

const IDLE: RunControls = {
  isTerminal: false, isHeld: false, isAtBoundary: false, isSettling: false,
  stepCredits: 0,
  canPause: false, canStep: false, canResume: false, canCancel: false,
  canResumeFromBlock: false, canContinueFromBlock: false,
};

export function deriveRunControls(run: TaskRun | null | undefined): RunControls {
  if (!run) return IDLE;

  const isTerminal = TERMINAL.includes(run.status);
  if (isTerminal) {
    return {
      ...IDLE,
      isTerminal: true,
      // Gate on the snapshot rather than offering the affordance and
      // letting the server 422: a control that always errors is worse
      // than an absent one.
      canResumeFromBlock: !!run.card_snapshot,
      canContinueFromBlock: !!run.card_snapshot,
    };
  }

  const isHeld = !!run.pause_requested;
  const isAtBoundary = isHeld && run.status === 'paused';

  return {
    isTerminal: false,
    isHeld,
    isAtBoundary,
    isSettling: isHeld && !isAtBoundary,
    stepCredits: Math.max(0, run.step_budget ?? 0),
    // Pausing an already-held run is a no-op the UI shouldn't offer.
    canPause: !isHeld,
    // Stepping a freely-running run is meaningful, not a no-op:
    // request_step also sets pause_requested, so the run advances to
    // its next boundary and holds there.  That is how you take control
    // of a card already in flight, so this is offered whenever the run
    // is live.
    canStep: true,
    canResume: isHeld,
    canCancel: true,
    // A live run must be cancelled first; the server enforces this with
    // a 409, and offering the control anyway would just produce errors.
    canResumeFromBlock: false,
    canContinueFromBlock: false,
  };
}

/**
 * Wording for the progress line while a run is held.
 *
 * ``stepping`` is the caller's own record of having just asked for a
 * step; it is NOT derivable from the run record, because a spent
 * credit leaves ``step_budget`` at zero and ``status`` at ``running``
 * — identical to a pause that hasn't landed yet.  Reporting what the
 * user asked for is honest; guessing from the record is not.
 */
export function heldLabel(c: RunControls, stepping: boolean): string {
  if (c.isAtBoundary) {
    return c.stepCredits > 0
      ? `Held — ${c.stepCredits} step${c.stepCredits === 1 ? '' : 's'} queued`
      : 'Held at a block boundary — step or resume to continue';
  }
  return stepping
    ? 'Advancing one block…'
    : 'Pausing at the next block boundary…';
}
