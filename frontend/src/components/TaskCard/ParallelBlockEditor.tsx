/**
 * Editor for a Parallel block (purple wrapper).  Recurses via BlockEditor.
 *
 * Semantics: all children in `body` execute concurrently; the composite
 * artifact concatenates each child's outputs in declared order.
 * See design/task-cards.md §Parallel and app/agents/block_executor.py
 * _execute_parallel.
 */

import React from 'react';
import type { Block, TaskScope } from '../../types/task_card';
import { BlockBody } from './BlockBody';
import { BlockScopeButton } from './BlockScopeButton';
import { DragHandle } from './DragContext';
import './task-card-editor.css';

interface Props {
  block: Block;
  onChange: (next: Block) => void;
  onDelete?: () => void;
  isRoot?: boolean;
}

export const ParallelBlockEditor: React.FC<Props> = ({ block, onChange, onDelete, isRoot }) => {
  const update = (patch: Partial<Block>) => onChange({ ...block, ...patch });

  return (
    <div className="tc-block tc-block-parallel">
      <div className="tc-block-header">
        {!isRoot && <DragHandle id={block.id} />}
        <span className="tc-emoji">⚡</span>
        <input
          className="tc-name-input"
          value={block.name}
          onChange={e => update({ name: e.target.value })}
          placeholder="Parallel group name"
        />
        <span className="tc-block-label tc-block-label-parallel">Parallel</span>
        {onDelete && (
          <button className="tc-icon-btn tc-icon-btn-delete" onClick={onDelete} title="Delete">×</button>
        )}
      </div>
      <div className="tc-block-body tc-block-body-scope-row">
        <BlockScopeButton
          scope={block.scope}
          onChange={(next: TaskScope) => update({ scope: next })}
          title={block.name || 'this Parallel block'}
        />
      </div>
      <BlockBody
        parentId={block.id}
        body={block.body}
        bodyClassName="tc-block-body-parallel"
        sequenceLabel="All at once:"
        onChange={body => update({ body })}
      />
    </div>
  );
};
