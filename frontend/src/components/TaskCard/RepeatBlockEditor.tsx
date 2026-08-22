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
            placeholder='["item1", "item2"] or {{sibling("plan-id").outputs.NAME.key}}'
            value={block.repeat_for_each_source ?? ''}
            onChange={e => update({ repeat_for_each_source: e.target.value || null })}
            title='Items to iterate over: a JSON array literal, or a template resolved when the loop starts. Preferred: name a prior task&apos;s emit_artifact data part, e.g. {{sibling("plan-id").outputs.roster.slugs}} — parsed strictly (whole-string array). Other templated sources use the first JSON array found in the resolved text. A templated source that resolves to no array fails the block rather than looping over nothing.'
          />
        )}
        <label className="tc-checkbox-label">
          <input
            type="checkbox"
            checked={!!block.repeat_parallel}
            onChange={e => update({ repeat_parallel: e.target.checked })}
          /> parallel
        </label>
        {block.repeat_parallel && (
          <>
            <span className="tc-label-dim">at most</span>
            <input
              type="number" min={0}
              className="tc-num-input"
              placeholder="8"
              value={block.repeat_max_concurrency ?? ''}
              onChange={e => {
                const raw = e.target.value.trim();
                // Empty clears back to the backend default rather than
                // pinning 0, which means unbounded — the opposite intent.
                if (!raw) { update({ repeat_max_concurrency: null }); return; }
                const n = parseInt(raw, 10);
                update({ repeat_max_concurrency: Number.isNaN(n) ? null : Math.max(0, n) });
              }}
              title="Maximum iterations running at once. Blank uses the default (8), which keeps a wide fan-out below provider rate limits. 0 means unbounded — only appropriate when the body does no model work."
            />
            <span className="tc-label-dim">at a time</span>
          </>
        )}
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
