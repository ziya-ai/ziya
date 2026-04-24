/**
 * The top-level Task Card editor.  Renders the header + block tree.
 *
 * Draft-mode only in Slice B: no launch, no save wiring.  Those land
 * in Slice C and beyond.  Callers pass the card state and an onChange
 * callback; this component is fully controlled.
 */

import React from 'react';
import type { TaskCard, Block } from '../../types/task_card';
import { BlockEditor } from './BlockEditor';
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
      <div className="tc-card-canvas">
        <BlockEditor block={card.root} onChange={setRoot} />
      </div>
    </div>
  );
};
