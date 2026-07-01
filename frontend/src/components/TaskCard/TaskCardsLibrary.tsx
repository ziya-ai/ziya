/**
 * TaskCardsLibrary — browse, edit, launch saved task cards.
 *
 * Option (a) from the rollout plan: a Modal-hosted library surface,
 * mirroring MemoryBrowser's shape.  Inline-in-chat rendering is a
 * separate component (option b) that reuses TaskCardEditor directly.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
import { taskCardApi, type CardScopeStatus } from '../../services/taskCardApi';
import { cancelTaskRun } from '../../services/taskRunApi';
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
  /** When set, the deck opens directly into this card's editor (inline
   *  tile "Edit card" backlink).  One-shot: consumed on open. */
  initialCardId?: string;
}

function emptyRoot(): Block {
  return {
    block_type: 'task', id: '', name: 'New task',
    instructions: '', body: [],
  };
}

export const TaskCardsLibrary: React.FC<Props> = ({
  visible, onClose, chatId, anchorMessageId, initialCardId,
}) => {
  const { currentProject } = useProject();
  const { addRunningTaskConversation, startNewChat } = useChatContext();
  const projectId = currentProject?.id ?? '';

  const [cards, setCards] = useState<TaskCard[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<TaskCard | null>(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');

  // cardId -> escalation/signature status, for the deck-list badge.
  const [scopeMap, setScopeMap] = useState<Record<string, CardScopeStatus>>({});

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
      // Fetch per-card escalation/signature status in parallel so the deck
      // list can badge which cards carry shell/write escalation and whether
      // it is signed. Failures are non-fatal: a card simply shows no badge.
      const entries = await Promise.all(list.map(async (c) => {
        try {
          return [c.id, await taskCardApi.scopeStatus(projectId, c.id)] as const;
        } catch {
          return [c.id, null] as const;
        }
      }));
      const next: Record<string, CardScopeStatus> = {};
      for (const [id, st] of entries) {
        if (st) next[id] = st;
      }
      setScopeMap(next);
    } catch (e) {
      message.error(`Failed to load task cards: ${String(e)}`);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { if (visible) reload(); }, [visible, reload]);

  // Keep the deck-list "Unsigned" badge in lock-step with the editor's own
  // re-check: when the open editor re-fetches a card's escalation status
  // (e.g. after you sign its scope), it calls this so scopeMap — the source
  // for the badge — updates without a full modal reload.  Stable identity
  // (empty deps, functional setState) so it never retriggers the editor's
  // status-refresh effect.
  const handleScopeStatusChange = useCallback(
    (cardId: string, status: CardScopeStatus | null) => {
      setScopeMap(prev => {
        if (!status) {
          const { [cardId]: _drop, ...rest } = prev;
          return rest;
        }
        return { ...prev, [cardId]: status };
      });
    }, []);

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

  // One-shot deep-link: when the deck opens with an initialCardId (the inline
  // tile "Edit card" backlink), jump straight into that card's editor.  A ref
  // guards against re-firing — without it, closing the editor back to the
  // list while the prop is still set would immediately reopen the card.
  const consumedInitialId = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (!visible) { consumedInitialId.current = undefined; return; }
    if (initialCardId && consumedInitialId.current !== initialCardId) {
      consumedInitialId.current = initialCardId;
      loadCard(initialCardId);
    }
  }, [visible, initialCardId, loadCard]);

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

  // Shared launch path: persist the draft (the binding endpoint needs a
  // durable card to reference), bind the card to targetChatId, surface the
  // inline tile, flag the conversation as running, and close the deck so the
  // user lands on the conversation watching the tile.  Both launch buttons
  // funnel through here — every launch now targets a conversation (the old
  // unbound launchTaskCard path was removed).
  const launchToChat = useCallback(async (targetChatId: string, anchor: string | null) => {
    if (!draft) return;
    await taskCardApi.update(projectId, draft.id, {
      name: draft.name, description: draft.description,
      root: draft.root, tags: draft.tags,
    });
    const resp = await createBinding(projectId, targetChatId, {
      card_id: draft.id,
      anchor_message_id: anchor,
    });
    setActiveRunId(resp.run.id);
    // Notify the chat's useTaskBindings hook so the inline tile renders
    // immediately — same event TaskCardLaunchButton dispatches.
    window.dispatchEvent(new CustomEvent('task-binding-created', {
      detail: { bindingId: resp.binding.id, runId: resp.run.id },
    }));
    // Gear affordance in the conversation list without waiting for the run.
    addRunningTaskConversation(targetChatId);
    onClose();
  }, [projectId, draft, addRunningTaskConversation, onClose]);

  const handleLaunchCurrent = useCallback(async () => {
    if (!projectId || !draft || !chatId) return;
    try {
      await launchToChat(chatId, anchorMessageId ?? null);
      message.success('Task launched in current conversation');
    } catch (e) {
      message.error(`Launch failed: ${String(e)}`);
    }
  }, [projectId, draft, chatId, anchorMessageId, launchToChat]);

  const handleLaunchNew = useCallback(async () => {
    if (!projectId || !draft) return;
    try {
      // Name the new conversation after the card; startNewChat returns the
      // id and sets it current, so the user navigates there automatically.
      const newId = await startNewChat(null, draft.name);
      if (!newId) { message.error('Could not create a new conversation'); return; }
      // New chat has no messages → no anchor; the tile anchors at the top.
      await launchToChat(newId, null);
      message.success('Task launched in new conversation');
    } catch (e) {
      message.error(`Launch failed: ${String(e)}`);
    }
  }, [projectId, draft, startNewChat, launchToChat]);

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
                <div style={{ fontWeight: 500, fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {c.name || 'Untitled'}
                  </span>
                  {(() => {
                    // Escalation/signature badge: only for cards whose blocks
                    // request shell/write escalation. Red when any block is
                    // unsigned, green when all escalation is signed.
                    const st = scopeMap[c.id];
                    const escBlocks = st?.blocks.filter(b => b.hasEscalation) ?? [];
                    if (escBlocks.length === 0) return null;
                    const unsigned = escBlocks.filter(b => !b.authorized).length;
                    return unsigned > 0 ? (
                      <Tag color="red" style={{ marginInlineEnd: 0, fontSize: 10, lineHeight: '16px', padding: '0 5px' }}>
                        Unsigned · {unsigned}
                      </Tag>
                    ) : (
                      <Tag color="green" style={{ marginInlineEnd: 0, fontSize: 10, lineHeight: '16px', padding: '0 5px' }}>
                        Signed
                      </Tag>
                    );
                  })()}
                </div>
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
                <Tooltip title={chatId
                  ? 'Launch and bind to the current conversation'
                  : 'No current conversation — use "Launch in new conversation"'}>
                  {/* span wrapper: Tooltip needs a non-disabled child to show on a disabled Button */}
                  <span>
                    <Button type="primary" icon={<PlayCircleOutlined />} onClick={handleLaunchCurrent}
                      disabled={!chatId || activeRun?.status === 'running'}>
                      Launch in current conversation
                    </Button>
                  </span>
                </Tooltip>
                <Tooltip title="Create a new conversation named after this card and launch the task there">
                  <Button icon={<PlayCircleOutlined />} onClick={handleLaunchNew}
                    disabled={activeRun?.status === 'running'}>
                    Launch in new conversation
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
                <TaskCardEditor
                  card={draft}
                  onChange={setDraft}
                  projectId={projectId}
                  onScopeStatusChange={handleScopeStatusChange}
                />
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
