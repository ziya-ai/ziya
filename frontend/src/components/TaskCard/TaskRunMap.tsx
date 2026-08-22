/**
 * TaskRunMap — the running card's "face": a compact indented map of
 * the block tree with per-block lifecycle state.  It is a CONTROLLED
 * navigator: focus state lives in the parent tile, so the output
 * region below can render the focused element's detail.  The map
 * itself only draws rows + dots and reports clicks via onFocus.
 *
 *   ✓ done   ● running   ✗ failed   ○ queued
 *   ⤼ skipped (on_failure=stop)    ◼ cancelled
 *
 * The running stage is signalled four ways — accent bar, row tint,
 * weighted label, and a "running" chip — because any single cue fails
 * under some condition (tint on a light theme, animation under
 * prefers-reduced-motion, colour in monochrome).
 *
 * Every row is clickable to focus that block; loop blocks also render
 * an iteration dot strip — clicking a dot focuses that specific
 * iteration.  The parent renders the detail for whatever is focused.
 *
 * Data sources: live ``block_status`` events (fresh) merged over the
 * REST snapshot's block_states (durable) — see runMapModel.
 */

import React from 'react';
import type { TaskCard } from '../../types/task_card';
import type { TaskRun } from '../../types/task_run';
import type { LiveTaskState } from '../../hooks/useTaskRunStream';
import {
  flattenBlocks, resolveBlockStatus, isLoopBlock, buildDots,
  blockEmoji, blockLabel,
} from './runMapModel';
import { deriveHoldChain, positionOf, holdLabel } from './holdChain';

interface Props {
  projectId: string;
  card: TaskCard;
  run: TaskRun;
  live: LiveTaskState;
  /** Currently focused block id (null = whole run). */
  focusedId: string | null;
  /** Focused loop iteration index, or null for block-level focus. */
  focusedIndex: number | null;
  /** Report a focus change.  index=null focuses the block itself. */
  onFocus: (blockId: string, index: number | null) => void;
  /**
   * When set, each row shows a "resume from here" affordance.  Absent
   * on live runs (the server 409s) and on runs with no card_snapshot
   * (it 422s), so the caller gates rather than this component.
   */
  onResumeFrom?: (blockId: string) => void;
  /**
   * When set, each row also offers "continue past here" — accept this
   * block's recorded outcome and start at the next one.  Distinct from
   * onResumeFrom, which re-runs the block: continuing is what you want
   * after fixing the problem by hand, and re-running would undo that.
   */
  onContinueFrom?: (blockId: string) => void;
  /**
   * Block id whose resume request is in flight — disables every row's
   * affordance so a double-click can't launch two runs.
   */
  resumingBlockId?: string | null;
}

const STATUS_GLYPHS: Record<string, string> = {
  queued: '○', running: '●', done: '✓',
  failed: '✗', cancelled: '◼', skipped: '⤼',
  // Held needs its own glyph: without an entry the `?? '○'` fallback
  // below painted the faulting block identically to a queued one, so the
  // backend's new 'held' block status was flattened straight back into
  // "hasn't started yet" -- the exact confusion it was added to remove.
  held: '⏸',
};

/**
 * Suffix labelling a row's position relative to an infrastructure hold.
 * Terse by design: the row already carries a glyph and a name, and the
 * banner carries the breadth, so this only has to answer "is this block
 * the problem, or downstream of it?".
 */
const POSITION_LABELS: Record<string, string> = {
  local: 'HELD HERE',
  descendant: 'holding',
  ancestor: 'blocked',
};

