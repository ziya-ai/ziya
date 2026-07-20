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

import React, { useCallback, useMemo, useState } from 'react';
import { Button, message, Modal, Tag } from 'antd';
import { PlayCircleOutlined, LoadingOutlined, CheckCircleOutlined } from '@ant-design/icons';
import { useProject } from '../context/ProjectContext';
import { useActiveChat } from '../context/ActiveChatContext';
import { useChatContext } from '../context/ChatContext';
import { TaskCardEditor } from './TaskCard/TaskCardEditor';
import { useMessageId } from '../context/MessageIdContext';
import { taskCardApi } from '../services/taskCardApi';
import { createBinding } from '../services/taskBindingApi';
import type { TaskCard, TaskCardCreate } from '../types/task_card';
import { normalizeBlockTree, normalizeTaskScope } from '../utils/taskCardBlocks';

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

  const spec = useMemo(() => parseTaskCardSpec(messageContent), [messageContent]);

  const openPreview = useCallback(() => {
    if (!spec) return;
    setPreviewCard(makeDraftCard(spec));
    setShowPreview(true);
  }, [spec]);

  const handleLaunch = useCallback(async () => {
    if (!spec || !currentProject?.id || !currentConversationId) return;
    setLaunching(true);
    setShowPreview(false);
    try {
      // If the user opened the preview and edited it, launch those edits;
      // otherwise launch the originally parsed spec unchanged.
      const toCreate: TaskCardCreate = previewCard ? {
        name: previewCard.name,
        description: previewCard.description,
        root: previewCard.root,
        scope: previewCard.scope,
        tags: previewCard.tags,
        is_template: previewCard.is_template,
      } : spec;
      const card = await taskCardApi.create(currentProject.id, toCreate);
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
  }, [spec, previewCard, currentProject?.id, currentConversationId, effectiveMessageId, addRunningTaskConversation]);

  if (!spec) return null;

  return (
    <>
      <div style={{
        display: 'flex', gap: 8, alignItems: 'center',
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
        <Button size="small" onClick={openPreview} disabled={launching || launched}>
          Preview
        </Button>
        <Button
          type="primary" size="small"
          icon={launched ? <CheckCircleOutlined /> : launching ? <LoadingOutlined /> : <PlayCircleOutlined />}
          onClick={handleLaunch}
          disabled={launching || launched || !currentProject?.id || !currentConversationId}
        >
          {launched ? 'Launched' : launching ? 'Launching' : 'Start'}
        </Button>
      </div>

      <Modal
        title={`Task card: ${previewCard?.name || spec.name}`}
        open={showPreview}
        onCancel={() => setShowPreview(false)}
        footer={[
          <Button key="cancel" onClick={() => setShowPreview(false)}>Close</Button>,
          <Button key="launch" type="primary" icon={<PlayCircleOutlined />}
            onClick={handleLaunch} disabled={launching || launched}>
            Start
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
            />
          </div>
        ) : null}
      </Modal>
    </>
  );
};

export default TaskCardLaunchButton;
