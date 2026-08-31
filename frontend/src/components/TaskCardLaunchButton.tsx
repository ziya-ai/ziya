/**
 * TaskCardLaunchButton — inline "Start" button for AI-authored task cards.
 *
 * Detects fenced 
 * Parses the block as a TaskCardCreate, previews it, and on click:
 *   1. POST /task-cards         → creates the card
 *   2. POST /chats/{id}/task-bindings → launches + binds to this chat
 * After launch the existing TaskCardInlineTile picks up the binding
 * via useTaskBindings and renders live status.
 *
 * Expected fenced block format:
 *
 * 
 * {
 *   "name": "Fuzz the renderer",
 *   "description": "Generate, render, iterate",
 *   "root": {
 *     "block_type": "repeat",
 *     "name": "loop",
 *     "repeat_mode": "count",
 *     "repeat_count": 10,
 *     "repeat_parallel": true,
 *     "body": [
 *       {
 *         "block_type": "task",
 *         "name": "generate",
 *         "instructions": "Emit a random diagram spec"
 *       }
 *     ]
 *   }
 * }
 * ```
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, message, Modal, Tag, Tooltip } from 'antd';
import {
  PlayCircleOutlined, LoadingOutlined, CheckCircleOutlined,
  SaveOutlined, WarningOutlined,
} from '@ant-design/icons';
import { useProject } from '../context/ProjectContext';
import { useActiveChat } from '../context/ActiveChatContext';
import { useChatContext } from '../context/ChatContext';
import { TaskCardEditor } from './TaskCard/TaskCardEditor';
import { useMessageId } from '../context/MessageIdContext';
import { taskCardApi, type CardScopeStatus } from '../services/taskCardApi';
import { CommandBlock } from './CommandBlock';
import { CARD_SCOPE_REFRESH_EVENT } from './TaskCard/useCardSignatureStatus';
import { createBinding } from '../services/taskBindingApi';
import type { TaskCard, TaskCardCreate } from '../types/task_card';
import { normalizeBlockTree, normalizeTaskScope } from '../utils/taskCardBlocks';

// Marks a card as having come from an AI proposal in chat, so the deck can
// group it separately (see TaskCardsLibrary).  Kept as a plain tag rather
// than a `folder:` one so it does not consume the card's single folder
// slot — a proposal can still be filed into a folder afterwards.
export const PROPOSED_TAG = 'proposed';

// \x60 is the backtick char; written this way so no literal backtick
// appears in the source and fence-unaware tools don't mis-parse it.
const TASK_CARD_REGEX = /\x60\x60\x60task-card\s*\n([\s\S]*?)\x60\x60\x60/;

interface Props {
  messageContent: string;
  messageId?: string;
}

function parseTaskCardSpec(content: string): TaskCardCreate | null {
  const match = TASK_CARD_REGEX.exec(content);
  if (!match) return null;
  try {
    const parsed = JSON.parse(match[1]);
    if (typeof parsed.name !== 'string' || !parsed.root) return null;
    if (typeof parsed.root.block_type !== 'string') return null;
    return parsed as TaskCardCreate;
  } catch {
    return null;
  }
}

function summarizeRoot(root: TaskCardCreate['root']): string {
  if (root.block_type === 'task') {
    return `1 task`;
  }
  if (root.block_type === 'repeat') {
    const body = root.body ?? [];
    const mode = root.repeat_mode ?? 'count';
    const n = root.repeat_count ?? root.repeat_max ?? '?';
    const par = root.repeat_parallel ? ' · parallel' : '';
    return `repeat ${mode} ${n}${par} · ${body.length} inner block(s)`;
  }
  if (root.block_type === 'parallel') {
    return `parallel · ${(root.body ?? []).length} block(s)`;
  }
  if (root.block_type === 'group') {
    const n = (root.body ?? []).length;
    return `${n} stage${n === 1 ? '' : 's'} (sequence)`;
  }
  if (root.block_type === 'until') {
    const max = root.until_max ?? '?';
    return `until condition met · max ${max} attempt(s)`;
  }
  if (root.block_type === 'schedule') {
    return `scheduled · ${(root.body ?? []).length} inner block(s)`;
  }
  return root.block_type;
}

/**
 * Build a draft TaskCard for the preview editor out of the parsed
 * TaskCardCreate spec. TaskCardEditor expects a full TaskCard (id,
 * tags, timestamps, etc.) — this is never persisted; it only exists so
 * the user can review/tweak the AI-authored spec before Start creates
 * the real card via taskCardApi.create.
 */
