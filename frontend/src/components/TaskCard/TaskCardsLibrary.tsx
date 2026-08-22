/**
 * TaskCardsLibrary — browse, edit, launch saved task cards.
 *
 * Option (a) from the rollout plan: a Modal-hosted library surface,
 * mirroring MemoryBrowser's shape.  Inline-in-chat rendering is a
 * separate component (option b) that reuses TaskCardEditor directly.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Modal, Input, Button, Tooltip, message, Empty, Popconfirm, Tag, Dropdown,
} from 'antd';
import {
  PlusOutlined, DeleteOutlined, CopyOutlined, SearchOutlined,
  PlayCircleOutlined, StopOutlined, ReloadOutlined,
  MoreOutlined, PushpinOutlined, FolderOutlined, FolderAddOutlined,
} from '@ant-design/icons';
import { useProject } from '../../context/ProjectContext';
import { useChatContext } from '../../context/ChatContext';
import type { TaskCard, Block, TaskScope } from '../../types/task_card';
import type { TaskRun } from '../../types/task_run';
import { useTaskRunStream } from '../../hooks/useTaskRunStream';
import { taskCardApi, type CardScopeStatus } from '../../services/taskCardApi';
import { cancelTaskRun, listTaskRuns } from '../../services/taskRunApi';
import { createBinding } from '../../services/taskBindingApi';
import { TaskCardEditor } from './TaskCardEditor';
import { BlockScopeButton } from './BlockScopeButton';
import { DeckRunList } from './DeckRunList';
import {
  hasLiveRuns, indexRunsByCard, summarizeCardRuns,
} from './deckRunIndex';
import { PROPOSED_TAG } from '../TaskCardLaunchButton';
import { CalleeHoldPanel } from './CalleeHoldPanel';

// Client-only pin set, mirroring the conversation sidebar's
// ZIYA_PINNED_FOLDERS pattern (see MUIChatHistory.tsx). Task Cards have no
// `pinned` field server-side by design: pins are a personal, per-browser
// affordance layered on top of the card list, not a shared card property.
const PINNED_TASK_CARDS_KEY = 'ZIYA_PINNED_TASK_CARDS';

function loadPinnedCardIds(): Set<string> {
  try {
    const raw = localStorage.getItem(PINNED_TASK_CARDS_KEY);
    if (raw) return new Set(JSON.parse(raw));
  } catch (error) {
    console.error('Error loading pinned task cards:', error);
  }
  return new Set();
}

// Deck sidebar width — personal, per-browser, like the pin set above.
const SIDEBAR_WIDTH_KEY = 'ZIYA_TASK_DECK_SIDEBAR_WIDTH';
const SIDEBAR_MIN_WIDTH = 180;
const SIDEBAR_MAX_WIDTH = 600;
const SIDEBAR_DEFAULT_WIDTH = 260;

const clampSidebarWidth = (w: number): number =>
  Math.max(SIDEBAR_MIN_WIDTH, Math.min(SIDEBAR_MAX_WIDTH, w));

function loadSidebarWidth(): number {
  try {
    const raw = localStorage.getItem(SIDEBAR_WIDTH_KEY);
    const parsed = raw ? parseInt(raw, 10) : NaN;
    if (Number.isFinite(parsed)) return clampSidebarWidth(parsed);
  } catch (error) {
    console.error('Error loading task deck sidebar width:', error);
  }
  return SIDEBAR_DEFAULT_WIDTH;
}

// Dialog (modal) size — same personal, per-browser treatment as the
// sidebar width above.  Stored raw and clamped on read, so moving between
// a laptop and a large display doesn't strand the dialog at a size the
// smaller screen cannot show.
const DIALOG_SIZE_KEY = 'ZIYA_TASK_DECK_DIALOG_SIZE';
const DIALOG_MIN_WIDTH = 640;
const DIALOG_MIN_HEIGHT = 320;
const DIALOG_DEFAULT_WIDTH = 1000;

// Chrome the Modal adds around the body (title bar, paddings) plus its
// default 100px top offset.  The stored height is the body's inner
// height, so the viewport cap has to leave room for this.
const DIALOG_CHROME_HEIGHT = 220;

const clampDialogWidth = (w: number): number =>
  Math.max(DIALOG_MIN_WIDTH, Math.min(window.innerWidth - 32, w));
const clampDialogHeight = (h: number): number =>
  Math.max(DIALOG_MIN_HEIGHT, Math.min(window.innerHeight - DIALOG_CHROME_HEIGHT, h));

function loadDialogSize(): { width: number; height: number } {
  // Default height matches the previous hard-coded 70vh body.
  const fallbackWidth = DIALOG_DEFAULT_WIDTH;
  const fallbackHeight = Math.round(window.innerHeight * 0.7);
  try {
    const raw = localStorage.getItem(DIALOG_SIZE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as { width?: number; height?: number };
      return {
        width: clampDialogWidth(Number.isFinite(parsed.width) ? parsed.width! : fallbackWidth),
        height: clampDialogHeight(Number.isFinite(parsed.height) ? parsed.height! : fallbackHeight),
      };
    }
  } catch (error) {
    console.error('Error loading task deck dialog size:', error);
  }
  return { width: clampDialogWidth(fallbackWidth), height: clampDialogHeight(fallbackHeight) };
}

// Tags double as "folders" for Task Cards (no dedicated folder model
// server-side). A tag prefixed with this marker is treated as a folder
// membership rather than a plain search-matchable tag, so it can be
// rendered/grouped distinctly without a schema change.
const FOLDER_TAG_PREFIX = 'folder:';
const folderNameFromTag = (tag: string): string => tag.slice(FOLDER_TAG_PREFIX.length);
const folderTagFromName = (name: string): string => `${FOLDER_TAG_PREFIX}${name}`;

// Cards saved straight from an AI proposal in chat (see
// TaskCardLaunchButton). Grouped separately so a burst of accepted
// proposals doesn't bury hand-authored cards, and because they're the
// ones most likely to still need their permissions signed. Deliberately a
// plain tag, not a `folder:` one, so it does not consume the card's single
// folder slot — a proposal can still be filed into a folder.
const isProposed = (c: TaskCard): boolean => c.tags.includes(PROPOSED_TAG);

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
  const { currentProject, updateProject } = useProject();
  const { addRunningTaskConversation, startNewChat, loadConversation } =
    useChatContext();
  const projectId = currentProject?.id ?? '';

  // Deck-level (project-wide) Task Card permissions baseline — merged
  // additively with each card's own scope (see ProjectSettings.taskScope).
  const deckScope: TaskScope = currentProject?.settings?.taskScope ?? { paths: [], tools: [], skills: [] };
  const handleDeckScopeChange = useCallback((next: TaskScope) => {
    if (!currentProject) return;
    updateProject(currentProject.id, {
      settings: { ...currentProject.settings, taskScope: next },
    });
  }, [currentProject, updateProject]);

  const [cards, setCards] = useState<TaskCard[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<TaskCard | null>(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');

  // cardId -> escalation/signature status, for the deck-list badge.
  const [scopeMap, setScopeMap] = useState<Record<string, CardScopeStatus>>({});

  // cardId -> that card's runs, newest first.  One project-wide request
  // rather than a fetch per card: the deck opens with every card visible,
  // so per-card fetching would be an N-request burst on every open — the
  // reason the deck previously settled for the card's `run_count` integer
  // and could say nothing about whether anything was still running.
  const [runIndex, setRunIndex] = useState<Map<string, TaskRun[]>>(new Map());

  // Pinned card ids — client-only (see loadPinnedCardIds above).
  const [pinnedIds, setPinnedIds] = useState<Set<string>>(() => loadPinnedCardIds());
  useEffect(() => {
    try {
      localStorage.setItem(PINNED_TASK_CARDS_KEY, JSON.stringify([...pinnedIds]));
    } catch (error) {
      console.error('Error saving pinned task cards:', error);
    }
  }, [pinnedIds]);
  const togglePinCard = useCallback((id: string) => {
    setPinnedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  // Resizable list pane.  Width is committed to localStorage only on
  // pointer-up so a drag doesn't produce a write per mousemove frame.
  const [sidebarWidth, setSidebarWidth] = useState<number>(() => loadSidebarWidth());
  const [resizing, setResizing] = useState(false);
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragRef.current = { startX: e.clientX, startWidth: sidebarWidth };
    setResizing(true);
  }, [sidebarWidth]);

  useEffect(() => {
    if (!resizing) return;
    const onMove = (e: MouseEvent) => {
      if (!dragRef.current) return;
      const { startX, startWidth } = dragRef.current;
      setSidebarWidth(clampSidebarWidth(startWidth + (e.clientX - startX)));
    };
    const onUp = () => {
      dragRef.current = null;
      setResizing(false);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    const prevUserSelect = document.body.style.userSelect;
    const prevCursor = document.body.style.cursor;
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';
    return () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.style.userSelect = prevUserSelect;
      document.body.style.cursor = prevCursor;
    };
  }, [resizing]);

  useEffect(() => {
    if (resizing) return;
    try {
      localStorage.setItem(SIDEBAR_WIDTH_KEY, String(sidebarWidth));
    } catch (error) {
      console.error('Error saving task deck sidebar width:', error);
    }
  }, [sidebarWidth, resizing]);

  // Resizable dialog.  The grip sits at the bottom-right of the body, so a
  // horizontal drag moves the width by 2x the pointer delta: the Modal is
  // centre-aligned, so its right edge only advances by half of any width
  // increase.  Vertically the Modal is top-anchored, so height tracks the
  // pointer 1:1.
  const [dialogSize, setDialogSize] = useState(() => loadDialogSize());
  const [dialogResizing, setDialogResizing] = useState(false);
  const dialogDragRef = useRef<
    { startX: number; startY: number; startWidth: number; startHeight: number } | null
  >(null);

  const handleDialogResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dialogDragRef.current = {
      startX: e.clientX, startY: e.clientY,
      startWidth: dialogSize.width, startHeight: dialogSize.height,
    };
    setDialogResizing(true);
  }, [dialogSize]);

  const resetDialogSize = useCallback(() => {
    setDialogSize({
      width: clampDialogWidth(DIALOG_DEFAULT_WIDTH),
      height: clampDialogHeight(Math.round(window.innerHeight * 0.7)),
    });
  }, []);

  useEffect(() => {
    if (!dialogResizing) return;
    const onMove = (e: MouseEvent) => {
      const d = dialogDragRef.current;
      if (!d) return;
      setDialogSize({
        width: clampDialogWidth(d.startWidth + (e.clientX - d.startX) * 2),
        height: clampDialogHeight(d.startHeight + (e.clientY - d.startY)),
      });
    };
    const onUp = () => {
      dialogDragRef.current = null;
      setDialogResizing(false);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    const prevUserSelect = document.body.style.userSelect;
    const prevCursor = document.body.style.cursor;
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'nwse-resize';
    return () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.style.userSelect = prevUserSelect;
      document.body.style.cursor = prevCursor;
    };
  }, [dialogResizing]);

  useEffect(() => {
    if (dialogResizing) return;
    try {
      localStorage.setItem(DIALOG_SIZE_KEY, JSON.stringify(dialogSize));
    } catch (error) {
      console.error('Error saving task deck dialog size:', error);
    }
  }, [dialogSize, dialogResizing]);

  // The dialog can now be narrowed below the stored sidebar width, so the
  // sidebar has to yield rather than squeeze the editor pane to nothing.
  // Reserve 320px for the editor before honouring the sidebar's width.
  const effectiveSidebarWidth = Math.min(
    sidebarWidth,
    Math.max(SIDEBAR_MIN_WIDTH, dialogSize.width - 320),
  );

  // Active run tracking — id is seeded on launch; hook streams status.
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const { run: activeRun, refresh: refreshActiveRun } =
    useTaskRunStream(projectId, activeRunId ?? undefined);

  const reload = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    // Runs are supplementary to the card list, so this is its own
    // try/catch: a deck that refuses to render its cards because the run
    // endpoint hiccuped is strictly worse than one showing no run badges.
    try {
      setRunIndex(indexRunsByCard(await listTaskRuns(projectId)));
    } catch (e) {
      console.debug('TaskCardsLibrary: run list fetch failed', e);
      setRunIndex(new Map());
    }
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

  // Keep the run rows current while the deck is open and something is
  // actually live.  Gated on `hasLiveRuns` in both directions: an idle
  // deck issues no requests at all, and a deck watching a running card
  // does not need a manual Refresh to notice it finished — the previous
  // behaviour, where a card's state was only as fresh as the last open.
  //
  // Polling rather than the WS stream used for a single active run: this
  // watches EVERY card's runs, and opening a socket per run would be a
  // connection per row for a surface the user glances at.
  const runsLive = hasLiveRuns(runIndex);
  useEffect(() => {
    if (!visible || !projectId || !runsLive) return;
    let cancelled = false;
    const timer = setInterval(async () => {
      try {
        const runs = await listTaskRuns(projectId);
        if (!cancelled) setRunIndex(indexRunsByCard(runs));
      } catch {
        // Transient: keep the last good index rather than blanking the
        // rows on one failed poll.
      }
    }, 4000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [visible, projectId, runsLive]);

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

  // "Folders" are just tags prefixed with FOLDER_TAG_PREFIX (see note near
  // the constant above) — no dedicated backend model, so these mutate a
  // card's existing `tags` array through the normal update endpoint.
  const setCardFolder = useCallback(async (id: string, folderName: string | null) => {
    if (!projectId) return;
    const card = cards.find(c => c.id === id) ?? (draft?.id === id ? draft : null);
    if (!card) return;
    const withoutFolders = card.tags.filter(t => !t.startsWith(FOLDER_TAG_PREFIX));
    const nextTags = folderName ? [...withoutFolders, folderTagFromName(folderName)] : withoutFolders;
    try {
      const updated = await taskCardApi.update(projectId, id, { tags: nextTags });
      if (draft?.id === id) setDraft(updated);
      await reload();
    } catch (e) {
      message.error(`Move to folder failed: ${String(e)}`);
    }
  }, [projectId, cards, draft, reload]);

  const [newFolderModalCardId, setNewFolderModalCardId] = useState<string | null>(null);
  const [newFolderName, setNewFolderName] = useState('');
  const handleCreateFolderAndAssign = useCallback(async () => {
    const name = newFolderName.trim();
    if (!name || !newFolderModalCardId) return;
    await setCardFolder(newFolderModalCardId, name);
    setNewFolderModalCardId(null);
    setNewFolderName('');
  }, [newFolderName, newFolderModalCardId, setCardFolder]);

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

  // Unsigned escalation on the card about to be launched.  Read from
  // scopeMap, which the open editor keeps current via
  // onScopeStatusChange, so this never needs its own fetch.
  const draftUnsignedCount = useMemo(() => {
    if (!draft) return 0;
    const st = scopeMap[draft.id];
    if (!st) return 0;
    return st.blocks.filter(b => b.needsSignature ?? !b.authorized).length;
  }, [draft, scopeMap]);

  // Same gate as the chat proposal block: launching with unsigned
  // escalation is allowed (authorize_scope clamps to the floor) but must
  // not be silent, or the clamp resurfaces mid-run as an opaque
  // permission failure well after the user has stopped watching.
  const confirmIfUnsigned = useCallback((onProceed: () => void) => {
    if (draftUnsignedCount === 0) { onProceed(); return; }
    Modal.confirm({
      title: 'Run without signed permissions?',
      okText: 'Run anyway',
      cancelText: 'Cancel',
      width: 520,
      content: (
        <div style={{ fontSize: 13 }}>
          <p style={{ marginTop: 0 }}>
            {draftUnsignedCount === 1
              ? '1 task in this card requests'
              : `${draftUnsignedCount} tasks in this card request`}
            {' '}shell or write permissions beyond the default safe set, and
            that escalation is <strong>not signed</strong>.
          </p>
          <p style={{ marginBottom: 0 }}>
            The run will start, but those tasks are clamped to the default
            floor — so work depending on the extra permissions fails partway
            through rather than up front. The banner in the editor lists the{' '}
            <code>ziya-approve</code> command for each block.
          </p>
        </div>
      ),
      onOk: onProceed,
    });
  }, [draftUnsignedCount]);

  const handleLaunchCurrent = useCallback(async () => {
    if (!projectId || !draft || !chatId) return;
    confirmIfUnsigned(async () => {
      try {
        await launchToChat(chatId, anchorMessageId ?? null);
        message.success('Task launched in current conversation');
      } catch (e) {
        message.error(`Launch failed: ${String(e)}`);
      }
    });
  }, [projectId, draft, chatId, anchorMessageId, launchToChat, confirmIfUnsigned]);

  const handleLaunchNew = useCallback(async () => {
    if (!projectId || !draft) return;
    confirmIfUnsigned(async () => {
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
    });
  }, [projectId, draft, startNewChat, launchToChat, confirmIfUnsigned]);

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

  /**
   * Navigate to the conversation a run is anchored in.
   *
   * The deck closes on the way, deliberately: it is a modal covering the
   * conversation, so navigating behind it is indistinguishable from the
   * click having done nothing.  No run id is passed along — the tile in
   * that conversation already renders the run, and its own attempt rail
   * resolves which attempt of a lineage to show.
   */
  const handleOpenRun = useCallback((run: TaskRun) => {
    const convId = run.source_conversation_id;
    if (!convId) return;
    loadConversation(convId);
    onClose();
  }, [loadConversation, onClose]);

  /** The selected card's runs, newest first.  Empty until fetched. */
  const draftRuns = useMemo(
    () => (draft ? runIndex.get(draft.id) ?? [] : []), [draft, runIndex]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return cards;
    return cards.filter(c =>
      c.name.toLowerCase().includes(q) ||
      c.description.toLowerCase().includes(q) ||
      c.tags.some(t => t.toLowerCase().includes(q)),
    );
  }, [cards, search]);

  // Card-derived folder name for a card (first folder: tag, since a card
  // lives in at most one folder in this lightweight scheme).
  const folderOfCard = useCallback((c: TaskCard): string | null => {
    const tag = c.tags.find(t => t.startsWith(FOLDER_TAG_PREFIX));
    return tag ? folderNameFromTag(tag) : null;
  }, []);

  // Every folder name in use, so "Move to folder" can offer existing
  // folders even if their only member is currently filtered out.
  const allFolderNames = useMemo(() => {
    const names = new Set<string>();
    for (const c of cards) {
      const n = folderOfCard(c);
      if (n) names.add(n);
    }
    return [...names].sort((a, b) => a.localeCompare(b));
  }, [cards, folderOfCard]);

  // Grouping for render: pinned cards float to the top regardless of
  // folder, then folders (alphabetical), then chat-proposed cards, then
  // unfiled cards.  Proposals are split out because they arrive in bursts
  // from conversations and would otherwise bury hand-authored cards.
  const grouped = useMemo(() => {
    const pinned = filtered.filter(c => pinnedIds.has(c.id));
    const unpinned = filtered.filter(c => !pinnedIds.has(c.id));
    const byFolder = new Map<string, TaskCard[]>();
    const proposed: TaskCard[] = [];
    const unfiled: TaskCard[] = [];
    for (const c of unpinned) {
      const f = folderOfCard(c);
      if (f) {
        if (!byFolder.has(f)) byFolder.set(f, []);
        byFolder.get(f)!.push(c);
      } else if (isProposed(c)) {
        // Only when unfiled: an explicitly foldered proposal has been
        // deliberately organized, so respect that over this grouping.
        proposed.push(c);
      } else {
        unfiled.push(c);
      }
    }
    const folders = [...byFolder.entries()].sort((a, b) => a[0].localeCompare(b[0]));
    return { pinned, folders, proposed, unfiled };
  }, [filtered, pinnedIds, folderOfCard]);

  // Per-row "⋯" context menu — mirrors the conversation sidebar's
  // AntActionMenu affordances (Duplicate, Delete, Pin, Move to folder).
  const cardMenuItems = useCallback((c: TaskCard) => {
    const currentFolder = folderOfCard(c);
    const folderSubmenu = [
      ...allFolderNames.map(name => ({
        key: `folder:${name}`,
        label: name,
        icon: <FolderOutlined />,
        disabled: name === currentFolder,
      })),
      ...(allFolderNames.length > 0 ? [{ type: 'divider' as const }] : []),
      { key: 'folder:__new__', label: 'New folder…', icon: <FolderAddOutlined /> },
      ...(currentFolder ? [{ key: 'folder:__none__', label: 'Remove from folder' }] : []),
    ];
    return {
      items: [
        { key: 'duplicate', label: 'Duplicate', icon: <CopyOutlined /> },
        {
          key: 'pin',
          label: pinnedIds.has(c.id) ? 'Unpin' : 'Pin',
          icon: <PushpinOutlined />,
        },
        { key: 'move', label: 'Add to folder', icon: <FolderOutlined />, children: folderSubmenu },
        { type: 'divider' as const },
        { key: 'delete', label: 'Delete', icon: <DeleteOutlined />, danger: true },
      ],
      onClick: (info: { key: string; domEvent: { stopPropagation: () => void } }) => {
        const { key } = info;
        info.domEvent.stopPropagation();
        if (key === 'duplicate') { handleDuplicate(c.id); return; }
        if (key === 'pin') { togglePinCard(c.id); return; }
        if (key === 'delete') {
          Modal.confirm({
            title: `Delete "${c.name || 'Untitled'}"?`,
            okType: 'danger',
            onOk: () => handleDelete(c.id),
          });
          return;
        }
        if (key === 'folder:__new__') { setNewFolderModalCardId(c.id); return; }
        if (key === 'folder:__none__') { setCardFolder(c.id, null); return; }
        if (key.startsWith('folder:')) { setCardFolder(c.id, key.slice('folder:'.length)); return; }
      },
    };
  }, [allFolderNames, folderOfCard, pinnedIds, handleDuplicate, handleDelete, togglePinCard, setCardFolder]);

  const renderCardRow = useCallback((c: TaskCard) => (
    <div
      key={c.id}
      onClick={() => loadCard(c.id)}
      style={{
        padding: '8px 10px',
        borderBottom: '1px solid rgba(128,128,128,0.15)',
        background: c.id === selectedId ? 'rgba(24,144,255,0.12)' : 'transparent',
        cursor: 'pointer',
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'space-between',
        gap: 4,
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 500, fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
          {pinnedIds.has(c.id) && <PushpinOutlined style={{ fontSize: 11, opacity: 0.7 }} />}
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
          {(() => {
            // Live / needs-attention badges.  Both can be present at
            // once — a retry running while the failed attempt it came
            // from is still on record — so they are separate badges
            // rather than one derived "state", which would hide
            // whichever of the two the user was not looking for.
            const s = summarizeCardRuns(runIndex.get(c.id));
            if (s.live === 0 && s.attention === 0) return null;
            return (
              <>
                {s.live > 0 && (
                  <Tooltip title={`${s.live} run(s) still in flight — click the card to open them`}>
                    <Tag color="blue" style={{ marginInlineEnd: 0, fontSize: 10, lineHeight: '16px', padding: '0 5px' }}>
                      {s.live === 1 ? 'Running' : `Running · ${s.live}`}
                    </Tag>
                  </Tooltip>
                )}
                {s.attention > 0 && (
                  <Tooltip title={
                    'Runs stopped without finishing: '
                    + Object.entries(s.byStatus)
                        .filter(([k]) => k === 'failed' || k === 'partial' || k === 'held')
                        .map(([k, n]) => `${n} ${k}`).join(', ')
                  }>
                    <Tag color="gold" style={{ marginInlineEnd: 0, fontSize: 10, lineHeight: '16px', padding: '0 5px' }}>
                      {s.attention === 1 ? 'Attention' : `Attention · ${s.attention}`}
                    </Tag>
                  </Tooltip>
                )}
              </>
            );
          })()}
        </div>
        <div style={{ fontSize: 11, opacity: 0.6 }}>
          {c.root.block_type}
          {c.is_template ? ' · template' : ''}
          {/* Distinguish "saved but never launched" from "has history".
              A run_count of 0 previously rendered as nothing at all, so a
              proposal saved-but-not-yet-run looked identical to a card
              that had been exercised. */}
          {c.run_count > 0
            ? ` · ${c.run_count} run${c.run_count === 1 ? '' : 's'}`
            : ' · never run'}
          {isProposed(c) ? ' · from chat' : ''}
        </div>
      </div>
      <Dropdown menu={cardMenuItems(c)} trigger={['click']} placement="bottomRight">
        <MoreOutlined
          style={{ fontSize: 14, opacity: 0.6, padding: '2px 4px' }}
          onClick={(e) => e.stopPropagation()}
        />
      </Dropdown>
    </div>
  ), [selectedId, pinnedIds, scopeMap, runIndex, cardMenuItems, loadCard]);

  const statusTag = activeRun ? (
    <Tag color={
      activeRun.status === 'running' ? 'blue' :
      activeRun.status === 'done' ? 'green' :
      activeRun.status === 'failed' ? 'red' :
      // Violet, matching the tile chip: a held run is stopped, not
      // broken.  Without this case it fell through to 'default' grey and
      // read as an unremarkable terminal state — indistinguishable at a
      // glance from queued.
      activeRun.status === 'held' ? 'purple' :
      activeRun.status === 'cancelled' ? 'orange' : 'default'
    }>{activeRun.status}</Tag>
  ) : null;

  return (
    <Modal
      title="Task Cards"
      open={visible}
      onCancel={onClose}
      width={dialogSize.width}
      footer={null}
      destroyOnClose
    >
      <div style={{ display: 'flex', gap: 12, height: dialogSize.height }}>
        {/* Left: list */}
        <div style={{ width: effectiveSidebarWidth, flexShrink: 0, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {currentProject && (
            <BlockScopeButton
              scope={deckScope}
              onChange={handleDeckScopeChange}
              title={`${currentProject.name} (deck)`}
              label="Deck Permissions"
            />
          )}
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
            {filtered.length === 0 ? <Empty description="No cards" style={{ marginTop: 40 }} /> : (
              <>
                {grouped.pinned.length > 0 && (
                  <div>
                    <div style={{ padding: '4px 10px', fontSize: 11, opacity: 0.55, fontWeight: 600 }}>PINNED</div>
                    {grouped.pinned.map(renderCardRow)}
                  </div>
                )}
                {grouped.folders.map(([folderName, cardsInFolder]) => (
                  <div key={folderName}>
                    <div style={{ padding: '4px 10px', fontSize: 11, opacity: 0.55, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 4 }}>
                      <FolderOutlined /> {folderName.toUpperCase()}
                    </div>
                    {cardsInFolder.map(renderCardRow)}
                  </div>
                ))}
                {grouped.proposed.length > 0 && (
                  <div>
                    <div style={{ padding: '4px 10px', fontSize: 11, opacity: 0.55, fontWeight: 600 }}>
                      PROPOSED IN CHAT
                    </div>
                    {grouped.proposed.map(renderCardRow)}
                  </div>
                )}
                {grouped.unfiled.length > 0 && (
                  grouped.pinned.length > 0 || grouped.folders.length > 0 ||
                  grouped.proposed.length > 0
                ) && (
                  <div style={{ padding: '4px 10px', fontSize: 11, opacity: 0.55, fontWeight: 600 }}>OTHER</div>
                )}
                {grouped.unfiled.map(renderCardRow)}
              </>
            )}
          </div>
        </div>
        {/* Divider: drag to resize the list pane */}
        <div
          onMouseDown={handleResizeStart}
          onDoubleClick={() => setSidebarWidth(SIDEBAR_DEFAULT_WIDTH)}
          title="Drag to resize · double-click to reset"
          style={{
            flex: '0 0 6px',
            marginInline: -3,
            cursor: 'col-resize',
            borderRadius: 3,
            alignSelf: 'stretch',
            background: resizing ? 'rgba(24,144,255,0.45)' : 'rgba(128,128,128,0.18)',
            transition: resizing ? 'none' : 'background 0.15s',
          }}
        />
        {/* Right: editor */}
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 8, overflow: 'hidden' }}>
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
              {/* This card's position inside somebody else's run.  Sits
                  above the editor because it is a fact about the card's
                  CURRENT state, not about its definition — and because a
                  card called by a held study previously showed no sign of
                  it anywhere on this pane. */}
              <CalleeHoldPanel
                projectId={projectId}
                cardId={selectedId}
                root={draft?.root}
              />
              <div style={{ flex: 1, overflow: 'auto', border: '1px solid rgba(128,128,128,0.2)', borderRadius: 4, padding: 8 }}>
                <TaskCardEditor
                  card={draft}
                  onChange={setDraft}
                  projectId={projectId}
                  onScopeStatusChange={handleScopeStatusChange}
                />
              </div>
              {/* Run history for this card.  Above the editor rather than
                  inside it: a run in flight is the thing you check before
                  editing a card, and burying it under the block tree of a
                  deep card puts it behind a scroll. */}
              <div style={{ fontSize: 11, opacity: 0.55, fontWeight: 600 }}>
                RUNS
              </div>
              <DeckRunList runs={draftRuns} onOpen={handleOpenRun} />
            </>
          ) : (
            <Empty description="Select or create a card" style={{ marginTop: 80 }} />
          )}
        </div>
      </div>
      {/* Bottom-right grip: drag to resize the dialog itself */}
      <div
        onMouseDown={handleDialogResizeStart}
        onDoubleClick={resetDialogSize}
        title="Drag to resize the dialog · double-click to reset"
        style={{
          height: 14,
          marginTop: 6,
          display: 'flex',
          justifyContent: 'flex-end',
          alignItems: 'center',
          cursor: 'nwse-resize',
        }}
      >
        <div style={{
          width: 11,
          height: 11,
          borderRight: '2px solid rgba(128,128,128,0.55)',
          borderBottom: '2px solid rgba(128,128,128,0.55)',
          borderBottomRightRadius: 3,
          opacity: dialogResizing ? 1 : 0.7,
        }} />
      </div>
      <Modal
        title="New folder"
        open={newFolderModalCardId !== null}
        onOk={handleCreateFolderAndAssign}
        onCancel={() => { setNewFolderModalCardId(null); setNewFolderName(''); }}
        okButtonProps={{ disabled: !newFolderName.trim() }}
        destroyOnClose
      >
        <Input
          placeholder="Folder name"
          value={newFolderName}
          onChange={e => setNewFolderName(e.target.value)}
          onPressEnter={handleCreateFolderAndAssign}
          autoFocus
        />
      </Modal>
    </Modal>
  );
};

export default TaskCardsLibrary;
