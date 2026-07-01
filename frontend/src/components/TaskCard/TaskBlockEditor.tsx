/**
 * Editor for a single Task block (leaf).
 * Renders the blue-coded block style from design/task-cards.md.
 */

 import React, { useState } from 'react';
 import type { Block, TaskScope, ScopeEntry } from '../../types/task_card';
 import { useProject } from '../../context/ProjectContext';
 import { PermissionsDialog, PermissionEntry, PermissionsSavePayload } from '../Permissions/PermissionsDialog';
 import { DirectoryBrowserModal } from '../DirectoryBrowserModal';
import { AutoGrowTextarea } from './AutoGrowTextarea';
import { DragHandle } from './DragContext';
import './task-card-editor.css';

interface Props {
  block: Block;
  onChange: (next: Block) => void;
  onDelete?: () => void;
  isRoot?: boolean;
}

const ScopeChip: React.FC<{
  value: string; kind: 'file' | 'tool' | 'skill'; onRemove: () => void;
  label?: string;
}> = ({ value, kind, onRemove, label }) => {
  const icon = kind === 'file' ? '📁' : kind === 'tool' ? '🔧' : '🎓';
  const cls = `tc-chip tc-chip-${kind}`;
  return (
    <span className={cls}>
      {icon} {label ?? value}
      <button className="tc-chip-remove" onClick={onRemove} aria-label="remove">×</button>
    </span>
  );
};

const removeFromScopeList = (
  scope: TaskScope, key: keyof TaskScope, value: string,
): TaskScope => ({ ...scope, [key]: scope[key].filter(v => v !== value) });

