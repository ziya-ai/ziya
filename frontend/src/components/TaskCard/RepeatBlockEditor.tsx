/**
 * Editor for a Repeat block (yellow wrapper).  Recurses via BlockEditor.
 */

import React from 'react';
import type { Block, RepeatMode, PropagateMode } from '../../types/task_card';
import { BlockEditor } from './BlockEditor';
import { makeTaskBlock, makeRepeatBlock } from '../../utils/taskCardBlocks';
import './task-card-editor.css';

interface Props {
  block: Block;
  onChange: (next: Block) => void;
  onDelete?: () => void;
}

export const RepeatBlockEditor: React.FC<Props> = ({ block, onChange, onDelete }) => {
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
  const addChild = (kind: 'task' | 'repeat') => {
    const child = kind === 'task' ? makeTaskBlock() : makeRepeatBlock();
    update({ body: [...block.body, child] });
  };

  const mode: RepeatMode = block.repeat_mode ?? 'count';
  const propagate: PropagateMode = block.repeat_propagate ?? 'none';

  return (
    <div className="tc-block tc-block-repeat">
      <div className="tc-block-header">
        <span className="tc-emoji">🔁</span>
        <span className="tc-block-label tc-block-label-repeat">Repeat</span>
        <select
          className="tc-select"
          value={mode}
          onChange={e => update({ repeat_mode: e.target.value as RepeatMode })}
        >
          <option value="count">count</option>
          <option value="until">until</option>
          <option value="for_each">for-each</option>
        </select>
        {mode === 'count' && (
          <>
            <input
              type="number" min={1}
              className="tc-num-input"
              value={block.repeat_count ?? 1}
              onChange={e => update({ repeat_count: parseInt(e.target.value, 10) || 1 })}
            />
            <span className="tc-label-dim">times</span>
          </>
        )}
        {mode === 'until' && (
          <>
            <span className="tc-label-dim">max</span>
            <input
              type="number" min={1}
              className="tc-num-input"
              value={block.repeat_max ?? 3}
              onChange={e => update({ repeat_max: parseInt(e.target.value, 10) || 1 })}
            />
          </>
        )}
        <label className="tc-checkbox-label">
          <input
            type="checkbox"
            checked={!!block.repeat_parallel}
            onChange={e => update({ repeat_parallel: e.target.checked })}
          /> parallel
        </label>
        <select
          className="tc-select tc-select-right"
          value={propagate}
          onChange={e => update({ repeat_propagate: e.target.value as PropagateMode })}
          title="What prior-iteration info is templated into the next iteration's instructions"
        >
          <option value="none">no propagation</option>
          <option value="last">last artifact</option>
          <option value="all">all artifacts</option>
        </select>
        {onDelete && (
          <button className="tc-icon-btn" onClick={onDelete} title="Delete">⋯</button>
        )}
      </div>
      <div className="tc-block-body tc-block-body-repeat">
        <div className="tc-sequence-label">In order:</div>
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
        </div>
      </div>
    </div>
  );
};
