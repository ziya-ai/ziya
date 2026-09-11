/**
 * The top-level Task Card editor.  Renders the header + block tree.
 *
 * Draft-mode only in Slice B: no launch, no save wiring.  Those land
 * in Slice C and beyond.  Callers pass the card state and an onChange
 * callback; this component is fully controlled.
 */

import React from 'react';
import type { TaskCard, Block, BlockType, TaskScope } from '../../types/task_card';
import { BlockEditor } from './BlockEditor';
import { BlockOutline } from './BlockOutline';
import { BlockScopeButton } from './BlockScopeButton';
import { SelfImproveSection } from './SelfImproveSection';
import { TaskCardDragProvider } from './DragContext';
import { taskCardApi, type CardScopeStatus } from '../../services/taskCardApi';
import { CARD_SCOPE_REFRESH_EVENT } from './useCardSignatureStatus';
import {
  makeGroupBlock, makeBlock, updateBlockById, removeBlockById, appendChildBlock,
} from '../../utils/taskCardBlocks';
import { flattenBlocks, firstSelectableRow } from './runMapModel';
import './task-card-editor.css';

// Types offered by the outline's top-level "add".  Group is excluded
// because the outline renders groups chromeless (their children appear
// at the group's depth), so an empty new group would be invisible;
// schedule is excluded because it is a root-only trigger.
const ADDABLE_TYPES: BlockType[] = [
  'task', 'repeat', 'parallel', 'until', 'state', 'call', 'ask',
];

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
  // True when `card` is an UNSAVED spec: an AI-authored proposal being
  // previewed, or a draft not yet created.  Escalation is then resolved
  // through the stateless scope-preview endpoint instead of the by-id
  // status endpoint.  Without this the proposal preview passed its
  // synthetic 'draft' id to /task-cards/draft/scope-status, took the 404
  // on every re-check, and showed no escalation warning at all.
  previewMode?: boolean;
  // 'tree' (default) renders every block's editor, nested and expanded —
  // the shape the chat proposal panel wants for a card you are reading
  // once.  'outline' renders a foldable spine beside ONE block's editor,
  // for the deck, where a card is revisited and edited rather than read
  // top to bottom.  Same card, same editors; only the arrangement differs.
  layout?: 'tree' | 'outline';
}

