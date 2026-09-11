/**
 * BlockOutline — a card's block tree as a compact, foldable spine.
 *
 * Grown from TaskRunMap's row model (flattenBlocks) rather than from the
 * block editors: the deck previously showed the whole tree fully
 * expanded, every block's editor open at once, so the tree and the text
 * fought for the same height and both lost.  Here the tree is a
 * navigator and the text lives in a pane beside it.
 *
 * Two modes share ONE row layout:
 *   edit — no status; a click selects the block for the definition pane
 *   run  — a status glyph per row from the caller's `statusOf`
 *
 * Invariant (the "no-reflow" rule): the gutter — caret then glyph — is
 * rendered on EVERY row in BOTH modes at a fixed width, even when the
 * caret is a leaf's blank or the glyph is edit-mode's blank.  Folding a
 * container, selecting a row, or switching mode changes what a row
 * says, never where its label sits.  This is what lets the pane beside
 * it swap freely without the user's eye losing the row they were on.
 */

import React from 'react';
import type { Block } from '../../types/task_card';
import type { CallSnapshot } from '../../types/task_run';
import {
  flattenBlocks, foldRows, blockEmoji, blockLabel, STATUS_GLYPHS,
} from './runMapModel';

export type OutlineMode = 'edit' | 'run';

interface Props {
  root: Block;
  mode: OutlineMode;
  /** Selected block id; null selects nothing. */
  selectedId: string | null;
  onSelect: (blockId: string) => void;
  /** Ids of collapsed container blocks.  Owned by the parent. */
  collapsed: ReadonlySet<string>;
  onToggleCollapse: (blockId: string) => void;
  /** Run mode: status per block id.  Unknown ids read as queued. */
  statusOf?: (blockId: string) => string;
  /** Run mode: callee trees to splice under Call rows (see flattenBlocks). */
  callSnapshots?: Record<string, CallSnapshot>;
}

export const BlockOutline: React.FC<Props> = ({
  root, mode, selectedId, onSelect, collapsed, onToggleCollapse,
  statusOf, callSnapshots,
}) => {
  const rows = React.useMemo(
    () => foldRows(flattenBlocks(root, 0, callSnapshots), collapsed),
    [root, callSnapshots, collapsed],
  );

  if (rows.length === 0) {
    return <div className="tc-outline tc-outline--empty">No blocks yet</div>;
  }

  return (
    <div className={`tc-outline tc-outline--${mode}`} role="tree" aria-label="Card blocks">
      {rows.map((r, i) => {
        const id = r.block.id;
        // An unsaved block has no id to select by; see firstSelectableRow.
        const selectable = id !== '';
        const selected = selectable && id === selectedId;
        const status = mode === 'run' && selectable
          ? (statusOf?.(id) ?? 'queued')
          : null;
        const className = [
          'tc-outline__row',
          selected && 'tc-outline__row--selected',
          status && `tc-outline__row--${status}`,
          r.viaCall && 'tc-outline__row--called',
        ].filter(Boolean).join(' ');
        return (
          <div
            key={selectable ? id : `unsaved-${i}`}
            role="treeitem"
            aria-selected={selected}
            aria-expanded={r.hasChildren ? !r.collapsed : undefined}
            aria-level={r.depth + 1}
            tabIndex={selectable ? 0 : -1}
            className={className}
            style={{ paddingLeft: 8 + r.depth * 16 }}
            title={selectable ? undefined : 'Unsaved block — save the card to select it'}
            onClick={selectable ? () => onSelect(id) : undefined}
            onKeyDown={(e) => {
              if (!selectable) return;
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault(); onSelect(id);
              } else if (e.key === 'ArrowLeft' && r.hasChildren && !r.collapsed) {
                e.preventDefault(); onToggleCollapse(id);
              } else if (e.key === 'ArrowRight' && r.collapsed) {
                e.preventDefault(); onToggleCollapse(id);
              }
            }}
          >
            {/* Gutter, column 1: always present.  A leaf renders the
                button hidden-but-sized rather than omitting it, so the
                label column lines up across leaf and container rows. */}
            <button
              type="button"
              className={`tc-outline__caret${r.hasChildren ? '' : ' tc-outline__caret--leaf'}`}
              aria-hidden={!r.hasChildren}
              tabIndex={-1}
              disabled={!r.hasChildren || !selectable}
              title={r.hasChildren ? (r.collapsed ? 'Expand' : 'Collapse') : undefined}
              onClick={(e) => {
                e.stopPropagation();   // the row's onClick selects
                if (r.hasChildren && selectable) onToggleCollapse(id);
              }}
            >
              {r.hasChildren ? (r.collapsed ? '▸' : '▾') : ''}
            </button>
            {/* Gutter, column 2: always present; blank in edit mode. */}
            <span
              className={`tc-outline__glyph ${status ? `tc-outline__glyph--${status}` : 'tc-outline__glyph--blank'}`}
              data-testid="outline-glyph"
            >
              {status ? (STATUS_GLYPHS[status] ?? '○') : ''}
            </span>
            <span className="tc-outline__emoji">{blockEmoji(r.block)}</span>
            <span className="tc-outline__label">{blockLabel(r.block)}</span>
            {r.viaCall && (
              <span
                className="tc-outline__tag"
                title="From a called task — runs under the callee's own permissions"
              >
                called
              </span>
            )}
            {r.collapsed && (
              <span
                className="tc-outline__hidden"
                title="Collapsed — expand to see the nested blocks"
              >
                {r.hiddenCount} hidden
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
};

export default BlockOutline;
