/**
 * Drag-and-drop coordination for the Task Card editor.
 *
 * Composability (drag a task into a parallel set, move a wrapper into
 * another, reorder siblings) is fundamentally a whole-tree move, not a
 * local subtree edit. This provider owns the drag state and performs
 * the move against the card root via moveBlock, so any drop target can
 * relocate any block regardless of where it currently lives.
 */

import React, { createContext, useContext, useMemo, useState } from 'react';
import type { Block } from '../../types/task_card';
import { moveBlock, canMoveBlock } from '../../utils/taskCardBlocks';

interface DragCtx {
  draggingId: string | null;
  beginDrag: (id: string) => void;
  endDrag: () => void;
  /** Can the in-flight block be dropped into this parent's body? */
  canDropInto: (targetParentId: string) => boolean;
  /** Perform the move; beforeId=null appends to the end of the body. */
  drop: (targetParentId: string, beforeId: string | null) => void;
}

const Ctx = createContext<DragCtx | null>(null);

export const useTaskCardDrag = (): DragCtx => {
  const c = useContext(Ctx);
  if (!c) throw new Error('useTaskCardDrag must be used within TaskCardDragProvider');
  return c;
};

interface ProviderProps {
  root: Block;
  onRootChange: (next: Block) => void;
  children: React.ReactNode;
}

export const TaskCardDragProvider: React.FC<ProviderProps> = ({
  root, onRootChange, children,
}) => {
  const [draggingId, setDraggingId] = useState<string | null>(null);

  const value = useMemo<DragCtx>(() => ({
    draggingId,
    beginDrag: (id) => setDraggingId(id),
    endDrag: () => setDraggingId(null),
    canDropInto: (targetParentId) =>
      draggingId != null && canMoveBlock(root, draggingId, targetParentId),
    drop: (targetParentId, beforeId) => {
      if (draggingId == null) { setDraggingId(null); return; }
      const next = moveBlock(root, draggingId, targetParentId, beforeId);
      // moveBlock returns the same reference on a no-op/illegal move.
      if (next !== root) onRootChange(next);
      setDraggingId(null);
    },
  }), [draggingId, root, onRootChange]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
};

/**
 * The grip a block is dragged by. A dedicated handle (rather than a
 * draggable block container) keeps the block's text inputs selectable.
 */
export const DragHandle: React.FC<{ id: string }> = ({ id }) => {
  const ctx = useTaskCardDrag();
  return (
    <span
      className="tc-drag-handle"
      draggable
      role="button"
      aria-label="Drag to move or reorder this block"
      title="Drag to move / reorder"
      onDragStart={e => {
        e.dataTransfer.effectAllowed = 'move';
        try { e.dataTransfer.setData('text/plain', id); } catch { /* some browsers */ }
        // stopPropagation so a drag started on a nested handle doesn't
        // also begin a drag on an ancestor wrapper's handle.
        e.stopPropagation();
        // Defer the state flip by one tick.  beginDrag sets draggingId,
        // which mounts every DropZone (null -> element) around the
        // dragged node.  Mutating the dragged element's subtree
        // synchronously inside dragstart aborts the drag in Chrome
        // (observed: dragstart -> dragend, zero drag events, +7/-7 node
        // churn).  A 0ms defer lets the native drag initiate first.
        setTimeout(() => ctx.beginDrag(id), 0);
      }}
      onDragEnd={e => { e.stopPropagation(); ctx.endDrag(); }}
    >
      ⠿
    </span>
  );
};
