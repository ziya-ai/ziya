/**
 * Editor for a single Task block (leaf).
 * Renders the blue-coded block style from design/task-cards.md.
 */

import React from 'react';
import type { Block, TaskScope } from '../../types/task_card';
import './task-card-editor.css';

interface Props {
  block: Block;
  onChange: (next: Block) => void;
  onDelete?: () => void;
}

const ScopeChip: React.FC<{
  value: string; kind: 'file' | 'tool' | 'skill'; onRemove: () => void;
}> = ({ value, kind, onRemove }) => {
  const icon = kind === 'file' ? '📁' : kind === 'tool' ? '🔧' : '🎓';
  const cls = `tc-chip tc-chip-${kind}`;
  return (
    <span className={cls}>
      {icon} {value}
      <button className="tc-chip-remove" onClick={onRemove} aria-label="remove">×</button>
    </span>
  );
};

const addToScopeList = (
  scope: TaskScope | null | undefined, key: keyof TaskScope, value: string,
): TaskScope => {
  const s: TaskScope = scope ?? { files: [], tools: [], skills: [] };
  if (!value.trim() || s[key].includes(value)) return s;
  return { ...s, [key]: [...s[key], value] };
};

const removeFromScopeList = (
  scope: TaskScope, key: keyof TaskScope, value: string,
): TaskScope => ({ ...scope, [key]: scope[key].filter(v => v !== value) });

export const TaskBlockEditor: React.FC<Props> = ({ block, onChange, onDelete }) => {
  const scope: TaskScope = block.scope ?? { files: [], tools: [], skills: [] };
  const update = (patch: Partial<Block>) => onChange({ ...block, ...patch });
  const updateScope = (s: TaskScope) => update({ scope: s });
  const promptFor = (label: string, cb: (v: string) => void) => {
    const v = window.prompt(label);
    if (v && v.trim()) cb(v.trim());
  };

  return (
    <div className="tc-block tc-block-task">
      <div className="tc-block-header">
        <span className="tc-emoji">{block.emoji ?? '🔵'}</span>
        <input
          className="tc-name-input"
          value={block.name}
          onChange={e => update({ name: e.target.value })}
          placeholder="Task name"
        />
        <span className="tc-block-label">Task</span>
        {onDelete && (
          <button className="tc-icon-btn" onClick={onDelete} title="Delete">⋯</button>
        )}
      </div>
      <div className="tc-block-body">
        <textarea
          className="tc-instructions"
          value={block.instructions ?? ''}
          onChange={e => update({ instructions: e.target.value })}
          placeholder="What should this task do?"
          rows={2}
        />
        <div className="tc-scope-row">
          {scope.files.map(v => (
            <ScopeChip key={`f-${v}`} value={v} kind="file"
              onRemove={() => updateScope(removeFromScopeList(scope, 'files', v))} />
          ))}
          {scope.tools.map(v => (
            <ScopeChip key={`t-${v}`} value={v} kind="tool"
              onRemove={() => updateScope(removeFromScopeList(scope, 'tools', v))} />
          ))}
          {scope.skills.map(v => (
            <ScopeChip key={`s-${v}`} value={v} kind="skill"
              onRemove={() => updateScope(removeFromScopeList(scope, 'skills', v))} />
          ))}
          <button className="tc-chip-add" onClick={() =>
            promptFor('Add file path:', v => updateScope(addToScopeList(scope, 'files', v)))
          }>+ file</button>
          <button className="tc-chip-add" onClick={() =>
            promptFor('Add tool name:', v => updateScope(addToScopeList(scope, 'tools', v)))
          }>+ tool</button>
          <button className="tc-chip-add" onClick={() =>
            promptFor('Add skill id:', v => updateScope(addToScopeList(scope, 'skills', v)))
          }>+ skill</button>
        </div>
      </div>
    </div>
  );
};
