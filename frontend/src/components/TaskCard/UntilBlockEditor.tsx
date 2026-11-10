/**
 * Editor for an Until block — loop until a model-evaluated condition
 * holds.  See app/agents/block_executor.py::_execute_until and
 * app/agents/until_evaluator.py.
 *
 * Mode 'expression' runs the condition as a shell command after each
 * iteration (app/agents/until_gate.py): exit 0 stops the loop, anything
 * else continues it and the command's output is fed to the next
 * iteration as the rationale.
 */

import React from 'react';
import type { Block, UntilMode, TaskScope } from '../../types/task_card';
import { BlockBody } from './BlockBody';
import { BlockScopeButton } from './BlockScopeButton';
import { SelfImproveSection } from './SelfImproveSection';
import { GatherContractSection } from './GatherContractSection';
import { DragHandle } from './DragContext';
import { AutoGrowTextarea } from './AutoGrowTextarea';
import { RequiredToggle } from './RequiredToggle';
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
          <option value="expression">command exits 0</option>
        </select>
        <AutoGrowTextarea
          className="tc-text-input tc-text-input--multiline tc-flex-grow"
          placeholder={mode === 'model' ? "condition (e.g. 'all tests pass')" : "shell command (e.g. pytest -q)"}
          value={block.until_condition ?? ''}
          onChange={e => update({ until_condition: e.target.value })}
          title={
            mode === 'model'
              ? "Plain English condition; an evaluator model decides yes/no after each iteration"
              : "Shell command run after each iteration under the shell policy; exit status 0 means satisfied, its output is passed to the next iteration"
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
        <select
          className="tc-select"
          value={block.on_failure ?? 'continue'}
          onChange={e => update({ on_failure: e.target.value === 'stop' ? 'stop' : null })}
          title="Failure policy for the steps inside each iteration: continue (later steps still run after a failed one) or stop (halt the iteration at the first failed step)"
        >
          <option value="continue">on fail: continue</option>
          <option value="stop">on fail: stop</option>
        </select>
        {!isRoot && <RequiredToggle block={block} onChange={update} />}
        {onDelete && (
          <button className="tc-icon-btn tc-icon-btn-delete" onClick={onDelete} title="Delete">×</button>
        )}
      </div>
      <div className="tc-block-body tc-block-body-scope-row">
        <BlockScopeButton
          scope={block.scope}
          onChange={(next: TaskScope) => update({ scope: next })}
          title={block.name || 'this Until block'}
        />
        <SelfImproveSection block={block} onChange={update} />
        <GatherContractSection block={block} onChange={update} />
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
