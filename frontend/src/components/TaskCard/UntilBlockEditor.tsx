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
import { BlockBody } from './BlockBody';
import { DragHandle } from './DragContext';
import { AutoGrowTextarea } from './AutoGrowTextarea';
import './task-card-editor.css';

interface Props {
  block: Block;
  onChange: (next: Block) => void;
  onDelete?: () => void;
  isRoot?: boolean;
}

export const UntilBlockEditor: React.FC<Props> = ({ block, onChange, onDelete, isRoot }) => {
  const update = (patch: Partial<Block>) => onChange({ ...block, ...patch });

  const mode: UntilMode = block.until_mode ?? 'model';

  return (
    <div className="tc-block tc-block-until">
      <div className="tc-block-header">
        {!isRoot && <DragHandle id={block.id} />}
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
        <AutoGrowTextarea
          className="tc-text-input tc-text-input--multiline tc-flex-grow"
          placeholder="condition (e.g. 'all tests pass')"
          value={block.until_condition ?? ''}
          onChange={e => update({ until_condition: e.target.value })}
          title={
            mode === 'model'
              ? "Plain English condition; an evaluator model decides yes/no after each iteration"
              : "Expression mode is not yet implemented"
          }
          minRows={1}
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
          <button className="tc-icon-btn tc-icon-btn-delete" onClick={onDelete} title="Delete">×</button>
        )}
      </div>
      <BlockBody
        parentId={block.id}
        body={block.body}
        bodyClassName="tc-block-body-until"
        sequenceLabel="Repeat in order until condition met:"
        allowSchedule={false}
        onChange={body => update({ body })}
      />
    </div>
  );
};
