/**
 * BlockScopeButton — compact "Permissions" affordance shared by every
 * hierarchy layer above the leaf Task: container blocks (Repeat,
 * Parallel, Until, Schedule, Group), the card itself, and the deck
 * (project-wide) baseline.
 *
 * Renders the same click-to-open PermissionsDialog pattern
 * TaskBlockEditor already uses for a Task's own scope, generalized so
 * every level of the hierarchy gets an identical, low-friction way to
 * grant paths/tools/skills/shell-commands that flow down additively
 * to every leaf Task beneath it (see app/models/task_card.py::
 * merge_scopes and app/agents/block_executor.py).
 */

import React, { useState } from 'react';
import type { TaskScope, ScopeEntry } from '../../types/task_card';
import { PermissionsDialog, PermissionEntry, PermissionsSavePayload } from '../Permissions/PermissionsDialog';
import { DirectoryBrowserModal } from '../DirectoryBrowserModal';
import { ModelTierPicker } from './ModelTierPicker';
import './task-card-editor.css';

interface Props {
  scope: TaskScope | null | undefined;
  onChange: (next: TaskScope) => void;
  title: string;
  /** Compact label shown before the summary — defaults to "Permissions". */
  label?: string;
  /**
   * Whether to show the model-tier picker beneath the permissions row.
   * Defaults to true: a tier set at this level flows down to every task
   * beneath it (the "smart supervisor over cheap executors" recipe).
   */
  showModel?: boolean;
}

export const BlockScopeButton: React.FC<Props> = ({ scope, onChange, title, label, showModel = true }) => {
  const [open, setOpen] = useState(false);
  const [cwdOpen, setCwdOpen] = useState(false);
  const effective: TaskScope = scope ?? { paths: [], tools: [], skills: [] };
  const entries: PermissionEntry[] = effective.paths ?? [];

  const onSave = (payload: PermissionsSavePayload) => {
    onChange({
      ...effective,
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
      shell_timeout_secs: payload.shellTimeoutSecs,
    });
  };

  const pathsCount = entries.length;
  const writableCount = entries.filter(e => e.write).length;
  const contextCount = entries.filter(e => e.context).length;
  const toolsCount = (effective.tools ?? []).length;
  const skillsCount = (effective.skills ?? []).length;
  const shellCount = (effective.shell_commands ?? []).length;

  const summary = (() => {
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
    if (effective.cwd) parts.push(`cwd: ${effective.cwd}`);
    return parts.join(' · ');
  })();

  return (
    <div className="tc-block-scope-group">
      <button
        type="button"
        className="tc-perms-row"
        onClick={() => setOpen(true)}
        title={`Manage permissions for ${title} — grants flow additively to every task beneath it`}
      >
        <span className="tc-perms-icon">📁</span>
        <span className="tc-perms-label">{label ?? 'Permissions'}</span>
        <span className="tc-perms-summary">{summary}</span>
        <span className="tc-perms-chevron" aria-hidden>›</span>
      </button>
      {showModel && (
        <ModelTierPicker scope={effective} onChange={onChange} />
      )}
      {/* Working directory flows down like model_tier (merge_scopes:
          innermost non-null wins), so it belongs on every layer, not
          only the leaf.  Same row TaskBlockEditor renders. */}
      <div className="tc-cwd-row">
        <span className="tc-cwd-label" title="Working directory for every task beneath this block (a task may override it). Must be inside the project root.">
          📂 Working directory:
        </span>
        <code className="tc-cwd-value" data-testid="tc-scope-cwd" title={effective.cwd || '(inherited / project root)'}>
          {effective.cwd || '(inherited / project root)'}
        </code>
        <button className="tc-chip-add" onClick={() => setCwdOpen(true)} title="Choose a working directory">
          change…
        </button>
        {effective.cwd && (
          <button className="tc-chip-add" onClick={() => onChange({ ...effective, cwd: null })} title="Inherit from the enclosing block (or project root)">
            reset
          </button>
        )}
      </div>
      <DirectoryBrowserModal
        open={cwdOpen}
        onClose={() => setCwdOpen(false)}
        title="Select working directory"
        subtitle={`Tasks beneath ${title} will run in this folder`}
        confirmLabel="Use this folder"
        busyLabel="Setting…"
        onSelect={async (path) => { onChange({ ...effective, cwd: path }); setCwdOpen(false); }}
      />
      <PermissionsDialog
        open={open}
        title={`Permissions — ${title}`}
        entries={entries}
        tools={effective.tools}
        skills={effective.skills}
        shellCommands={effective.shell_commands ?? []}
        shellTimeoutSecs={effective.shell_timeout_secs}
        onClose={() => setOpen(false)}
        onSave={onSave}
      />
    </div>
  );
};

export default BlockScopeButton;
