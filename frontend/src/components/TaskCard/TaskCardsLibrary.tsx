/**
 * TaskCardsLibrary — browse, edit, launch saved task cards.
 *
 * Option (a) from the rollout plan: a Modal-hosted library surface,
 * mirroring MemoryBrowser's shape.  Inline-in-chat rendering is a
 * separate component (option b) that reuses TaskCardEditor directly.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Modal, Input, Button, Tooltip, message, Empty, Popconfirm, Tag,
} from 'antd';
import {
  PlusOutlined, DeleteOutlined, CopyOutlined, SearchOutlined,
  PlayCircleOutlined, StopOutlined, ReloadOutlined,
} from '@ant-design/icons';
import { useProject } from '../../context/ProjectContext';
import { useChatContext } from '../../context/ChatContext';
import type { TaskCard, Block } from '../../types/task_card';
import { useTaskRunStream } from '../../hooks/useTaskRunStream';
import { taskCardApi } from '../../services/taskCardApi';
import {
  launchTaskCard, cancelTaskRun,
} from '../../services/taskRunApi';
import { createBinding } from '../../services/taskBindingApi';
import { TaskCardEditor } from './TaskCardEditor';

interface Props {
  visible: boolean;
  onClose: () => void;
  /** When set, Launch creates a binding to this chat instead of a
   *  plain unbound launch.  The anchor_message_id should be the id
   *  of the last message in the chat at launch time. */
  chatId?: string;
  anchorMessageId?: string | null;
}

function emptyRoot(): Block {
  return {
    block_type: 'task', id: '', name: 'New task',
    instructions: '', body: [],
  };
}

