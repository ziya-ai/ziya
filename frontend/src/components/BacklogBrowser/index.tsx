/**
 * BacklogBrowser - project-scoped bead backlog sidebar tab.
 * See design/bead-backlog-browser.md.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, Segmented, Spin, Empty, message, Tooltip } from 'antd';
import { ReloadOutlined, SortAscendingOutlined, SortDescendingOutlined } from '@ant-design/icons';
import { useTheme } from '../../context/ThemeContext';
import { useProject } from '../../context/ProjectContext';
import { useActiveChat } from '../../context/ActiveChatContext';
import { useStreamingContext } from '../../context/StreamingContext';
import { useConversationList } from '../../context/ConversationListContext';
import * as backlogApi from '../../api/backlogApi';
import type { BacklogItem, BacklogResponse } from '../../api/backlogApi';
import * as beadApi from '../../api/beadApi';
import BacklogList from './BacklogList';
import BacklogTable from './BacklogTable';
import BeadPeekDrawer from './BeadPeekDrawer';
import { BACKLOG_COUNT_EVENT, COMPOSER_INJECT_EVENT } from './staleness';

type StatusFilter = 'parked' | 'abandoned';
type ViewMode = 'grouped' | 'flat';
type SortDir = 'asc' | 'desc';

const VIEW_KEY = 'ZIYA_BACKLOG_VIEW';
const SORT_KEY = 'ZIYA_BACKLOG_SORT';

function publishParkedCount(count: number) {
  window.dispatchEvent(new CustomEvent(BACKLOG_COUNT_EVENT, { detail: { count } }));
}

const BacklogBrowser: React.FC = () => {
  const { isDarkMode } = useTheme();
  const { currentProject } = useProject();
  const { loadConversation, loadConversationAndScrollToMessage } = useActiveChat();
  const { streamingConversations } = useStreamingContext();
  const { conversations, setConversations } = useConversationList();

  const projectId = currentProject?.id || (window as any).__ZIYA_CURRENT_PROJECT_ID__ || 'default';

  const [data, setData] = useState<BacklogResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('parked');
  const [view, setView] = useState<ViewMode>(() => (localStorage.getItem(VIEW_KEY) as ViewMode) || 'grouped');
  const [sortDir, setSortDir] = useState<SortDir>(() => (localStorage.getItem(SORT_KEY) as SortDir) || 'asc');
  const [drawerItem, setDrawerItem] = useState<BacklogItem | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [statusOverride, setStatusOverride] = useState<Record<string, beadApi.BeadItem['status']>>({});

  useEffect(() => { localStorage.setItem(VIEW_KEY, view); }, [view]);
  useEffect(() => { localStorage.setItem(SORT_KEY, sortDir); }, [sortDir]);

  const fetchBacklog = useCallback(async () => {
    setLoading(true);
    try {
      const res = await backlogApi.getBacklog(projectId, { status: 'parked,abandoned' });
      setData(res);
      setStatusOverride({});
      publishParkedCount(res.counts?.parked ?? 0);
    } catch (e) {
      console.debug('Backlog fetch failed:', e);
      message.error('Failed to load backlog');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { fetchBacklog(); }, [fetchBacklog]);

  const filteredItems = useMemo(() => {
    const items = (data?.items ?? []).map(it => {
      const ov = statusOverride[it.bead.id];
      return ov ? { ...it, bead: { ...it.bead, status: ov } } : it;
    });
    const wanted = items.filter(it => it.bead.status === statusFilter);
    if (view === 'flat') {
      const sorted = [...wanted].sort((a, b) =>
        sortDir === 'asc' ? b.age_ms - a.age_ms : a.age_ms - b.age_ms
      );
      return sorted;
    }
    return wanted;
  }, [data, statusOverride, statusFilter, view, sortDir]);

  const counts = useMemo(() => {
    const items = (data?.items ?? []).map(it => statusOverride[it.bead.id] ?? it.bead.status);
    return {
      parked: items.filter(s => s === 'parked').length,
      abandoned: items.filter(s => s === 'abandoned').length,
    };
  }, [data, statusOverride]);

  useEffect(() => { publishParkedCount(counts.parked); }, [counts.parked]);

  const openPeek = useCallback((item: BacklogItem) => {
    setDrawerItem(item);
    setDrawerOpen(true);
  }, []);
  const closePeek = useCallback(() => setDrawerOpen(false), []);

  const handleJump = useCallback(async (item: BacklogItem) => {
    if (item.bead.message_index == null) return;
    setDrawerOpen(false);
    try {
      await loadConversationAndScrollToMessage(item.conversation_id, item.bead.message_index - 1);
    } catch (e) {
      message.error('Failed to jump to seam');
    }
  }, [loadConversationAndScrollToMessage]);

  const handleResume = useCallback(async (item: BacklogItem) => {
    setActionBusy(true);
    try {
      const result = await beadApi.resumeBead(item.conversation_id, item.bead.id);
      setDrawerOpen(false);
      loadConversation(item.conversation_id);
      window.setTimeout(() => {
        document.dispatchEvent(new CustomEvent(COMPOSER_INJECT_EVENT, {
          detail: { conversationId: item.conversation_id, text: result.suggested_message },
        }));
      }, 250);
      message.success(`Resumed: ${result.resumed_bead.content}`);
      fetchBacklog();
    } catch (e) {
      message.error('Failed to resume thread');
    } finally {
      setActionBusy(false);
    }
  }, [loadConversation, fetchBacklog]);

  const handleBranch = useCallback(async (item: BacklogItem) => {
    if (!item.can_branch) return;
    setActionBusy(true);
    try {
      const result = await beadApi.forkFromBead(item.conversation_id, item.bead.id);
      const parent = conversations.find(c => c.id === item.conversation_id);
      const now = Date.now();
      const branchShell: any = {
        id: result.new_chat_id,
        title: result.branchedFromLabel || 'Branch',
        messages: [],
        projectId: (parent as any)?.projectId,
        folderId: (parent as any)?.folderId ?? null,
        lastAccessedAt: now,
        isActive: true,
        _version: now,
        _isShell: true,
        hasUnreadResponse: false,
        branchedFrom: result.branchedFrom,
        branchedAtMessageIndex: result.branchedAtMessageIndex,
        branchedFromLabel: result.branchedFromLabel,
      };
      setConversations(prev =>
        prev.some(c => c.id === branchShell.id) ? prev : [...prev, branchShell]
      );
      setDrawerOpen(false);
      message.success(`Branched: ${result.branchedFromLabel || 'thread'} - original preserved`);
      loadConversation(result.new_chat_id);
    } catch (e) {
      message.error('Failed to branch from thread');
    } finally {
      setActionBusy(false);
    }
  }, [conversations, setConversations, loadConversation]);

  const flipStatus = useCallback(async (item: BacklogItem, next: backlogApi.BacklogStatus) => {
    const prev = item.bead.status;
    setStatusOverride(o => ({ ...o, [item.bead.id]: next }));
    setDrawerOpen(false);
    try {
      await backlogApi.setBeadStatus(projectId, item.conversation_id, item.bead.id, next);
      if (next === 'abandoned') {
        const undo = () => {
          setStatusOverride(o => ({ ...o, [item.bead.id]: 'parked' }));
          backlogApi
            .setBeadStatus(projectId, item.conversation_id, item.bead.id, 'parked')
            .catch(() => { message.error('Undo failed'); fetchBacklog(); });
        };
        message.success({
          content: (
            <span>
              Abandoned "{item.bead.content.slice(0, 40)}"{' '}
              <a onClick={undo} style={{ marginLeft: 6 }}>Undo</a>
            </span>
          ),
          duration: 6,
        });
      } else {
        message.success('Restored to backlog');
      }
    } catch (e) {
      setStatusOverride(o => ({ ...o, [item.bead.id]: prev }));
      message.error(`Failed to ${next === 'abandoned' ? 'abandon' : 'restore'} thread`);
    }
  }, [projectId, fetchBacklog]);

  const handleAbandon = useCallback((item: BacklogItem) => flipStatus(item, 'abandoned'), [flipStatus]);
  const handleRestore = useCallback((item: BacklogItem) => flipStatus(item, 'parked'), [flipStatus]);

  const subtle = isDarkMode ? '#94a3b8' : '#64748b';
  const drawerTargetStreaming = drawerItem ? streamingConversations.has(drawerItem.conversation_id) : false;

  const chip = (key: StatusFilter, label: string, n: number) => {
    const active = statusFilter === key;
    return (
      <button
        key={key}
        onClick={() => setStatusFilter(key)}
        style={{
          border: `1px solid ${active ? (isDarkMode ? '#f59e0b66' : '#f59e0b55') : (isDarkMode ? '#33415566' : '#cbd5e1')}`,
          background: active ? (isDarkMode ? 'rgba(245,158,11,0.15)' : 'rgba(245,158,11,0.1)') : 'transparent',
          color: active ? '#f59e0b' : subtle,
          borderRadius: 12, padding: '2px 10px', fontSize: 11, cursor: 'pointer',
        }}
      >
        {label} {n > 0 && <span style={{ opacity: 0.8 }}>({n})</span>}
      </button>
    );
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: '8px 8px 4px', display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        {chip('parked', 'Parked', counts.parked)}
        {chip('abandoned', 'Abandoned', counts.abandoned)}
        <span style={{ flex: 1 }} />
        <Tooltip title="Refresh backlog">
          <Button
            type="text" size="small"
            icon={loading ? <Spin size="small" /> : <ReloadOutlined />}
            onClick={fetchBacklog} disabled={loading}
          />
        </Tooltip>
      </div>

      <div style={{ padding: '0 8px 8px', display: 'flex', alignItems: 'center', gap: 8 }}>
        <Segmented
          size="small"
          value={view}
          onChange={v => setView(v as ViewMode)}
          options={[
            { label: 'Grouped', value: 'grouped' },
            { label: 'Flat', value: 'flat' },
          ]}
        />
        {view === 'flat' && (
          <Tooltip title={sortDir === 'asc' ? 'Oldest first' : 'Newest first'}>
            <Button
              type="text" size="small"
              icon={sortDir === 'asc' ? <SortDescendingOutlined /> : <SortAscendingOutlined />}
              onClick={() => setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))}
            />
          </Tooltip>
        )}
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '0 8px 12px' }}>
        {loading && !data ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}>
            <Spin />
          </div>
        ) : filteredItems.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <span style={{ fontSize: 12, color: subtle }}>
                {statusFilter === 'parked'
                  ? 'Nothing parked across this project'
                  : 'No abandoned threads'}
              </span>
            }
            style={{ marginTop: 40 }}
          />
        ) : view === 'grouped' ? (
          <BacklogList items={filteredItems} onPeek={openPeek} />
        ) : (
          <BacklogTable items={filteredItems} onPeek={openPeek} />
        )}
      </div>

      {data && (
        <div style={{ padding: '4px 8px', fontSize: 10, color: subtle, fontFamily: 'monospace', borderTop: `1px solid ${isDarkMode ? '#1e293b' : '#e2e8f0'}` }}>
          scanned {data.scanned_chats} chat{data.scanned_chats !== 1 ? 's' : ''}
        </div>
      )}

      <BeadPeekDrawer
        item={drawerItem}
        open={drawerOpen}
        onClose={closePeek}
        isTargetStreaming={drawerTargetStreaming}
        busy={actionBusy}
        onResume={handleResume}
        onBranch={handleBranch}
        onJump={handleJump}
        onAbandon={handleAbandon}
        onRestore={handleRestore}
      />
    </div>
  );
};

export default BacklogBrowser;