export const TaskBlockEditor: React.FC<Props> = ({ block, onChange, onDelete, isRoot }) => {
  const scope: TaskScope = block.scope ?? { paths: [], tools: [], skills: [] };
  const { skills: availableSkills } = useProject();
  const update = (patch: Partial<Block>) => onChange({ ...block, ...patch });
  const updateScope = (s: TaskScope) => update({ scope: s });

  // ── Permissions / cwd dialog state ───────────────────────
  const [permsOpen, setPermsOpen] = useState(false);
  const [cwdPickerOpen, setCwdPickerOpen] = useState(false);

  const entries: PermissionEntry[] = scope.paths ?? [];

  // Look up the friendly name for a skill id.  Without this, a
  // skill stored by its full backend id (e.g.
  // ``project-hot-patch-static-assets-4523b36a8fc8``) renders the
  // raw id in the chip — confusing the user into thinking the
  // skill has been mis-renamed.
  const skillLabel = (id: string): string =>
    availableSkills.find(s => s.id === id)?.name ?? id;

  // Single combined handler. The dialog now emits one payload with
  // all four pieces of state, so we apply them in one updateScope call
  // rather than four sequential updates that would each see a stale
  // closure-captured `scope` and clobber each other's writes.
  const onSavePermsCombined = (payload: PermissionsSavePayload) => {
    updateScope({
      ...scope,
      paths: payload.entries.map(e => ({
        path: e.path,
        is_dir: !!e.is_dir,
        read: !!e.read,
        write: !!e.write,
        context: !!e.context && !e.is_dir,
      })) as ScopeEntry[],
      tools: payload.tools,
      skills: payload.skills,
      shell_commands: payload.shellCommands,
    });
  };

  const pathsCount = entries.length;
  const writableCount = entries.filter(e => e.write).length;
  const contextCount = entries.filter(e => e.context).length;
  const toolsCount = scope.tools.length;
  const skillsCount = scope.skills.length;
  const shellCount = (scope.shell_commands ?? []).length;

  return (
    <div className="tc-block tc-block-task">
      <div className="tc-block-header">
        {!isRoot && <DragHandle id={block.id} />}
        <span className="tc-emoji">{block.emoji ?? '🔵'}</span>
        <input
          className="tc-name-input"
          value={block.name}
          onChange={e => update({ name: e.target.value })}
          placeholder="Task name"
        />
        <span className="tc-block-label">Task</span>
        {onDelete && (
          <button className="tc-icon-btn tc-icon-btn-delete" onClick={onDelete} title="Delete">×</button>
        )}
      </div>
      <div className="tc-block-body">
        <AutoGrowTextarea
          className="tc-instructions"
          value={block.instructions ?? ''}
          onChange={e => update({ instructions: e.target.value })}
          placeholder="What should this task do?"
          minRows={2}
        />
        <button
          type="button"
          className="tc-perms-row"
          onClick={() => setPermsOpen(true)}
          title="Manage file, tool, and skill permissions for this task"
        >
          <span className="tc-perms-icon">📁</span>
          <span className="tc-perms-label">Permissions</span>
          <span className="tc-perms-summary">
            {(() => {
              const parts: string[] = [];
              if (pathsCount) {
                let p = `${pathsCount} file${pathsCount === 1 ? '' : 's'}`;
                const sub: string[] = [];
                if (writableCount) sub.push(`${writableCount} W`);
                if (contextCount) sub.push(`${contextCount} Ctx`);
                if (sub.length) p += ` (${sub.join(', ')})`;
                parts.push(p);
              }
              if (toolsCount) parts.push(`${toolsCount} tool${toolsCount === 1 ? '' : 's'}`);
              if (skillsCount) parts.push(`${skillsCount} skill${skillsCount === 1 ? '' : 's'}`);
              if (shellCount) parts.push(`${shellCount} shell cmd${shellCount === 1 ? '' : 's'}`);
              return parts.join(' · ');
            })()}
          </span>
          <span className="tc-perms-chevron" aria-hidden>›</span>
        </button>
        <div className="tc-scope-row">
          {/* Read-only chips for set tools / skills.  Add/remove now
              happens inside the Permissions dialog — these chips just
              surface the current grants for at-a-glance review.
              Click × on a chip to revoke locally. */}
          {scope.tools.map(v => (
            <ScopeChip key={`t-${v}`} value={v} kind="tool"
              onRemove={() => updateScope(removeFromScopeList(scope, 'tools', v))} />
          ))}
          {scope.skills.map(v => (
            <ScopeChip key={`s-${v}`} value={v} kind="skill" label={skillLabel(v)}
              onRemove={() => updateScope(removeFromScopeList(scope, 'skills', v))} />
          ))}
        </div>

        <details className="tc-advanced">
          <summary>
            ▸ Advanced
            {scope.cwd && (
              <span className="tc-advanced-summary">
                {' '}(working directory: {scope.cwd})
              </span>
            )}
          </summary>
          <div className="tc-cwd-row">
            <span className="tc-cwd-label" title="Working directory for this task. Must be inside the project root.">
              📂 Working directory:
            </span>
            <code className="tc-cwd-value" title={scope.cwd || '(project root)'}>
              {scope.cwd || '(project root)'}
            </code>
            <button
              className="tc-chip-add"
              onClick={() => setCwdPickerOpen(true)}
              title="Choose an alternate working directory"
            >
              change…
            </button>
            {scope.cwd && (
              <button
                className="tc-chip-add"
                onClick={() => updateScope({ ...scope, cwd: null })}
                title="Reset to project root"
              >
                reset
              </button>
            )}
          </div>
        </details>
      </div>
      <PermissionsDialog
        open={permsOpen}
        title={`Permissions — ${block.name || 'Task'}`}
        entries={entries}
        tools={scope.tools}
        skills={scope.skills}
        shellCommands={scope.shell_commands ?? []}
        onClose={() => setPermsOpen(false)}
        onSave={onSavePermsCombined}
      />
      <DirectoryBrowserModal
        open={cwdPickerOpen}
        onClose={() => setCwdPickerOpen(false)}
        title="Select working directory"
        subtitle="Choose a folder for this task to run in"
        confirmLabel="Use this folder"
        busyLabel="Setting…"
        onSelect={async (path) => {
          updateScope({ ...scope, cwd: path });
          setCwdPickerOpen(false);
        }}
      />
    </div>
  );
};
