/**
 * Editor for a Parallel block (purple wrapper).  Recurses via BlockEditor.
 *
 * Semantics: all children in `body` execute concurrently; the composite
 * artifact concatenates each child's outputs in declared order.
 * See design/task-cards.md §Parallel and app/agents/block_executor.py
 * _execute_parallel.
 */

import React from 'react';
import type { Block } from '../../types/task_card';
import { BlockEditor } from './BlockEditor';
import {
  makeTaskBlock, makeRepeatBlock, makeParallelBlock, makeUntilBlock, makeScheduleBlock,
} from '../../utils/taskCardBlocks';
import './task-card-editor.css';

interface Props {
  block: Block;
  onChange: (next: Block) => void;
  onDelete?: () => void;
}

export const ParallelBlockEditor: React.FC<Props> = ({ block, onChange, onDelete }) => {
  const update = (patch: Partial<Block>) => onChange({ ...block, ...patch });
  const updateChild = (idx: number, child: Block) => {
    const body = block.body.slice();
    body[idx] = child;
    update({ body });
  };
  const removeChild = (idx: number) => {
    const body = block.body.slice();
    body.splice(idx, 1);
    update({ body });
  };
  const addChild = (kind: 'task' | 'repeat' | 'parallel' | 'until' | 'schedule') => {
    const child =
      kind === 'task' ? makeTaskBlock() :
      kind === 'repeat' ? makeRepeatBlock() :
      kind === 'parallel' ? makeParallelBlock() :
      kind === 'until' ? makeUntilBlock() :
      makeScheduleBlock();
    update({ body: [...block.body, child] });
  };

  return (
    <div className="tc-block tc-block-parallel">
      <div className="tc-block-header">
        <span className="tc-emoji">⚡</span>
        <input
          className="tc-name-input"
          value={block.name}
          onChange={e => update({ name: e.target.value })}
          placeholder="Parallel group name"
        />
        <span className="tc-block-label tc-block-label-parallel">Parallel</span>
        {onDelete && (
          <button className="tc-icon-btn" onClick={onDelete} title="Delete">⋯</button>
        )}
      </div>
      <div className="tc-block-body tc-block-body-parallel">
        <div className="tc-sequence-label">All at once:</div>
        {block.body.map((child, idx) => (
          <BlockEditor
            key={child.id}
            block={child}
            onChange={next => updateChild(idx, next)}
            onDelete={() => removeChild(idx)}
          />
        ))}
        <div className="tc-add-row">
          <button className="tc-add-btn" onClick={() => addChild('task')}>+ Task</button>
          <button className="tc-add-btn" onClick={() => addChild('repeat')}>+ Repeat</button>
          <button className="tc-add-btn" onClick={() => addChild('parallel')}>+ Parallel</button>
          <button className="tc-add-btn" onClick={() => addChild('until')}>+ Until</button>
          <button className="tc-add-btn" onClick={() => addChild('schedule')}>+ Schedule</button>
        </div>
      </div>
    </div>
  );
};
