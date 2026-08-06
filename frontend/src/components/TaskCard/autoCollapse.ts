/**
 * autoCollapse — decides whether a finished tile may fold itself away.
 *
 * The tile collapses to a one-line receipt 8s after reaching a terminal
 * status, which is right for the common case (a run you glanced at and
 * are done with) and wrong for the case that matters: a user reading the
 * inspector trace has the whole thing yanked out from under them
 * mid-sentence, and the only way back is to click and re-open every
 * section they had expanded.
 *
 * That is worse now than it was.  The completion footer added in B5 is
 * precisely the cue that tells someone the trace is worth a last look —
 * and it lives inside the drawer that the timer closes.
 *
 * Three rules, kept here as pure functions so the policy is testable
 * without timers or a rendered tile:
 *
 *   1. Engagement defers.  Any interaction inside the tile pushes the
 *      collapse out by a quiet period, so reading is never interrupted;
 *      an untouched tile still tidies itself away on the original 8s.
 *   2. A run awaiting the user never collapses at all.  'held' is
 *      terminal for the run object but the work is unfinished and
 *      continuable, and the receipt carries no controls — collapsing it
 *      hides the only surface offering "resume from here".
 *   3. An expand BY HAND pins the tile open indefinitely.  A mousedown
 *      inside a tile is ambiguous (a stray click while scrolling past
 *      reads the same as reading), which is why rule 1 only defers.
 *      Clicking a collapsed receipt open is not ambiguous: it is a
 *      decision to read this particular run, and the quiet period is no
 *      answer to it — a reader who is still on the same trace 30s later
 *      gets it closed under them anyway, and re-opening re-arms the same
 *      timer, so the tile fights back every time it is opened.
 */

import type { TaskRun } from '../../types/task_run';

/** Delay before an untouched finished tile folds to a receipt. */
export const AUTO_COLLAPSE_MS = 8000;

/**
 * Quiet period required after the last interaction.  Deliberately longer
 * than AUTO_COLLAPSE_MS: someone who touched the tile is reading it, and
 * the cost of collapsing too early (losing your place, re-expanding
 * several sections) far exceeds the cost of a tile that stays open a
 * little longer than needed.
 */
export const ENGAGED_QUIET_MS = 30000;

/**
 * Statuses that mean "stopped, but the user still has a decision to
 * make".  A held run stopped on an infrastructure fault, so it is
 * waiting on the human — not finished.
 */
const AWAITING_USER: ReadonlyArray<string> = ['held'];

/**
 * True when a terminal run is waiting on the user rather than done with
 * them, and so must never auto-collapse.
 *
 * Keyed on ``status`` rather than on ``deriveRunControls(...).isHeld``
 * because that flag is false for status==='held': the terminal branch
 * returns a spread of IDLE, so ``isHeld`` only ever describes a
 * PAUSED/STEPPING (non-terminal) run.  A guard written against it would
 * therefore never fire for exactly the status it was meant to protect.
 */
export function awaitsUser(run: TaskRun | null | undefined): boolean {
  return !!run && AWAITING_USER.includes(run.status);
}

export interface CollapseDecision {
  /** Whether a collapse timer should be armed at all. */
  arm: boolean;
  /** Milliseconds to wait before collapsing.  Meaningless when !arm. */
  delayMs: number;
}

/**
 * Decide whether to arm the auto-collapse timer, and for how long.
 *
 * @param run            the run being displayed
 * @param isTerminal     the tile's own terminal verdict (shared with the
 *                       controls layer, so this helper does not
 *                       re-derive it and cannot disagree)
 * @param expanded       whether the tile is currently expanded
 * @param lastInteractionAt epoch ms of the last interaction inside the
 *                       tile, or null if it has never been touched
 * @param nowMs          injectable clock for testing
 * @param manuallyExpanded whether the CURRENT expanded state was reached
 *                       by the user clicking the chevron / receipt, as
 *                       opposed to the tile simply rendering open.
 *                       Trailing so ``nowMs`` keeps the position every
 *                       existing caller already passes it in; the clock
 *                       stays where callers expect it.
 */
export function decideAutoCollapse(
  run: TaskRun | null | undefined,
  isTerminal: boolean,
  expanded: boolean,
  lastInteractionAt: number | null,
  nowMs: number = Date.now(),
  manuallyExpanded: boolean = false,
): CollapseDecision {
  // Nothing to collapse, or nothing has finished yet.
  if (!expanded || !isTerminal) return { arm: false, delayMs: 0 };
  // No run to reason about.  Unreachable from the tile (it early-returns
  // a loading state before render), but arming a timer for a run whose
  // status we cannot read would be a guess, and the guess that hides UI
  // is the wrong one to make.
  if (!run) return { arm: false, delayMs: 0 };
  // Waiting on the user — the receipt has no controls, so folding away
  // would strand the run behind an extra click with no hint it needs one.
  if (awaitsUser(run)) return { arm: false, delayMs: 0 };
  // Opened by hand — leave it open.  The user closes it, or nothing
  // does.  Deliberately checked before the interaction arithmetic below,
  // so a pinned tile never gets a delay computed for it at all: an
  // "eventually" here is the behaviour being removed, not a safer form
  // of it.
  if (manuallyExpanded) return { arm: false, delayMs: 0 };
  if (lastInteractionAt == null) {
    // Untouched: the original behaviour, unchanged.
    return { arm: true, delayMs: AUTO_COLLAPSE_MS };
  }
  // Touched: wait out the remainder of the quiet period.  Clamped at 0
  // rather than negative so a long-idle tile collapses on the next tick
  // instead of arming a timer with a nonsense delay.
  const elapsed = Math.max(0, nowMs - lastInteractionAt);
  return {
    arm: true,
    delayMs: Math.max(0, ENGAGED_QUIET_MS - elapsed),
  };
}
