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
import { TaskCardDragProvider } from './DragContext';
import { taskCardApi, type CardScopeStatus } from '../../services/taskCardApi';
import { makeGroupBlock } from '../../utils/taskCardBlocks';
import './task-card-editor.css';

interface Props {
  card: TaskCard;
  onChange: (next: TaskCard) => void;
  // Project owning the card — needed to fetch escalation-approval status.
  projectId?: string;
  onSave?: () => void;
  onLaunch?: () => void;  // Wired in Slice C.
  saving?: boolean;
  // Notifies the parent (deck library) whenever this editor re-fetches the
  // card's escalation/signature status — so the deck-list "Unsigned" badge
  // refreshes in lock-step with the in-editor warning after a re-check.
  onScopeStatusChange?: (cardId: string, status: CardScopeStatus | null) => void;
}

export const TaskCardEditor: React.FC<Props> = ({
  card, onChange, projectId, onSave, onLaunch, saving, onScopeStatusChange,
}) => {
  // Escalation-approval status (ASR F-001). A saved card whose blocks request
  // shell/write escalation shows which blocks are unsigned and the exact
  // `ziya-approve` command to activate them. Only meaningful for a saved card
  // (the store keys on persisted block ids); skipped for unsaved drafts.
  //
  // NOTE: unlike the shell-config GUI, the card path needs only a "re-check",
  // NOT a server restart. Shell-config escalations are read once into a
  // long-lived shell subprocess's env at spawn, so a signature written after
  // spawn requires restarting that subprocess to take effect. Card escalations
  // are different: execute_task_block consults the signed approval store fresh
  // at each launch (app/utils/scope_approvals.authorize_scope), so a newly
  // signed record is picked up on the next run with no restart. Do not add a
  // "restart" affordance here — refreshScopeStatus alone reflects reality.
  const [scopeStatus, setScopeStatus] = React.useState<CardScopeStatus | null>(null);
  const refreshScopeStatus = React.useCallback(async () => {
    if (!projectId || !card.id) {
      setScopeStatus(null);
      onScopeStatusChange?.(card.id, null);
      return;
    }
    try {
      const st = await taskCardApi.scopeStatus(projectId, card.id);
      setScopeStatus(st);
      onScopeStatusChange?.(card.id, st);
    } catch {
      setScopeStatus(null);  // status is advisory; never block editing on it
    }
  }, [projectId, card.id, onScopeStatusChange]);
  // Re-check whenever the card id changes or its scope-bearing content changes.
  React.useEffect(() => { void refreshScopeStatus(); },
    [refreshScopeStatus, JSON.stringify(card.root)]);

  const setRoot = (root: Block) => onChange({ ...card, root });
  const setName = (name: string) => onChange({ ...card, name });
  const setDescription = (description: string) => onChange({ ...card, description });

  // The card root is always an invisible Group (a run-once sequence) so
  // the canvas presents an ordered drop list: a State can be added first
  // and have operators follow it, and a State can precede a loop without
  // entering the loop's scope.  Legacy cards saved with a bare root are
  // wrapped once on load — the old root becomes the group's first child,
  // so no data or semantics are lost.
  //
  // Exception: a 'schedule' root is a top-level recurring trigger, not a
  // step in a sequence — the backend scheduler detects it via
  // root.block_type === 'schedule' (task_scheduler.py).  Wrapping it would
  // hide it from the scheduler, so schedule roots are left unwrapped.
  React.useEffect(() => {
    if (card.root.block_type === 'group' || card.root.block_type === 'schedule') return;
    const wrapped = makeGroupBlock();
    onChange({ ...card, root: { ...wrapped, body: [card.root] } });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [card.root.block_type]);

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
      {scopeStatus?.anyUnapproved && (
        <div className="tc-scope-approval-warning" role="alert">
          <div className="tc-scope-approval-title">
            🔒 Unsigned privilege escalation — not active
          </div>
          <div className="tc-scope-approval-body">
            This card requests shell/write permissions beyond the default safe
            set. Until approved, these blocks run at the default floor. Approval
            requires a privileged gesture the agent cannot perform.
          </div>
          {scopeStatus.blocks.filter(b => !b.authorized).map(b => (
            <div key={b.blockId} className="tc-scope-approval-block">
              <div className="tc-scope-approval-block-name">
                ⚠ {b.name || b.blockId}
              </div>
              {Object.entries(b.escalation).map(([field, vals]) => (
                <div key={field} className="tc-scope-approval-detail">
                  {field}: {vals.join(', ')}
                </div>
              ))}
              <code className="tc-scope-approval-cmd">{b.signCommand}</code>
            </div>
          ))}
          <button
            className="tc-btn tc-btn-secondary tc-scope-approval-recheck"
            onClick={() => void refreshScopeStatus()}
          >
            ↻ Re-check (after signing)
          </button>
        </div>
      )}
      <input
        className="tc-card-description-input"
        value={card.description}
        onChange={e => setDescription(e.target.value)}
        placeholder="Optional description"
      />
      <div className="tc-card-canvas">
        <TaskCardDragProvider root={card.root} onRootChange={setRoot}>
          <BlockEditor block={card.root} onChange={setRoot} isRoot />
        </TaskCardDragProvider>
      </div>
    </div>
  );
};
