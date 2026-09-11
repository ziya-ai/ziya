/**
 * Editor for an Ask block (amber leaf) — a human-in-the-loop checkpoint.
 *
 * The run holds at this block with status 'awaiting_input' until a human
 * answers in the inline tile (AskAnswerPanel).  A leaf like State: no
 * body, and placement does not change its meaning.
 *
 *   - Question (required): what the operator is asked.  An empty question
 *     would hold the run on a blank prompt, so it is the one field the
 *     validator requires (app/utils/task_card_validation.py).
 *   - Variable (optional): binds the free-text answer as {{var.NAME}} for
 *     later blocks.  Unset means the answer reaches them only as prose
 *     context — the common "should I go on?" case.
 *   - Choices (optional): fixed answer options.  Unset means free text.
 */

import React from 'react';
import type { Block } from '../../types/task_card';
import { AutoGrowTextarea } from './AutoGrowTextarea';
import { DragHandle } from './DragContext';
import './task-card-editor.css';

interface Props {
  block: Block;
  onChange: (next: Block) => void;
  onDelete?: () => void;
  isRoot?: boolean;
}

export const AskBlockEditor: React.FC<Props> = ({ block, onChange, onDelete, isRoot }) => {
  const update = (patch: Partial<Block>) => onChange({ ...block, ...patch });

  const choices = block.ask_choices ?? [];
  const setChoice = (idx: number, value: string) => {
    const next = choices.slice();
    next[idx] = value;
    update({ ask_choices: next });
  };
  const addChoice = () => update({ ask_choices: [...choices, ''] });
  const removeChoice = (idx: number) => {
    const next = choices.slice();
    next.splice(idx, 1);
    // Empty list -> null, so the block reads as free-text rather than
    // "choices, but none" — the two are different answer shapes downstream.
    update({ ask_choices: next.length ? next : null });
  };

  return (
    <div className="tc-block tc-block-ask">
      <div className="tc-block-header">
        {!isRoot && <DragHandle id={block.id} />}
        <span className="tc-emoji">🙋</span>
        <input
          className="tc-name-input"
          value={block.name}
          onChange={e => update({ name: e.target.value })}
          placeholder="Checkpoint name"
        />
        <span className="tc-block-label tc-block-label-ask">Ask</span>
        {onDelete && (
          <button className="tc-icon-btn tc-icon-btn-delete" onClick={onDelete} title="Delete">×</button>
        )}
      </div>
      <div className="tc-block-body tc-block-body-ask">
        <div className="tc-sequence-label">Question for the operator:</div>
        <AutoGrowTextarea
          className="tc-instructions"
          value={block.ask_question ?? ''}
          onChange={e => update({ ask_question: e.target.value })}
          placeholder="e.g. &quot;The migration is ready. Proceed to apply it to production?&quot;"
          minRows={2}
        />

        <div className="tc-ask-field">
          <label className="tc-ask-field__label">
            Bind answer as variable (optional)
          </label>
          <input
            className="tc-state-key"
            value={block.ask_variable ?? ''}
            onChange={e => update({ ask_variable: e.target.value || null })}
            placeholder="e.g. approval_note — read later as {{var.approval_note}}"
            spellCheck={false}
          />
        </div>

        <details className="tc-state-advanced">
          <summary>▸ Fixed choices ({choices.length}) — leave empty for free text</summary>
          {choices.length === 0 && (
            <div className="tc-state-empty">
              No choices — the operator answers in free text.
            </div>
          )}
          {choices.map((c, idx) => (
            <div className="tc-state-row" key={idx}>
              <input
                className="tc-state-val"
                value={c}
                onChange={e => setChoice(idx, e.target.value)}
                placeholder="choice label"
              />
              <button
                className="tc-icon-btn tc-icon-btn-delete"
                onClick={() => removeChoice(idx)}
                title="Remove choice"
              >×</button>
            </div>
          ))}
          <div className="tc-add-row">
            <button className="tc-add-btn" onClick={addChoice}>+ Choice</button>
          </div>
        </details>

        <div className="tc-state-hint">
          The run holds here with status “awaiting_input” until answered.
          Approve continues; reject fails this block, so the enclosing
          on-failure policy decides what happens next.
        </div>
      </div>
    </div>
  );
};

export default AskBlockEditor;