export const TaskCardsLibrary: React.FC<Props> = ({
  visible, onClose, chatId, anchorMessageId,
}) => {
  const { currentProject } = useProject();
  const { addRunningTaskConversation } = useChatContext();
  const projectId = currentProject?.id ?? '';

  const [cards, setCards] = useState<TaskCard[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<TaskCard | null>(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');

  // Active run tracking — id is seeded on launch; hook streams status.
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const { run: activeRun, refresh: refreshActiveRun } =
    useTaskRunStream(projectId, activeRunId ?? undefined);

  const reload = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const list = await taskCardApi.list(projectId);
      setCards(list);
    } catch (e) {
      message.error(`Failed to load task cards: ${String(e)}`);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { if (visible) reload(); }, [visible, reload]);

  const loadCard = useCallback(async (id: string) => {
    if (!projectId) return;
    try {
      const c = await taskCardApi.get(projectId, id);
      setSelectedId(id);
      setDraft(c);
      setActiveRunId(null);
    } catch (e) {
      message.error(`Failed to load card: ${String(e)}`);
    }
  }, [projectId]);

  const handleNew = useCallback(async () => {
    if (!projectId) return;
    try {
      const c = await taskCardApi.create(projectId, {
        name: 'Untitled', description: '', root: emptyRoot(), tags: [],
      });
      await reload();
      loadCard(c.id);
    } catch (e) {
      message.error(`Failed to create: ${String(e)}`);
    }
  }, [projectId, reload, loadCard]);

  const handleSave = useCallback(async () => {
    if (!projectId || !draft) return;
    try {
      await taskCardApi.update(projectId, draft.id, {
        name: draft.name, description: draft.description,
        root: draft.root, tags: draft.tags,
      });
      await reload();
      message.success('Saved');
    } catch (e) {
      message.error(`Save failed: ${String(e)}`);
    }
  }, [projectId, draft, reload]);

  const handleDuplicate = useCallback(async (id: string) => {
    if (!projectId) return;
    try {
      const copy = await taskCardApi.duplicate(projectId, id);
      await reload();
      loadCard(copy.id);
    } catch (e) {
      message.error(`Duplicate failed: ${String(e)}`);
    }
  }, [projectId, reload, loadCard]);

  const handleDelete = useCallback(async (id: string) => {
    if (!projectId) return;
    try {
      await taskCardApi.delete(projectId, id);
      if (selectedId === id) {
        setSelectedId(null); setDraft(null); setActiveRunId(null);
      }
      await reload();
    } catch (e) {
      message.error(`Delete failed: ${String(e)}`);
    }
  }, [projectId, selectedId, reload]);

  const handleLaunch = useCallback(async () => {
    if (!projectId || !draft) return;
    try {
      if (chatId) {
        // Chat-bound launch: the binding endpoint needs a persisted
        // card to reference.  Save silently so the run has something
        // durable to point at.  (Unbound launches below skip this —
        // they operate on the draft in-memory and don't need a save.)
        await taskCardApi.update(projectId, draft.id, {
          name: draft.name, description: draft.description,
          root: draft.root, tags: draft.tags,
        });
        const resp = await createBinding(projectId, chatId, {
          card_id: draft.id,
          anchor_message_id: anchorMessageId ?? null,
        });
        setActiveRunId(resp.run.id);
        // Notify the chat's useTaskBindings hook so the inline tile
        // renders immediately.  Uses the same event name that
        // TaskCardLaunchButton already dispatches for consistency.
        window.dispatchEvent(new CustomEvent('task-binding-created', {
          detail: { bindingId: resp.binding.id, runId: resp.run.id },
        }));
        // Bug 1 fix: mark the conversation as having a running task
        // immediately so the conversation list shows the gear
        // affordance without waiting for the run to complete.
        // The reconciler in Conversation.tsx will clear this when
        // the run reaches a terminal state (or on next navigation).
        addRunningTaskConversation(chatId);
        // Chat-bound: close the modal so the user sees the inline
        // tile appear in their conversation.  The tile polls status.
        onClose();
        message.success('Task launched in chat');
      } else {
        // Unbound launch: still need a saved card for the backend to
        // find.  TODO: support truly ephemeral one-off launches that
        // skip the card storage entirely.
        await taskCardApi.update(projectId, draft.id, {
          name: draft.name, description: draft.description,
          root: draft.root, tags: draft.tags,
        });
        const run = await launchTaskCard(projectId, draft.id);
        setActiveRunId(run.id);
      }
    } catch (e) {
      message.error(`Launch failed: ${String(e)}`);
    }
  }, [projectId, draft, chatId, anchorMessageId, onClose, addRunningTaskConversation]);

  const handleCancel = useCallback(async () => {
    if (!projectId || !activeRun) return;
    try {
      await cancelTaskRun(projectId, activeRun.id);
      // Hook picks up run_completed; prompt a refresh in case WS lags.
      refreshActiveRun();
    } catch (e) {
      message.error(`Cancel failed: ${String(e)}`);
    }
  }, [projectId, activeRun, refreshActiveRun]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return cards;
    return cards.filter(c =>
      c.name.toLowerCase().includes(q) ||
      c.description.toLowerCase().includes(q) ||
      c.tags.some(t => t.toLowerCase().includes(q)),
    );
  }, [cards, search]);

  const statusTag = activeRun ? (
    <Tag color={
      activeRun.status === 'running' ? 'blue' :
      activeRun.status === 'done' ? 'green' :
      activeRun.status === 'failed' ? 'red' :
      activeRun.status === 'cancelled' ? 'orange' : 'default'
    }>{activeRun.status}</Tag>
  ) : null;

  return (
    <Modal
      title="Task Cards"
      open={visible}
      onCancel={onClose}
      width={1000}
      footer={null}
      destroyOnClose
    >
      <div style={{ display: 'flex', gap: 12, height: '70vh' }}>
        {/* Left: list */}
        <div style={{ width: 260, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', gap: 6 }}>
            <Input
              size="small"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              prefix={<SearchOutlined />}
              placeholder="Search"
            />
            <Tooltip title="New card"><Button size="small" icon={<PlusOutlined />} onClick={handleNew} /></Tooltip>
            <Tooltip title="Refresh"><Button size="small" icon={<ReloadOutlined />} onClick={reload} loading={loading} /></Tooltip>
          </div>
          <div style={{ overflowY: 'auto', flex: 1, border: '1px solid rgba(128,128,128,0.2)', borderRadius: 4 }}>
            {filtered.length === 0 ? <Empty description="No cards" style={{ marginTop: 40 }} /> : filtered.map(c => (
              <div
                key={c.id}
                onClick={() => loadCard(c.id)}
                style={{
                  padding: '8px 10px',
                  borderBottom: '1px solid rgba(128,128,128,0.15)',
                  background: c.id === selectedId ? 'rgba(24,144,255,0.12)' : 'transparent',
                  cursor: 'pointer',
                }}
              >
                <div style={{ fontWeight: 500, fontSize: 13 }}>{c.name || 'Untitled'}</div>
                <div style={{ fontSize: 11, opacity: 0.6 }}>
                  {c.root.block_type}
                  {c.is_template ? ' · template' : ''}
                  {c.run_count > 0 ? ` · ${c.run_count} run${c.run_count === 1 ? '' : 's'}` : ''}
                </div>
              </div>
            ))}
          </div>
        </div>
        {/* Right: editor */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 8, overflow: 'hidden' }}>
          {draft ? (
            <>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                <Tooltip title={chatId ? 'Launches and binds to the current chat' : 'Launches unbound'}>
                  <Button type="primary" icon={<PlayCircleOutlined />} onClick={handleLaunch}
                    disabled={activeRun?.status === 'running'}>
                    {chatId ? 'Launch in chat' : 'Launch'}
                  </Button>
                </Tooltip>
                {activeRun?.status === 'running' && (
                  <Button danger icon={<StopOutlined />} onClick={handleCancel}>Cancel</Button>
                )}
                <Button onClick={handleSave}>Save</Button>
                <Tooltip title="Duplicate"><Button icon={<CopyOutlined />} onClick={() => handleDuplicate(draft.id)} /></Tooltip>
                <Popconfirm title={`Delete "${draft.name}"?`} onConfirm={() => handleDelete(draft.id)}>
                  <Button danger icon={<DeleteOutlined />} />
                </Popconfirm>
                <div style={{ flex: 1 }} />
                {statusTag}
                {activeRun?.error && <span style={{ color: '#ff4d4f', fontSize: 12 }}>{activeRun.error}</span>}
              </div>
              <div style={{ flex: 1, overflow: 'auto', border: '1px solid rgba(128,128,128,0.2)', borderRadius: 4, padding: 8 }}>
                <TaskCardEditor card={draft} onChange={setDraft} />
              </div>
            </>
          ) : (
            <Empty description="Select or create a card" style={{ marginTop: 80 }} />
          )}
        </div>
      </div>
    </Modal>
  );
};

export default TaskCardsLibrary;
