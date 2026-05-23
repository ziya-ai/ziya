/**
 * The top-level Task Card editor.  Renders the header + block tree.
 *
 * Draft-mode only in Slice B: no launch, no save wiring.  Those land
 * in Slice C and beyond.  Callers pass the card state and an onChange
 * callback; this component is fully controlled.
 */

import React from 'react';
import type { TaskCard, Block, BlockType } from '../../types/task_card';
import { BlockEditor } from './BlockEditor';
import {
  makeTaskBlock, makeRepeatBlock, makeParallelBlock,
  makeUntilBlock, makeScheduleBlock,
} from '../../utils/taskCardBlocks';
import './task-card-editor.css';

interface Props {
  card: TaskCard;
  onChange: (next: TaskCard) => void;
  onSave?: () => void;
  onLaunch?: () => void;  // Wired in Slice C.
  saving?: boolean;
}

export const TaskCardEditor: React.FC<Props> = ({
  card, onChange, onSave, onLaunch, saving,
}) => {
  const setRoot = (root: Block) => onChange({ ...card, root });
  const setName = (name: string) => onChange({ ...card, name });
  const setDescription = (description: string) => onChange({ ...card, description });

  const changeRootType = (nextType: BlockType) => {
    if (nextType === card.root.block_type) return;
    // Build a fresh block of the requested type.  We reuse the
    // existing root's name so the user doesn't lose that label.
    const name = card.root.name || 'Root';
    let next: Block;
    if (nextType === 'task') {
      next = makeTaskBlock(name);
    } else if (nextType === 'repeat') {
      next = makeRepeatBlock(name);
    } else if (nextType === 'parallel') {
      next = makeParallelBlock(name);
    } else if (nextType === 'until') {
      next = makeUntilBlock(name);
    } else {
      next = makeScheduleBlock(name);
    }
    // Preserve the existing body when switching between
    // Repeat↔Parallel (both are wrappers with a body).  Task has
    // no body so we drop it.  For Task→Repeat/Parallel we keep the
    // existing Task as a child of the new wrapper so the user
    // doesn't lose their instructions.
    if (card.root.block_type === 'task' && nextType !== 'task') {
      next = { ...next, body: [card.root] };
    } else if (card.root.block_type !== 'task' && nextType !== 'task') {
      next = { ...next, body: card.root.body };
    }
    onChange({ ...card, root: next });
  };

  return (
    <div className="tc-card">
      <div className="tc-card-header">
        <span className="tc-card-emoji">📋</span>
        <input
          className="tc-card-name-input"
          value={card.name}
          onChange={e => setName(e.target.value)}
          placeholder="Task card name"
        />
        <span className="tc-draft-indicator">
          {card.id ? 'saved' : 'draft'}
        </span>
        <div className="tc-card-actions">
          {onSave && (
            <button className="tc-btn tc-btn-secondary" onClick={onSave} disabled={saving}>
              💾 {saving ? 'Saving…' : 'Save'}
            </button>
          )}
          {onLaunch && (
            <button className="tc-btn tc-btn-primary" onClick={onLaunch} disabled>
              ▶ Launch (Slice C)
            </button>
          )}
        </div>
      </div>
      <input
        className="tc-card-description-input"
        value={card.description}
        onChange={e => setDescription(e.target.value)}
        placeholder="Optional description"
      />
      <div className="tc-root-type-row">
        <span className="tc-label-dim">Root block:</span>
        <select
          className="tc-select"
          value={card.root.block_type}
          onChange={e => changeRootType(e.target.value as BlockType)}
        >
          <option value="task">🔵 Task</option>
          <option value="repeat">🔁 Repeat</option>
          <option value="parallel">⚡ Parallel</option>
          <option value="until">🔄 Until</option>
          <option value="schedule">⏰ Schedule</option>
        </select>
      </div>
      <div className="tc-card-canvas">
        <BlockEditor block={card.root} onChange={setRoot} />
      </div>
    </div>
  );
};
