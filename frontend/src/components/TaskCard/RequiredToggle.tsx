/**
 * "required" toggle for a block header.
 *
 * Block.required (app/models/task_card.py) decides whether this block's
 * failure counts against its enclosing container: unchecked, a lane can
 * fail without failing a Parallel, tripping an on_failure=stop sequence,
 * or failing a sequence it ends.  The field existed and the executor
 * honoured it, but no editor surface could set it — a card could only say
 * required: false by hand-editing its JSON.
 *
 * Rendered by each non-root block editor (Task / Call / Repeat / Parallel /
 * Until).  Re-checking removes the key rather than writing true: the
 * default is true, so a saved card stays minimal.
 */
import React from 'react';
import type { Block } from '../../types/task_card';
import './task-card-editor.css';

interface Props {
  block: Block;
  onChange: (patch: Partial<Block>) => void;
}

export const RequiredToggle: React.FC<Props> = ({ block, onChange }) => {
  const required = block.required !== false;
  return (
    <label
      className={`tc-checkbox-label tc-required-toggle${required ? '' : ' tc-required-toggle--optional'}`}
      title={required
        ? 'Required: if this block fails, its container fails (and an on_failure=stop sequence halts here). Uncheck to let it fail without stopping the rest.'
        : 'Optional: this block may fail without failing its container. The failure is still recorded in the container\'s decisions.'}
    >
      <input
        type="checkbox"
        data-testid="tc-required"
        checked={required}
        onChange={e => onChange({ required: e.target.checked ? undefined : false })}
      />
      {required ? 'required' : 'optional'}
    </label>
  );
};
