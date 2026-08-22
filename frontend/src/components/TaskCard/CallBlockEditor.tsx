/**
 * Editor for a Call block — invoke a NAMED unit of work defined
 * elsewhere: another task card in this project, or a named file task
 * from tasks.yaml.  See app/agents/task_call.py::resolve_call_target
 * and app/agents/block_executor.py::_execute_call.
 *
 * A leaf here: the callee's tree lives in the callee's own definition
 * and is resolved server-side at run time, which is the entire point of
 * a call (editing the callee changes every caller).  So this editor
 * carries only the reference, and the body is deliberately absent.
 *
 * The target is stored as a NAME/id string, not a resolved pointer.
 * ``_resolve_card`` accepts a card id OR a case-insensitive card name,
 * so this uses a free-text field backed by a datalist rather than a
 * <select> of ids: a select keyed on id would fail to match — and
 * therefore silently blank — the many existing cards that store the
 * callee's name.  Resolution is reported inline instead.
 */

import React from 'react';
import type { Block } from '../../types/task_card';
import type { TaskCard } from '../../types/task_card';
import { useProject } from '../../context/ProjectContext';
import { taskCardApi } from '../../services/taskCardApi';
import { DragHandle } from './DragContext';
import './task-card-editor.css';

interface Props {
  block: Block;
  onChange: (next: Block) => void;
  onDelete?: () => void;
  isRoot?: boolean;
}

type Kind = 'card' | 'file_task';

/**
 * Per-project card list, shared across every Call block on the canvas.
 * A card built out of six calls would otherwise issue six identical
 * list requests on mount.  Cached by promise so concurrent mounts share
 * one flight; dropped on failure so a transient error can be retried.
 */
const cardListCache = new Map<string, Promise<TaskCard[]>>();

const loadCards = (projectId: string): Promise<TaskCard[]> => {
  const hit = cardListCache.get(projectId);
  if (hit) return hit;
  const p = taskCardApi.list(projectId).catch(e => {
    cardListCache.delete(projectId);
    throw e;
  });
  cardListCache.set(projectId, p);
  return p;
};

export const CallBlockEditor: React.FC<Props> = ({
  block, onChange, onDelete, isRoot,
}) => {
  const update = (patch: Partial<Block>) => onChange({ ...block, ...patch });

  const { currentProject } = useProject();
  const projectId = currentProject?.id;
  const [cards, setCards] = React.useState<TaskCard[] | null>(null);

  React.useEffect(() => {
    if (!projectId) return;
    let live = true;
    loadCards(projectId)
      .then(list => { if (live) setCards(list); })
      .catch(() => { if (live) setCards(null); });
    return () => { live = false; };
  }, [projectId]);

  // None means "card" on the backend, so an unset kind is shown as card
  // rather than as an empty select.
  const kind: Kind = (block.call_target_kind ?? 'card') as Kind;
  const target = block.call_target ?? '';

  // Resolution mirrors _resolve_card: exact id first, then a
  // case-insensitive name match.  Advisory only — the authoritative
  // check runs server-side at launch (and card validation reports it),
  // so an unresolved target never blocks editing.
  const match = React.useMemo(() => {
    if (kind !== 'card' || !cards || !target.trim()) return null;
    const wanted = target.trim().toLowerCase();
    return (
      cards.find(c => c.id === target.trim()) ??
      cards.find(c => (c.name ?? '').toLowerCase() === wanted) ??
      null
    );
  }, [cards, kind, target]);

  const listId = `tc-call-targets-${block.id}`;

  return (
    <div className="tc-block tc-block-call">
      <div className="tc-block-header">
        {!isRoot && <DragHandle id={block.id} />}
        <span className="tc-emoji">📞</span>
        <input
          className="tc-name-input"
          value={block.name}
          onChange={e => update({ name: e.target.value })}
          placeholder="Call name"
        />
        <span className="tc-block-label tc-block-label-call">Call</span>
        {onDelete && (
          <button className="tc-icon-btn tc-icon-btn-delete" onClick={onDelete} title="Delete">×</button>
        )}
      </div>
      <div className="tc-block-body tc-block-body-call">
        <div className="tc-call-row">
          <select
            className="tc-select"
            value={kind}
            onChange={e => update({
              call_target_kind: e.target.value === 'file_task' ? 'file_task' : 'card',
            })}
            title="Which namespace the target is resolved in"
          >
            <option value="card">task card</option>
            <option value="file_task">file task (tasks.yaml)</option>
          </select>
          <input
            className="tc-text-input"
            value={target}
            list={kind === 'card' ? listId : undefined}
            onChange={e => update({ call_target: e.target.value })}
            placeholder={kind === 'card'
              ? 'card name or id'
              : 'task name in tasks.yaml'}
            spellCheck={false}
          />
          {kind === 'card' && cards && (
            <datalist id={listId}>
              {cards
                .filter(c => c.id !== undefined)
                .map(c => <option key={c.id} value={c.name || c.id} />)}
            </datalist>
          )}
        </div>
        {kind === 'card' && target.trim() !== '' && cards && (
          <div className={`tc-call-status${match ? '' : ' tc-call-status-bad'}`}>
            {match
              ? `✓ resolves to “${match.name || match.id}”`
              : '⚠ no card in this project matches that name or id'}
          </div>
        )}
        <div className="tc-call-hint">
          Runs the target inline in this run; its artifact becomes this
          block's artifact. Permissions do not cross the boundary in
          either direction — the callee runs under its own scope.
        </div>
      </div>
    </div>
  );
};