function makeDraftCard(spec: TaskCardCreate): TaskCard {
  const now = Date.now() / 1000;
  return {
    id: 'draft',
    name: spec.name,
    description: spec.description ?? '',
    // The spec is raw model output parsed from a task-card fenced block —
    // it never went through the backend pydantic model that fills scope
    // defaults, so a block may carry a partial scope ({"paths": [...]}).
    // Normalize the whole tree (and the card-level scope) so every editor
    // sees well-formed scopes and cannot crash on scope.tools.length.
    root: normalizeBlockTree(spec.root),
    scope: spec.scope != null ? normalizeTaskScope(spec.scope) : null,
    tags: spec.tags ?? [],
    is_template: spec.is_template ?? false,
    source: 'ai',
    created_at: now,
    updated_at: now,
    last_run_at: null,
    run_count: 0,
  };
}

export const TaskCardLaunchButton: React.FC<Props> = ({ messageContent, messageId }) => {
  const { currentProject } = useProject();
  const { currentConversationId } = useActiveChat();
  const { addRunningTaskConversation } = useChatContext();
  // MarkdownRenderer doesn't pass messageId as a prop — pick it up from
  // context instead.  Prop still wins for callers that supply it
  // explicitly (e.g. future composer UI).
  const ctxMessageId = useMessageId();
  const effectiveMessageId = messageId ?? ctxMessageId ?? null;

  const [launching, setLaunching] = useState(false);
  const [launched, setLaunched] = useState(false);
  const [showPreview, setShowPreview] = useState(false);
  const [previewCard, setPreviewCard] = useState<TaskCard | null>(null);
  const [saving, setSaving] = useState(false);
  // Set once the proposal has been persisted via "Save to deck".  A later
  // Start must UPDATE this card rather than creating a second one:
  // otherwise the user signs the saved copy and runs an unsigned twin,
  // and the run is still clamped to the floor with no visible reason.
  const [savedCardId, setSavedCardId] = useState<string | null>(null);
  // Escalation as reported for the spec as parsed from the message.
  const [specScope, setSpecScope] = useState<CardScopeStatus | null>(null);
  // Escalation as reported for the card currently open in the preview
  // modal.  Tracked separately so edits made in there (adding or removing
  // a grant) move the outer badge without clobbering the pristine reading.
  const [editedScope, setEditedScope] = useState<CardScopeStatus | null>(null);
  // Escalation as reported for the card once PERSISTED.  Distinct from the
  // two readings above because only this one carries runnable signCommands:
  // approvals key on persisted block ids, and the preview endpoint returns
  // signCommand: "" by contract.  Fetching it also has a required side
  // effect — the by-id endpoint stages the decrypted scope that the
  // out-of-process signer needs to recompute the hash.
  const [savedScope, setSavedScope] = useState<CardScopeStatus | null>(null);
  // Manual re-check in flight.  Signing happens out of band (`sudo
  // ziya-approve` in a terminal), so the panel needs an explicit way to
  // learn that the record now exists.
  const [recheckPending, setRecheckPending] = useState(false);

  const spec = useMemo(() => parseTaskCardSpec(messageContent), [messageContent]);

  const openPreview = useCallback(() => {
    if (!spec) return;
    // Once saved, the draft carries the persisted id so the modal's editor
    // reads real by-id signature status (with its re-check button) instead
    // of the preview reading, which reports "unsigned" by contract.
    setPreviewCard({ ...makeDraftCard(spec), id: savedCardId ?? 'draft' });
    setShowPreview(true);
  }, [spec, savedCardId]);

  // Whichever reading reflects what Start would actually launch.  Once the
  // card is PERSISTED, the by-id reading is authoritative: it is the only
  // reading that can ever report "signed" — the preview endpoint returns
  // needsSignature: true by contract, so gating on it after save meant the
  // panel said "unsigned" forever, however many times the user signed.
  const activeScope = savedScope ?? editedScope ?? specScope;
  const unsignedCount = useMemo(
    () => (activeScope?.blocks ?? [])
      .filter(b => b.needsSignature ?? !b.authorized).length,
    [activeScope]);
  const needsSigning = unsignedCount > 0;

  // Where runnable commands come from.  Kept separate from activeScope so a
  // preview edit still moves the badge (what WOULD launch) without blanking
  // the commands (what CAN be signed right now).
  const commandScope = savedScope ?? (activeScope?.preview ? null : activeScope);
  const unsignedBlocks = useMemo(
    () => (activeScope?.blocks ?? []).filter(b => b.needsSignature ?? !b.authorized),
    [activeScope]);
  const commandBlocks = useMemo(
    () => (commandScope?.blocks ?? [])
      // signCommand first: the preview endpoint's "" must never render as
      // a blank command box.
      .filter(b => b.signCommand && (b.needsSignature ?? !b.authorized)),
    [commandScope]);
  // One label, two render sites.  Written twice, these diverged: the notice
  // kept saying "Save to deck" after the button had relabelled to "Update
  // in deck", naming a control that was no longer on screen.
  const saveLabel = savedCardId ? 'Update in deck' : 'Save to deck';

  // Ask the server whether this spec escalates.  Server-side because the
  // floor subtraction (a write inside `.ziya/` is NOT an escalation; the
  // shell floor is the base allowlist minus destructive/interpreter
  // commands) lives in app/config/scope_canonical.py.  A client-side copy
  // would either cry wolf — teaching users to ignore the notice — or miss
  // a real grant, which is the direction that actually matters.
  //
  // Read-only and stateless: previewing a proposal the user never accepts
  // persists nothing and stages nothing for the signer.
  useEffect(() => {
    if (!spec || !currentProject?.id) { setSpecScope(null); return; }
    let cancelled = false;
    taskCardApi.scopePreview(currentProject.id, spec)
      .then(st => { if (!cancelled) setSpecScope(st); })
      // Advisory: a failed check must not block Preview/Save/Start.  It
      // does mean no warning is shown, which is why the gate below treats
      // "unknown" as "no escalation" rather than inventing one.
      .catch(() => { if (!cancelled) setSpecScope(null); });
    return () => { cancelled = true; };
  }, [spec, currentProject?.id]);

  // Fresh by-id status for the persisted card.  Returns the reading so the
  // Start gate can decide on it directly rather than racing setState.
  const refreshSavedScope = useCallback(async (): Promise<CardScopeStatus | null> => {
    if (!savedCardId || !currentProject?.id) return null;
    try {
      const st = await taskCardApi.scopeStatus(currentProject.id, savedCardId);
      setSavedScope(st);
      return st;
    } catch {
      return null;  // advisory — keep the last reading rather than blanking
    }
  }, [savedCardId, currentProject?.id]);

  // Signing happens in a terminal, so no in-app action marks the moment it
  // lands.  Two triggers stand in for that: returning focus to this window
  // (the user just came back from the terminal), and the cross-surface
  // refresh event the deck editor broadcasts after its own re-check.
  useEffect(() => {
    if (!savedCardId) return;
    const onFocus = () => { void refreshSavedScope(); };
    const onRefresh = (e: Event) => {
      const detail = (e as CustomEvent).detail as { cardId?: string } | undefined;
      if (!detail?.cardId || detail.cardId === savedCardId) void refreshSavedScope();
    };
    window.addEventListener('focus', onFocus);
    window.addEventListener(CARD_SCOPE_REFRESH_EVENT, onRefresh);
    return () => {
      window.removeEventListener('focus', onFocus);
      window.removeEventListener(CARD_SCOPE_REFRESH_EVENT, onRefresh);
    };
  }, [savedCardId, refreshSavedScope]);

  // Explicit re-check button in the signing notice.  Broadcasts on success
  // so any open inline tile or deck badge for this card updates in step.
  const handleRecheck = useCallback(async () => {
    setRecheckPending(true);
    const st = await refreshSavedScope();
    setRecheckPending(false);
    if (st && savedCardId) {
      window.dispatchEvent(new CustomEvent(CARD_SCOPE_REFRESH_EVENT, {
        detail: { cardId: savedCardId },
      }));
    }
  }, [refreshSavedScope, savedCardId]);

  // The spec as the user would launch it — preview edits win.  Shared by
  // Save to deck and the launch path so the two cannot diverge.
  const currentSpec = useCallback((): TaskCardCreate | null => {
    if (previewCard) {
      return {
        name: previewCard.name,
        description: previewCard.description,
        root: previewCard.root,
        scope: previewCard.scope,
        tags: previewCard.tags,
        is_template: previewCard.is_template,
      };
    }
    return spec;
  }, [previewCard, spec]);

  // Persist without launching.  This is also the ONLY route to signing a
  // proposal: approvals key on persisted block ids, which do not exist
  // until TaskCardStorage.create assigns them.
  const handleSaveToDeck = useCallback(async () => {
    const toSave = currentSpec();
    if (!toSave || !currentProject?.id) return;
    setSaving(true);
    try {
      const tags = toSave.tags?.includes(PROPOSED_TAG)
        ? toSave.tags
        : [...(toSave.tags ?? []), PROPOSED_TAG];
      let cardId = savedCardId;
      if (savedCardId) {
        await taskCardApi.update(currentProject.id, savedCardId, {
          name: toSave.name, description: toSave.description,
          root: toSave.root, scope: toSave.scope, tags,
        });
        message.success('Card updated in deck');
      } else {
        const card = await taskCardApi.create(currentProject.id, { ...toSave, tags });
        cardId = card.id;
        setSavedCardId(card.id);
        // If the preview modal is open its draft now has a real id — switch
        // it over so the editor reads by-id signature status from here on.
        setPreviewCard(pc => (pc ? { ...pc, id: card.id } : pc));
        message.success('Saved to deck');
      }
      // Now that block ids exist, get the real per-block status: this both
      // yields the runnable sign commands shown below the panel and stages
      // the scope the signer reads.  Advisory — a failure here must not
      // present the save as failed, it only means no command is displayed.
      if (cardId) {
        try {
          setSavedScope(await taskCardApi.scopeStatus(currentProject.id, cardId));
        } catch {
          setSavedScope(null);
        }
      }
    } catch (e) {
      message.error(`Save failed: ${String(e)}`);
    } finally {
      setSaving(false);
    }
  }, [currentSpec, currentProject?.id, savedCardId]);

  const handleLaunch = useCallback(async () => {
    if (!spec || !currentProject?.id || !currentConversationId) return;
    setLaunching(true);
    setShowPreview(false);
    try {
      // If the user opened the preview and edited it, launch those edits;
      // otherwise launch the originally parsed spec unchanged.
      const toCreate = currentSpec();
      if (!toCreate) return;
      // Reuse the card if it was already saved to the deck.  Creating a
      // second one here would strand any signature the user just obtained
      // on the first: approvals key on block id, and a fresh create
      // assigns fresh ids, so the run would silently clamp to the floor.
      const card = savedCardId
        ? await taskCardApi.update(currentProject.id, savedCardId, {
            name: toCreate.name, description: toCreate.description,
            root: toCreate.root, scope: toCreate.scope, tags: toCreate.tags,
          })
        : await taskCardApi.create(currentProject.id, toCreate);
      // Remember it either way, so a retry after a binding failure does
      // not leave a second copy behind.
      setSavedCardId(card.id);
      const resp = await createBinding(currentProject.id, currentConversationId, {
        card_id: card.id,
        anchor_message_id: effectiveMessageId,
      });
      setLaunched(true);
      // Bug 1 fix: mark the conversation as having a running task
      // immediately so the conversation list shows the gear
      // affordance without waiting for the run to complete.
      // The reconciler in Conversation.tsx will clear this when
      // the run reaches a terminal state (or on next navigation).
      addRunningTaskConversation(currentConversationId);
      window.dispatchEvent(new CustomEvent('task-binding-created', {
        // See TaskCardInlineTile: `run` is Optional for staged bindings, this
        // path never stages, and no listener reads `detail`.
        detail: { bindingId: resp.binding.id, runId: resp.run?.id ?? null },
      }));
      message.success('Task launched');
    } catch (e) {
      message.error(`Launch failed: ${String(e)}`);
    } finally {
      setLaunching(false);
    }
  }, [spec, currentSpec, savedCardId, currentProject?.id, currentConversationId, effectiveMessageId, addRunningTaskConversation]);

  // Direct run of a card with unsigned escalation is not refused — the run
  // is still useful, and authorize_scope clamps the escalating blocks to
  // the floor rather than failing the launch.  But starting it silently
  // means the clamp surfaces LATER as an opaque mid-run permission
  // failure, several minutes into work the user thought was authorized.
  // Naming the cost up front is the whole point of this gate.
  const handleStart = useCallback(async () => {
    // The badge can be stale — signing happens out of band, so re-read the
    // persisted status at the moment of truth rather than scolding the
    // user from a reading taken before they signed.
    let gate = activeScope;
    if (savedCardId && currentProject?.id) {
      // Persist preview-modal edits BEFORE the status read: the check
      // grades the STORED card, so reading first grades the OLD scope —
      // an edit that added escalation after save+sign would pass the
      // gate here, then the launch path's own update would change the
      // scope hash and the run would clamp with no warning.  Launch
      // writes this same content anyway; this only moves the write
      // ahead of the check.
      const toPersist = currentSpec();
      if (toPersist) {
        try {
          await taskCardApi.update(currentProject.id, savedCardId, {
            name: toPersist.name, description: toPersist.description,
            root: toPersist.root, scope: toPersist.scope, tags: toPersist.tags,
          });
        } catch { /* advisory — the launch path's update reports real failures */ }
      }
      const fresh = await refreshSavedScope();
      if (fresh) gate = fresh;
    }
    const gateUnsigned = (gate?.blocks ?? [])
      .filter(b => b.needsSignature ?? !b.authorized).length;
    if (gateUnsigned === 0) { void handleLaunch(); return; }
    Modal.confirm({
      title: 'Run without signed permissions?',
      okText: 'Run anyway',
      cancelText: 'Cancel',
      width: 520,
      content: (
        <div style={{ fontSize: 13 }}>
          <p style={{ marginTop: 0 }}>
            {gateUnsigned === 1
              ? '1 task in this card requests'
              : `${gateUnsigned} tasks in this card request`}
            {' '}shell or write permissions beyond the default safe set, and
            that escalation is <strong>not signed</strong>.
          </p>
          <p style={{ marginBottom: 0 }}>
            The run will start, but those tasks are clamped to the default
            floor — so anything depending on the extra permissions fails
            partway through instead of up front. To sign first, cancel and
            use <strong>{saveLabel}</strong> — the exact{' '}
            <code>ziya-approve</code> command then appears in this panel.
          </p>
        </div>
      ),
      onOk: () => { void handleLaunch(); },
    });
  }, [activeScope, savedCardId, currentProject?.id, currentSpec,
      refreshSavedScope, handleLaunch, saveLabel]);

  if (!spec) return null;

  return (
    <>
      <div style={{
        display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap',
        padding: '10px 14px',
        background: 'rgba(31,111,235,0.06)',
        border: '1px solid rgba(31,111,235,0.3)',
        borderRadius: 8,
        margin: '8px 0',
      }}>
        <Tag color="blue" style={{ margin: 0 }}>task card</Tag>
        <div style={{ flex: 1, fontSize: 13 }}>
          <strong>{spec.name}</strong>
          {spec.description && (
            <div style={{ fontSize: 12, opacity: 0.75, marginTop: 2 }}>{spec.description}</div>
          )}
          <div style={{ fontSize: 11, opacity: 0.6, marginTop: 4, fontFamily: 'ui-monospace, monospace' }}>
            {summarizeRoot(spec.root)}
          </div>
        </div>
        {needsSigning && (
          <Tag icon={<WarningOutlined />} color="warning" style={{ margin: 0 }}>
            Needs signing · {unsignedCount}
          </Tag>
        )}
        <Button size="small" onClick={openPreview} disabled={launching || launched}>
          Preview
        </Button>
        <Tooltip title={savedCardId
          ? 'Update this card in the Task Cards deck'
          : 'Save to the Task Cards deck without running it — required before its permissions can be signed'}>
          <Button size="small" icon={<SaveOutlined />} loading={saving}
            onClick={handleSaveToDeck}
            disabled={saving || launching || !currentProject?.id}>
            {saveLabel}
          </Button>
        </Tooltip>
        <Button
          type="primary" size="small"
          icon={launched ? <CheckCircleOutlined /> : launching ? <LoadingOutlined /> : <PlayCircleOutlined />}
          onClick={handleStart}
          disabled={launching || launched || !currentProject?.id || !currentConversationId}
        >
          {launched ? 'Launched' : launching ? 'Launching' : 'Start'}
        </Button>
        {/* Full-width row: the escalation detail cannot sit in the button
            strip without squeezing the name/summary, and it must be
            readable WITHOUT opening Preview — a user who trusts the
            proposal and clicks Start directly is exactly the person who
            most needs to see it. */}
        {needsSigning && (
          <div style={{
            flexBasis: '100%', fontSize: 12, marginTop: 2,
            paddingTop: 6, borderTop: '1px solid rgba(250,140,22,0.25)',
            color: '#8a5a00',
          }}>
            🔒 <strong>Requires signing.</strong> {unsignedCount === 1
              ? 'One task requests'
              : `${unsignedCount} tasks request`} shell/write permissions
            beyond the default safe set. It will run either way, but those
            tasks are clamped to the default floor until signed.

            {/* One field per line with the name set apart. The previous
                inline-span run produced an unbroken monospace wall of
                paths that users skipped rather than read. */}
            {unsignedBlocks.map(b => (
              <div key={b.blockId} style={{ marginTop: 6, opacity: 0.9 }}>
                <div>⚠ {b.name || b.blockId}</div>
                {Object.entries(b.escalation).map(([field, vals]) => (
                  <div key={field} style={{ marginLeft: 14, marginTop: 2, lineHeight: 1.5 }}>
                    <span style={{ opacity: 0.7 }}>{field}: </span>
                    <span style={{ fontFamily: 'ui-monospace, monospace', wordBreak: 'break-word' }}>
                      {vals.join(', ')}
                    </span>
                  </div>
                ))}
              </div>
            ))}

            {/* The command, in place. Before this the panel pointed at a
                button label that had already changed and at a second
                surface it did not link to, so the instruction dead-ended. */}
            {commandScope?.signAllCommand ? (
              <>
                <div style={{ marginTop: 8 }}>
                  <strong>To sign all {unsignedCount}</strong>, run this in a terminal:
                </div>
                <CommandBlock cmd={commandScope.signAllCommand} />
                {/* The deck editor lists per-block commands; hiding them
                    here made the two surfaces disagree about how to sign
                    the same card. */}
                {commandBlocks.length > 0 && (
                  <details style={{ marginTop: 2 }}>
                    <summary style={{ cursor: 'pointer', opacity: 0.75 }}>
                      …or sign blocks individually
                    </summary>
                    {commandBlocks.map(b => (
                      <CommandBlock key={b.blockId} cmd={b.signCommand}
                        label={b.name || b.blockId} />
                    ))}
                  </details>
                )}
              </>
            ) : commandBlocks.length > 0 ? (
              <>
                <div style={{ marginTop: 8 }}>
                  <strong>To sign</strong>, run this in a terminal:
                </div>
                {commandBlocks.map(b => (
                  <CommandBlock
                    key={b.blockId}
                    cmd={b.signCommand}
                    label={commandBlocks.length > 1 ? (b.name || b.blockId) : undefined}
                  />
                ))}
              </>
            ) : (
              <div style={{ marginTop: 8 }}>
                Use <strong>{saveLabel}</strong> to save it to the deck — the
                exact <code>ziya-approve</code> command appears here once the
                card has ids to sign against.
              </div>
            )}

            {/* Signing is out of band, so the panel cannot see it happen.
                It re-checks on window focus; this is the explicit handle
                for when that is not enough. */}
            {savedCardId && (
              <div style={{ marginTop: 8, display: 'flex', alignItems: 'center',
                gap: 8, flexWrap: 'wrap' }}>
                <Button size="small" loading={recheckPending}
                  onClick={() => void handleRecheck()}>
                  ↻ Re-check (after signing)
                </Button>
                <span style={{ opacity: 0.65 }}>
                  Re-checks automatically when you return to this window.
                </span>
              </div>
            )}
          </div>
        )}
      </div>

      <Modal
        title={`Task card: ${previewCard?.name || spec.name}`}
        open={showPreview}
        onCancel={() => setShowPreview(false)}
        footer={[
          <Button key="cancel" onClick={() => setShowPreview(false)}>Close</Button>,
          <Button key="save" icon={<SaveOutlined />} loading={saving}
            onClick={handleSaveToDeck} disabled={saving || launching}>
            {savedCardId ? 'Update in deck' : 'Save to deck'}
          </Button>,
          <Button key="launch" type="primary" icon={<PlayCircleOutlined />}
            onClick={handleStart} disabled={launching || launched}>
            {needsSigning ? 'Start unsigned…' : 'Start'}
          </Button>,
        ]}
        width={800}
        destroyOnClose
      >
        {previewCard ? (
          <div style={{ maxHeight: 560, overflow: 'auto' }}>
            <TaskCardEditor
              card={previewCard}
              onChange={setPreviewCard}
              projectId={currentProject?.id}
              // Unsaved spec: route escalation through the stateless
              // preview endpoint (the by-id endpoint 404s on the synthetic
              // 'draft' id).  Once saved, the real id exists — switch to
              // by-id status so the editor can report "signed" and offer
              // its re-check button.
              previewMode={!savedCardId}
              // Keeps the outer block's badge in step with edits made in
              // here — removing a grant must clear the warning, and
              // adding one must raise it.
              onScopeStatusChange={(_id, st) => setEditedScope(st)}
            />
          </div>
        ) : null}
      </Modal>
    </>
  );
};

export default TaskCardLaunchButton;