export const TaskRunMap: React.FC<Props> = ({
  card, run, live, focusedId, focusedIndex, onFocus,
  onResumeFrom, onContinueFrom, resumingBlockId,
}) => {
  const rows = flattenBlocks(card.root, 0, run.call_snapshots ?? undefined);
  // Derived once for the whole map rather than per row: the walk is O(tree)
  // and every row needs an answer from the same snapshot.  Inert unless the
  // run actually held, so this is safe to call unconditionally.
  //
  // The tree passed here is the FLATTENED card root, which already has the
  // Call targets spliced in (flattenBlocks does that above) -- but
  // deriveHoldChain walks `body` itself, so it sees only this card's own
  // blocks.  A hold inside a callee therefore resolves to no position
  // rather than a wrong one, and the run-level banner still reports it.
  const hold = deriveHoldChain(run, card.root);

  // A single-node map adds nothing over the tile's own status chrome.
  if (rows.length <= 1) return null;

  return (
    <div className="tc-map">
      {rows.map(({ block, depth, viaCall }) => {
        const status = resolveBlockStatus(block.id, live.blockStatuses, run);
        const state = run.block_states?.[block.id];
        const dots = isLoopBlock(block)
          ? buildDots(state?.iteration_summaries, status === 'running')
          : null;
        // The dots strip and the "running" chip both claim margin-left:
        // auto, so only one can hold the row's right edge.  The strip
        // already shows a live iteration, so the chip is redundant there.
        const showDots = !!dots && (dots.total > 0 || dots.running);
        const rowSelected = focusedId === block.id && focusedIndex == null;
        const holdPos = positionOf(hold, block.id);
        return (
            <div
              key={block.id}
              className={
                `tc-map__row tc-map__row--${status}` +
                (holdPos !== 'none' ? ` tc-map__row--hold-${holdPos}` : '') +
                (rowSelected ? ' tc-map__row--selected' : '')
              }
              style={{ paddingLeft: 8 + depth * 16 }}
              title={
                state?.error
                || (holdPos !== 'none' ? holdLabel(hold, holdPos) : null)
                || 'Click for config & output'
              }
              role="button"
              tabIndex={0}
              onClick={() => onFocus(block.id, null)}
              onKeyDown={e => { if (e.key === 'Enter') onFocus(block.id, null); }}
            >
              <span className={`tc-map__icon tc-map__icon--${status}`}>
                {STATUS_GLYPHS[status] ?? '○'}
              </span>
              <span className="tc-map__emoji">{blockEmoji(block)}</span>
              <span className="tc-map__label">{blockLabel(block)}</span>
              {/* Position relative to an infrastructure hold.  Deliberately
                  NOT margin-left:auto — the "called" tag and the iteration
                  dots strip both claim the row's right edge, and a third
                  claimant would silently win or lose depending on which
                  siblings happen to render.  Sitting next to the label also
                  keeps the marker adjacent to the thing it qualifies. */}
              {holdPos !== 'none' && (
                <span
                  className={`tc-map__hold tc-map__hold--${holdPos}`}
                  title={holdLabel(hold, holdPos) ?? undefined}
                >
                  {POSITION_LABELS[holdPos]}
                </span>
              )}
              {/* Attribution, not decoration: this block belongs to a
                  DIFFERENT card, runs under that card's own approved
                  permissions, and editing this card will not change it.
                  An unmarked row would imply all three are false. */}
              {viaCall && (
                <span className="tc-map__tag" title="From a called task — runs under the callee's own permissions">
                  called
                </span>
              )}
              {showDots && dots && (
                <span className="tc-map__dots">
                  {dots.overflow > 0 && (
                    <span className="tc-map__dot-count">+{dots.overflow}</span>
                  )}
                  {dots.dots.map(d => {
                    // Openable whenever an artifact was RETAINED, not only
                    // when the iteration failed.  ``has_artifact`` is true
                    // for every failure AND for passes under the retention
                    // cap (see block_executor._record_iteration), so the old
                    // ``status === 'failed'`` half of this test discarded
                    // real, fetchable output: a 5-iteration loop that all
                    // passed had five dots that looked identical to the
                    // unopenable kind and did nothing on click.
                    const clickable = d.hasArtifact;
                    const sel = focusedId === block.id && focusedIndex === d.index;
                    return (
                      <button
                        key={d.index}
                        className={
                          `tc-map__dot tc-map__dot--${d.status}` +
                          // Distinguishes "nothing to open" from "click me"
                          // VISUALLY.  Previously the only difference was the
                          // disabled attribute, invisible on a 8px circle, so
                          // clicking around mostly did nothing and read as
                          // broken rather than as absent data.
                          (clickable ? ' tc-map__dot--openable' : '') +
                          // Preserved, not performed here.  Keeps the
                          // pass/fail colour — the outcome is still the
                          // record — but dimmed, so the strip reads as
                          // "these three were kept, these two are mine"
                          // instead of restarting the count at 1.
                          (d.replayed ? ' tc-map__dot--replayed' : '') +
                          (sel ? ' tc-map__dot--selected' : '')
                        }
                        onClick={clickable
                          ? (e) => { e.stopPropagation(); onFocus(block.id, d.index); }
                          : undefined}
                        disabled={!clickable}
                        title={d.replayed
                          ? `#${d.index} ${d.status} — replayed from an earlier `
                            + `attempt, not re-run`
                            + (clickable ? ' — click to view output' : '')
                          : clickable
                          ? `#${d.index} ${d.status} — click to view output`
                          : `#${d.index} ${d.status} — output not retained`}
                      />
                    );
                  })}
                  {dots.running && (
                    <span className="tc-map__dot tc-map__dot--running" />
                  )}
                  <span className="tc-map__dot-count">{dots.total}</span>
                </span>
              )}
              {status === 'running' && !showDots && (
                <span className="tc-map__tag tc-map__tag--running">running</span>
              )}
              {status === 'skipped' && (
                <span className="tc-map__tag">skipped</span>
              )}
              {/* Per-block resume.  Rendered on every row rather than
                  only on failed ones: re-running from an earlier point
                  than the failure is a legitimate and common choice,
                  and the server normalizes a loop-body target up to its
                  enclosing loop anyway.

                  Suppressed on a callee's rows: a resume target must be a
                  block of the tree THIS run's card_snapshot describes, and
                  a callee block is not, so the server would 422 it. */}
              {onResumeFrom && !viaCall && (
                <button
                  className="tc-map__resume"
                  disabled={!!resumingBlockId}
                  title={
                    resumingBlockId === block.id
                      ? 'Starting a new run…'
                      : 'Start a NEW run from this block, replaying the ' +
                        'earlier blocks\u2019 recorded results instead of ' +
                        're-running them. This run is kept as a record.'
                  }
                  onClick={(e) => {
                    e.stopPropagation();   // row onClick focuses the block
                    onResumeFrom(block.id);
                  }}
                >
                  {resumingBlockId === block.id ? '…' : '↻ from here'}
                </button>
              )}
              {/* Continue past this block.  Offered alongside retry
                  rather than instead of it: after a failure the two are
                  genuinely different intents ("try again" vs "I fixed
                  it, move on"), and guessing which the user meant from
                  the block's status would be wrong half the time.
                  Callee rows are excluded for the same reason as retry. */}
              {onContinueFrom && !viaCall && (
                <button
                  className="tc-map__continue"
                  disabled={!!resumingBlockId}
                  title={
                    'Start a NEW run that accepts this block\u2019s '
                    + 'recorded result and continues from the NEXT block. '
                    + 'Use after fixing the problem by hand. Earlier '
                    + 'blocks replay from record; this run is kept.'
                  }
                  onClick={(e) => {
                    e.stopPropagation();
                    onContinueFrom(block.id);
                  }}
                >
                  ▶ past here
                </button>
              )}
            </div>
        );
      })}
    </div>
  );
};

export default TaskRunMap;
