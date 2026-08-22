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

  const spec = useMemo(() => parseTaskCardSpec(messageContent), [messageContent]);

  const openPreview = useCallback(() => {
    if (!spec) return;
    setPreviewCard(makeDraftCard(spec));
    setShowPreview(true);
  }, [spec]);

  // Whichever reading reflects what Start would actually launch: the
  // edited card if the user has opened the preview, else the raw spec.
  const activeScope = editedScope ?? specScope;
  const unsignedCount = useMemo(
    () => (activeScope?.blocks ?? [])
      .filter(b => b.needsSignature ?? !b.authorized).length,
    [activeScope]);
  const needsSigning = unsignedCount > 0;

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
      if (savedCardId) {
        await taskCardApi.update(currentProject.id, savedCardId, {
          name: toSave.name, description: toSave.description,
          root: toSave.root, scope: toSave.scope, tags,
        });
        message.success('Card updated in deck');
      } else {
        const card = await taskCardApi.create(currentProject.id, { ...toSave, tags });
        setSavedCardId(card.id);
        message.success('Saved to deck — open Task Cards to sign or run it');
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
        detail: { bindingId: resp.binding.id, runId: resp.run.id },
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
  const handleStart = useCallback(() => {
    if (!needsSigning) { void handleLaunch(); return; }
    Modal.confirm({
      title: 'Run without signed permissions?',
      okText: 'Run anyway',
      cancelText: 'Cancel',
      width: 520,
      content: (
        <div style={{ fontSize: 13 }}>
          <p style={{ marginTop: 0 }}>
            {unsignedCount === 1
              ? '1 task in this card requests'
              : `${unsignedCount} tasks in this card request`}
            {' '}shell or write permissions beyond the default safe set, and
            that escalation is <strong>not signed</strong>.
          </p>
          <p style={{ marginBottom: 0 }}>
            The run will start, but those tasks are clamped to the default
            floor — so anything depending on the extra permissions fails
            partway through instead of up front. To sign first, use{' '}
            <strong>Save to deck</strong>, then open Task Cards and run the{' '}
            <code>ziya-approve</code> command shown for each block.
          </p>
        </div>
      ),
      onOk: () => { void handleLaunch(); },
    });
  }, [needsSigning, unsignedCount, handleLaunch]);

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
            {savedCardId ? 'Update in deck' : 'Save to deck'}
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
            tasks are clamped to the default floor until signed. Use{' '}
            <strong>Save to deck</strong> to get the{' '}
            <code>ziya-approve</code> command for each.
            {activeScope?.blocks
              .filter(b => b.needsSignature ?? !b.authorized)
              .map(b => (
                <div key={b.blockId} style={{ marginTop: 4, opacity: 0.85 }}>
                  ⚠ {b.name || b.blockId}
                  {Object.entries(b.escalation).map(([field, vals]) => (
                    <span key={field} style={{ marginLeft: 6, fontFamily: 'ui-monospace, monospace' }}>
                      {field}: {vals.join(', ')}
                    </span>
                  ))}
                </div>
              ))}
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
              // preview endpoint.  Without this the editor asked for
              // /task-cards/draft/scope-status and swallowed the 404, so
              // the live preview showed no signing warning at all.
              previewMode
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
