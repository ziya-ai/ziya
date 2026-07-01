/**
 * Shared body renderer for every wrapper block (Repeat / Parallel /
 * Until / Schedule). Renders the child list with drop zones between
 * children, a trailing drop zone, and the add-block row.
 *
 * Extracted from the four wrappers (which had byte-identical
 * updateChild/removeChild/addChild + map + add-row) so drop-target
 * logic lives in one place. Local edits (add/edit/delete a child) go
 * through onChange; cross-tree moves go through the drag context's
 * root-level moveBlock.
 */

import React, { useState } from 'react';
import type { Block, BlockType } from '../../types/task_card';
import { BlockEditor } from './BlockEditor';
import { makeBlock } from '../../utils/taskCardBlocks';
import { useTaskCardDrag } from './DragContext';
import './task-card-editor.css';

const DropZone: React.FC<{ parentId: string; beforeId: string | null }> = ({
  parentId, beforeId,
}) => {
  const ctx = useTaskCardDrag();
  const [over, setOver] = useState(false);
  // Only render an active target while a droppable drag is in flight,
  // so the editor has no extra spacing at rest.
  if (ctx.draggingId == null || !ctx.canDropInto(parentId)) return null;
  return (
    <div
      className={`tc-drop-zone${over ? ' tc-drop-zone-over' : ''}`}
      onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={e => { e.preventDefault(); e.stopPropagation(); setOver(false); ctx.drop(parentId, beforeId); }}
    />
  );
};

const ADD_KINDS: BlockType[] = ['task', 'state', 'repeat', 'parallel', 'until', 'schedule'];

interface Props {
  parentId: string;
  body: Block[];
  bodyClassName: string;
  sequenceLabel: string;
  /** Until/Schedule omit nested schedules from their add-row. Defaults to true. */
  allowSchedule?: boolean;
  onChange: (nextBody: Block[]) => void;
}

export const BlockBody: React.FC<Props> = ({
  parentId, body, bodyClassName, sequenceLabel, allowSchedule = true, onChange,
}) => {
  const updateChild = (idx: number, child: Block) => {
    const next = body.slice(); next[idx] = child; onChange(next);
  };
  const removeChild = (idx: number) => {
    const next = body.slice(); next.splice(idx, 1); onChange(next);
  };
  const addChild = (kind: BlockType) => onChange([...body, makeBlock(kind)]);

  const kinds = allowSchedule ? ADD_KINDS : ADD_KINDS.filter(k => k !== 'schedule');

  return (
    <div className={`tc-block-body ${bodyClassName}`}>
      {sequenceLabel ? <div className="tc-sequence-label">{sequenceLabel}</div> : null}
      {body.map((child, idx) => (
        <React.Fragment key={child.id}>
          <DropZone parentId={parentId} beforeId={child.id} />
          <BlockEditor
            block={child}
            onChange={next => updateChild(idx, next)}
            onDelete={() => removeChild(idx)}
          />
        </React.Fragment>
      ))}
      <DropZone parentId={parentId} beforeId={null} />
      <div className="tc-add-row">
        {kinds.map(k => (
          <button key={k} className="tc-add-btn" onClick={() => addChild(k)}>
            + {k.charAt(0).toUpperCase() + k.slice(1)}
          </button>
        ))}
      </div>
    </div>
  );
};
