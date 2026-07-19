/**
 * Editor for a Repeat block (yellow wrapper).  Recurses via BlockEditor.
 */

import React from 'react';
import type { Block, RepeatMode, PropagateMode, TaskScope } from '../../types/task_card';
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

export const RepeatBlockEditor: React.FC<Props> = ({ block, onChange, onDelete, isRoot }) => {
  const update = (patch: Partial<Block>) => onChange({ ...block, ...patch });

  const mode: RepeatMode = block.repeat_mode ?? 'count';
  const propagate: PropagateMode = block.repeat_propagate ?? 'last';

  return (
    <div className="tc-block tc-block-repeat">
      <div className="tc-block-header">
        {!isRoot && <DragHandle id={block.id} />}
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
            <span className="tc-label-dim">until summary contains</span>
            <input
              type="text"
              className="tc-text-input"
              placeholder="(or leave blank for: first success)"
              value={block.repeat_until ?? ''}
              onChange={e => update({ repeat_until: e.target.value || null })}
              title="Substring the iteration's summary must contain (case-insensitive) to terminate the loop. Leave blank to stop on the first non-failed iteration."
            />
          </>
        )}
        {mode === 'for_each' && (
          <input
            type="text"
            className="tc-text-input tc-flex-grow"
            placeholder='["item1", "item2"] or {{sibling("plan-block-id")}}'
            value={block.repeat_for_each_source ?? ''}
            onChange={e => update({ repeat_for_each_source: e.target.value || null })}
            title='Items to iterate over: a JSON array literal, or a template like {{sibling("plan-block-id")}} resolved when the loop starts. The first JSON array found in the resolved text drives the iterations; if none is found, falls back to count.'
          />
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
          title="How much prior-iteration context the model sees on each iteration"
        >
          <option value="none">isolated (no context)</option>
          <option value="last">previous result</option>
          <option value="all">all prior results</option>
        </select>
        <select
          className="tc-select"
          value={block.on_failure ?? 'continue'}
          onChange={e => update({ on_failure: e.target.value === 'stop' ? 'stop' : null })}
          title="Failure policy for the steps inside each iteration: continue (later steps still run after a failed one) or stop (halt the iteration at the first failed step)"
        >
          <option value="continue">on fail: continue</option>
          <option value="stop">on fail: stop</option>
        </select>
        {onDelete && (
          <button className="tc-icon-btn tc-icon-btn-delete" onClick={onDelete} title="Delete">×</button>
        )}
      </div>
      <div className="tc-block-body tc-block-body-scope-row">
        <BlockScopeButton
          scope={block.scope}
          onChange={(next: TaskScope) => update({ scope: next })}
          title={block.name || 'this Repeat block'}
        />
      </div>
      <BlockBody
        parentId={block.id}
        body={block.body}
        bodyClassName="tc-block-body-repeat"
        sequenceLabel="In order:"
        onChange={body => update({ body })}
      />
    </div>
  );
};