export const TaskCardEditor: React.FC<Props> = ({
  card, onChange, projectId, onSave, onLaunch, saving, onScopeStatusChange,
  previewMode, layout = 'tree',
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
  const [scopeCheckPending, setScopeCheckPending] = React.useState(false);
  const [scopeCheckError, setScopeCheckError] = React.useState<string | null>(null);
  // Latest card, read by refreshScopeStatus WITHOUT being a dependency of
  // it.  Putting the mutable fields in the dep list would change the
  // callback's identity on every keystroke, and in preview mode that is a
  // scope-preview POST per character typed.
  const cardRef = React.useRef(card);
  cardRef.current = card;
  const refreshScopeStatus = React.useCallback(async (opts?: { manual?: boolean }) => {
    // Check previewMode FIRST: an unsaved spec carries a synthetic id
    // ('draft') that is truthy but not fetchable, so the old id-only guard
    // let it through to a 404.
    if (!projectId || (!previewMode && !card.id)) {
      setScopeStatus(null);
      onScopeStatusChange?.(card.id, null);
      return;
    }
    if (opts?.manual) { setScopeCheckPending(true); setScopeCheckError(null); }
    try {
      const c = cardRef.current;
      const st = previewMode
        ? await taskCardApi.scopePreview(projectId, {
            name: c.name, description: c.description, root: c.root,
            scope: c.scope, tags: c.tags, is_template: c.is_template,
          })
        : await taskCardApi.scopeStatus(projectId, card.id);
      setScopeStatus(st);
      onScopeStatusChange?.(card.id, st);
      // Tell every other surface showing this card to re-check.  Signing
      // happens out of band (`ziya-approve` in a terminal), so nothing
      // in-app marks the transition: without this broadcast an open
      // inline tile keeps its stale "unsigned" badge until it remounts,
      // which is the "only shows after you exit and come back" defect.
      // Skipped in preview mode — an unsaved spec has no persisted id
      // for another surface to key on.
      if (!previewMode && card.id) {
        window.dispatchEvent(new CustomEvent(CARD_SCOPE_REFRESH_EVENT, {
          detail: { cardId: card.id },
        }));
      }
    } catch (e) {
      setScopeStatus(null);  // status is advisory; never block editing on it
      if (opts?.manual) {
        setScopeCheckError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      if (opts?.manual) setScopeCheckPending(false);
    }
  }, [projectId, previewMode, card.id, onScopeStatusChange]);
  // Re-check when the card id or its scope-bearing content changes.
  //
  // Debounced: the root dep changes on every keystroke inside an
  // instructions textarea and each change is a network round trip, so the
  // undebounced version fired a request per character.
  //
  // card.scope is included deliberately — a CARD-level grant is a layer of
  // every leaf's effective scope, so editing it changes the escalation, yet
  // it previously triggered no re-check and the banner went stale.
  React.useEffect(() => {
    const t = setTimeout(() => { void refreshScopeStatus(); }, 400);
    return () => clearTimeout(t);
  }, [refreshScopeStatus, JSON.stringify(card.root), JSON.stringify(card.scope)]);

  const setRoot = (root: Block) => onChange({ ...card, root });
  const setName = (name: string) => onChange({ ...card, name });
  const setDescription = (description: string) => onChange({ ...card, description });
  const setScope = (scope: TaskScope) => onChange({ ...card, scope });

  // Outline layout state.  Local rather than on the card: which block is
  // being looked at and which containers are folded are properties of
  // this editing session, not of the card definition.
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [collapsed, setCollapsed] = React.useState<ReadonlySet<string>>(() => new Set());
  const toggleCollapse = React.useCallback((id: string) => {
    setCollapsed(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);
  const outlineRows = React.useMemo(() => flattenBlocks(card.root), [card.root]);
  // Resolve the selection against the CURRENT tree.  Falls back to the
  // first selectable block when nothing is selected or the selected block
  // was deleted: an empty pane beside a populated outline reads as broken.
  const selectedBlock = React.useMemo<Block | null>(() => {
    const hit = selectedId
      ? outlineRows.find(r => r.block.id === selectedId)?.block
      : undefined;
    return hit ?? firstSelectableRow(outlineRows)?.block ?? null;
  }, [outlineRows, selectedId]);
  const addTopLevel = (type: BlockType) => {
    const block = makeBlock(type);
    setRoot(appendChildBlock(card.root, card.root.id, block));
    setSelectedId(block.id);
  };

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
            {scopeStatus.preview
              ? '🔒 Needs signing before these permissions take effect'
              : '🔒 Unsigned privilege escalation — not active'}
          </div>
          <div className="tc-scope-approval-body">
            {scopeStatus.preview ? (
              <>
                This card requests shell/write permissions beyond the default
                safe set. It will still run — but these blocks are clamped to
                the default floor until the escalation is signed, so work that
                depends on the extra permissions fails partway through rather
                than up front. Signing requires a privileged gesture the agent
                cannot perform. Save the card to the deck to get the exact
                <code> ziya-approve </code> command for each block.
              </>
            ) : (
              <>
                This card requests shell/write permissions beyond the default
                safe set. Until approved, these blocks run at the default floor.
                Approval requires a privileged gesture the agent cannot perform.
              </>
            )}
          </div>
          {scopeStatus.blocks.filter(b => !b.authorized).map(b => (
            <div key={b.blockId} className="tc-scope-approval-block">
              <div className="tc-scope-approval-block-name">
                ⚠ {b.name || b.blockId}
              </div>
              {b.denialMessage && (
                <div className="tc-scope-approval-reason">
                  {b.denialMessage}
                </div>
              )}
              {Object.entries(b.escalation).map(([field, vals]) => (
                <div key={field} className="tc-scope-approval-detail">
                  {field}: {vals.join(', ')}
                </div>
              ))}
              {/* Empty for an unsaved spec: no persisted block id exists to
                  sign against yet, so rendering a bare <code> would show an
                  empty box that reads as a broken command. */}
              {b.signCommand && (
                <code className="tc-scope-approval-cmd">{b.signCommand}</code>
              )}
            </div>
          ))}
          {/* One command signing every unapproved block at once — the same
              vocabulary the chat proposal panel offers, so the two surfaces
              cannot disagree about how to sign the same card.  Minted
              server-side only when it saves work (>1 unsigned block); the
              per-block commands above remain for signing selectively. */}
          {scopeStatus.signAllCommand && (
            <div className="tc-scope-approval-block">
              <div className="tc-scope-approval-block-name">
                To sign all at once:
              </div>
              <code className="tc-scope-approval-cmd">{scopeStatus.signAllCommand}</code>
            </div>
          )}
          {/* Re-check is meaningless for an unsaved spec: no approval can
              exist for block ids that were never assigned, so the button
              could only ever report "still unsigned" — which reads as
              breakage rather than as the expected state. */}
          {!scopeStatus.preview && (
            <>
              <button
                className="tc-btn tc-btn-secondary tc-scope-approval-recheck"
                onClick={() => void refreshScopeStatus({ manual: true })}
                disabled={scopeCheckPending}
              >
                {scopeCheckPending ? '⏳ Checking…' : '↻ Re-check (after signing)'}
              </button>
              {scopeCheckError && (
                <div className="tc-scope-approval-check-error">
                  Re-check failed: {scopeCheckError}
                </div>
              )}
              {!scopeCheckPending && !scopeCheckError && scopeStatus?.anyUnapproved && (
                <div className="tc-scope-approval-check-hint">
                  Still unsigned as of the last check — verify you signed the block(s)
                  listed above in the same environment this server reads from.
                </div>
              )}
            </>
          )}
        </div>
      )}
      <input
        className="tc-card-description-input"
        value={card.description}
        onChange={e => setDescription(e.target.value)}
        placeholder="Optional description"
      />
      <div className="tc-card-scope-row">
        <BlockScopeButton
          scope={card.scope}
          onChange={setScope}
          title={card.name || 'this card'}
          label="Card Permissions"
        />
      </div>
      {/* Whole-card self-improvement rides the invisible group root —
          the root IS a container level, so no special casing in the
          executor.  Schedule roots are excluded: the scheduler
          dispatches each fire as an independent run rooted at the
          body, so a root-level improve flag would never execute. */}
      {card.root.block_type === 'group' && (
        <SelfImproveSection
          block={card.root}
          onChange={patch => setRoot({ ...card.root, ...patch })}
        />
      )}
      {layout === 'outline' ? (
        <div className="tc-card-canvas tc-card-canvas--outline">
          <div className="tc-outline-col">
            <BlockOutline
              root={card.root}
              mode="edit"
              selectedId={selectedBlock?.id ?? null}
              onSelect={setSelectedId}
              collapsed={collapsed}
              onToggleCollapse={toggleCollapse}
            />
            <label className="tc-outline__add">
              ⊕ add
              <select
                value=""
                aria-label="Add a block at the top level"
                onChange={e => {
                  if (e.target.value) addTopLevel(e.target.value as BlockType);
                }}
              >
                <option value="">block…</option>
                {ADDABLE_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>
          </div>
          <div className="tc-outline-pane">
            {selectedBlock ? (
              // The provider still wraps the whole root: a container
              // block's own children drag within the pane exactly as they
              // did in the tree layout, and the move applies to the root.
              <TaskCardDragProvider root={card.root} onRootChange={setRoot}>
                <BlockEditor
                  // Remount on selection change so an editor's internal
                  // state (open sections, drafts) never carries across
                  // blocks.
                  key={selectedBlock.id}
                  block={selectedBlock}
                  onChange={next =>
                    setRoot(updateBlockById(card.root, selectedBlock.id, () => next))}
                  onDelete={() => {
                    const next = removeBlockById(card.root, selectedBlock.id);
                    if (next) setRoot(next);
                  }}
                />
              </TaskCardDragProvider>
            ) : (
              <div className="tc-outline-pane__empty">
                No blocks yet — add one from the outline.
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="tc-card-canvas">
          <TaskCardDragProvider root={card.root} onRootChange={setRoot}>
            <BlockEditor block={card.root} onChange={setRoot} isRoot />
          </TaskCardDragProvider>
        </div>
      )}
    </div>
  );
};
