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

import React, { useState } from 'react';
import type { TaskCard } from '../../types/task_card';
import type { TaskRun } from '../../types/task_run';
import type { LiveTaskState } from '../../hooks/useTaskRunStream';
import {
  flattenBlocks, resolveBlockStatus, isLoopBlock, buildDotPasses,
  blockEmoji, blockLabel, STATUS_GLYPHS,
} from './runMapModel';
import { IterationDotStrip } from './IterationDotStrip';
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
  /**
   * Enclosing-loop pass of the focused iteration (IterationSummary
   * .pass_key), or null for a top-level loop.  Needed alongside the
   * index because a nested loop's index repeats once per outer pass.
   */
  focusedPassKey?: string | null;
  /** Report a focus change.  index=null focuses the block itself;
   * passKey qualifies the index for a nested loop's iteration. */
  onFocus: (blockId: string, index: number | null, passKey?: string | null) => void;
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
  card, run, live, focusedId, focusedIndex, focusedPassKey = null, onFocus,
  onResumeFrom, onContinueFrom, resumingBlockId,
}) => {
  const rows = flattenBlocks(card.root, 0, run.call_snapshots ?? undefined);
  // Loop rows whose collapsed pass history has been expanded.  Local to
  // the map: it is a viewing choice, not run state, and resets with the
  // tile like every other disclosure toggle.
  const [expandedPasses, setExpandedPasses] = useState<Set<string>>(new Set());
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
      {rows.map(({ block, depth, viaCall, loop }) => {
        const status = resolveBlockStatus(block.id, live.blockStatuses, run, loop);
        const state = run.block_states?.[block.id];
        const passes = isLoopBlock(block)
          ? buildDotPasses(
              state?.iteration_summaries, status === 'running',
              // Live buckets are the only source that knows HOW MANY
              // iterations are in flight; iteration_summaries records
              // only completed ones.
              live.iterations
                .filter(it => it.blockId === block.id && it.status === 'running')
                .map(it => it.index),
            )
          : null;
        // The dots strip and the "running" chip both claim margin-left:
        // auto, so only one can hold the row's right edge.  The strip
        // already shows a live iteration, so the chip is redundant there.
        const showDots = !!passes
          && passes.some(p => p.dots.total > 0 || p.dots.running);
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
              {showDots && passes && (
                <IterationDotStrip
                  blockId={block.id}
                  passes={passes}
                  planned={state?.planned_iterations}
                  focusedId={focusedId}
                  focusedIndex={focusedIndex}
                  focusedPassKey={focusedPassKey}
                  onFocus={onFocus}
                  expanded={expandedPasses.has(block.id)}
                  onToggleExpanded={() => setExpandedPasses(prev => {
                    const next = new Set(prev);
                    if (next.has(block.id)) next.delete(block.id); else next.add(block.id);
                    return next;
                  })}
                />
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
                      ? 'Re-running…'
                      : 'Re-run this block and everything after it. This '
                        + 'block EXECUTES AGAIN, so anything it wrote '
                        + '(files, deck state) is produced from scratch. '
                        + 'To keep its result, use "past here" instead.'
                  }
                  onClick={(e) => {
                    e.stopPropagation();   // row onClick focuses the block
                    onResumeFrom(block.id);
                  }}
                >
                  {resumingBlockId === block.id ? '…' : '↻ re-run from here'}
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
                    'Continue from the next block. Use after fixing the problem by hand.'
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
