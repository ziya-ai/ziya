/**
 * Editor for an Until block — loop until a model-evaluated condition
 * holds.  See app/agents/block_executor.py::_execute_until and
 * app/agents/until_evaluator.py.
 *
 * Mode 'expression' is shown but greyed out; it's reserved for a
 * future server-side expression evaluator and is recorded on the
 * block so cards saved now will run as 'model' until the
 * implementation lands.
 */

import React from 'react';
import type { Block, UntilMode } from '../../types/task_card';
import { BlockEditor } from './BlockEditor';
import {
  makeTaskBlock, makeRepeatBlock, makeParallelBlock,
  makeUntilBlock, makeScheduleBlock,
} from '../../utils/taskCardBlocks';
import './task-card-editor.css';

interface Props {
  block: Block;
  onChange: (next: Block) => void;
  onDelete?: () => void;
}

export const UntilBlockEditor: React.FC<Props> = ({ block, onChange, onDelete }) => {
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

  const mode: UntilMode = block.until_mode ?? 'model';

  return (
    <div className="tc-block tc-block-until">
      <div className="tc-block-header">
        <span className="tc-emoji">🔄</span>
        <span className="tc-block-label tc-block-label-until">Until</span>
        <select
          className="tc-select"
          value={mode}
          onChange={e => update({ until_mode: e.target.value as UntilMode })}
          title="How the loop's exit condition is evaluated"
        >
          <option value="model">model judges (yes/no)</option>
          <option value="expression" disabled>
            expression (coming soon)
          </option>
        </select>
        <input
          type="text"
          className="tc-text-input tc-flex-grow"
          placeholder="condition (e.g. 'all tests pass')"
          value={block.until_condition ?? ''}
          onChange={e => update({ until_condition: e.target.value })}
          title={
            mode === 'model'
              ? "Plain English condition; an evaluator model decides yes/no after each iteration"
              : "Expression mode is not yet implemented"
          }
        />
        <span className="tc-label-dim">max</span>
        <input
          type="number" min={1}
          className="tc-num-input"
          value={block.until_max ?? 5}
          onChange={e => update({ until_max: parseInt(e.target.value, 10) || 1 })}
          title="Hard upper bound on iteration count"
        />
        {onDelete && (
          <button className="tc-icon-btn" onClick={onDelete} title="Delete">⋯</button>
        )}
      </div>
      <div className="tc-block-body tc-block-body-until">
        <div className="tc-sequence-label">Repeat in order until condition met:</div>
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
        </div>
      </div>
    </div>
  );
};
