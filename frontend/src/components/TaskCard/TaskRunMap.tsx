/**
 * TaskRunMap — the running card's "face": a compact indented map of
 * the block tree with per-block lifecycle state.  It is a CONTROLLED
 * navigator: focus state lives in the parent tile, so the output
 * region below can render the focused element's detail.  The map
 * itself only draws rows + dots and reports clicks via onFocus.
 *
 *   ✓ done   ● running (pulsing)   ✗ failed   ○ queued
 *   ⤼ skipped (on_failure=stop)    ◼ cancelled
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
}

const STATUS_GLYPHS: Record<string, string> = {
  queued: '○', running: '●', done: '✓',
  failed: '✗', cancelled: '◼', skipped: '⤼',
};

export const TaskRunMap: React.FC<Props> = ({
  card, run, live, focusedId, focusedIndex, onFocus,
}) => {
  const rows = flattenBlocks(card.root);

  // A single-node map adds nothing over the tile's own status chrome.
  if (rows.length <= 1) return null;

  return (
    <div className="tc-map">
      {rows.map(({ block, depth }) => {
        const status = resolveBlockStatus(block.id, live.blockStatuses, run);
        const state = run.block_states?.[block.id];
        const dots = isLoopBlock(block)
          ? buildDots(state?.iteration_summaries, status === 'running')
          : null;
        const rowSelected = focusedId === block.id && focusedIndex == null;
        return (
            <div
              key={block.id}
              className={
                `tc-map__row tc-map__row--${status}` +
                (rowSelected ? ' tc-map__row--selected' : '')
              }
              style={{ paddingLeft: 8 + depth * 16 }}
              title={state?.error || 'Click for config & output'}
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
              {dots && (dots.total > 0 || dots.running) && (
                <span className="tc-map__dots">
                  {dots.overflow > 0 && (
                    <span className="tc-map__dot-count">+{dots.overflow}</span>
                  )}
                  {dots.dots.map(d => {
                    const clickable = d.status === 'failed' && d.hasArtifact;
                    const sel = focusedId === block.id && focusedIndex === d.index;
                    return (
                      <button
                        key={d.index}
                        className={
                          `tc-map__dot tc-map__dot--${d.status}` +
                          (sel ? ' tc-map__dot--selected' : '')
                        }
                        onClick={clickable
                          ? (e) => { e.stopPropagation(); onFocus(block.id, d.index); }
                          : undefined}
                        disabled={!clickable}
                        title={`#${d.index} ${d.status}`}
                      />
                    );
                  })}
                  {dots.running && (
                    <span className="tc-map__dot tc-map__dot--running" />
                  )}
                  <span className="tc-map__dot-count">{dots.total}</span>
                </span>
              )}
              {status === 'skipped' && (
                <span className="tc-map__tag">skipped</span>
              )}
            </div>
        );
      })}
    </div>
  );
};

export default TaskRunMap;
