/**
 * BeadTree — displays the conversation's task tree with resume actions.
 *
 * Shows as a small "📿 N" indicator near the conversation header when
 * parked beads exist.  Clicking opens a popover with the full tree.
 * Users can click "Resume" on a parked bead to switch context.
 */
import React, { useState, useEffect, useCallback } from 'react';
import { Popover, Button, Empty, message, Tooltip, Spin } from 'antd';
import { useTheme } from '../context/ThemeContext';
import { useStreamingContext } from '../context/StreamingContext';
import * as beadApi from '../api/beadApi';
import type { BeadItem, BeadTreeResponse } from '../api/beadApi';


interface BeadTreeProps {
  conversationId: string;
  onResume?: (suggestedMessage: string) => void;
}

// Status → visual indicator
const STATUS_ICONS: Record<string, string> = {
  active: '▶',
  parked: '⏸',
  completed: '✓',
  abandoned: '✗',
};

const STATUS_COLORS: Record<string, string> = {
  active: '#10b981',
  parked: '#f59e0b',
  completed: '#64748b',
  abandoned: '#ef4444',
};

/**
 * Render a single bead node with its children (recursive).
 */
const BeadNode: React.FC<{
  bead: BeadItem;
  allBeads: BeadItem[];
  depth: number;
  isDarkMode: boolean;
  onResume: (beadId: string) => void;
}> = ({ bead, allBeads, depth, isDarkMode, onResume }) => {
  const children = allBeads.filter(b => b.parent_id === bead.id);
  const icon = STATUS_ICONS[bead.status] || '?';
  const color = STATUS_COLORS[bead.status] || '#888';
  const isResumable = bead.status === 'parked';

  return (
    <div style={{ marginLeft: depth * 16, marginBottom: 4 }}>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        padding: '4px 8px',
        borderRadius: 6,
        background: bead.status === 'active'
          ? (isDarkMode ? 'rgba(16, 185, 129, 0.1)' : 'rgba(16, 185, 129, 0.08)')
          : 'transparent',
        border: bead.status === 'active'
          ? `1px solid ${isDarkMode ? '#10b98144' : '#10b98133'}`
          : '1px solid transparent',
      }}>
        <span style={{ color, fontSize: 12, fontWeight: 600, flexShrink: 0 }}>{icon}</span>
        <span style={{
          flex: 1,
          fontSize: 12,
          color: isDarkMode ? '#e2e8f0' : '#334155',
          opacity: bead.status === 'completed' ? 0.6 : 1,
          textDecoration: bead.status === 'completed' ? 'line-through' : 'none',
        }}>
          {bead.content}
        </span>
        {isResumable && (
          <Tooltip title="Resume this thread">
            <button
              onClick={() => onResume(bead.id)}
              style={{
                background: 'none',
                border: `1px solid ${isDarkMode ? '#f59e0b55' : '#f59e0b44'}`,
                borderRadius: 4,
                padding: '1px 6px',
                fontSize: 10,
                color: '#f59e0b',
                cursor: 'pointer',
                flexShrink: 0,
              }}
            >
              resume
            </button>
          </Tooltip>
        )}
      </div>
      {bead.context_hint && bead.status === 'parked' && (
        <div style={{
          marginLeft: 22,
          fontSize: 10,
          color: isDarkMode ? '#64748b' : '#94a3b8',
          fontStyle: 'italic',
        }}>
          {bead.context_hint}
        </div>
      )}
      {children.map(child => (
        <BeadNode
          key={child.id}
          bead={child}
          allBeads={allBeads}
          depth={depth + 1}
          isDarkMode={isDarkMode}
          onResume={onResume}
        />
      ))}
    </div>
  );
};


const BeadTree: React.FC<BeadTreeProps> = ({ conversationId, onResume }) => {
  const { isDarkMode } = useTheme();
  const { streamingConversations } = useStreamingContext();
  const [tree, setTree] = useState<BeadTreeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const isStreaming = streamingConversations.has(conversationId);

  const loadBeads = useCallback(async () => {
    if (!conversationId) return;
    setLoading(true);
    try {
      const data = await beadApi.getBeadTree(conversationId);
      setTree(data);
    } catch (e) {
      console.debug('Bead tree load failed:', e);
    } finally {
      setLoading(false);
    }
  }, [conversationId]);

  useEffect(() => {
    if (open) loadBeads();
  }, [open, loadBeads]);

  // Refresh on conversation change
  useEffect(() => { loadBeads(); }, [conversationId]);

  // Refresh when streaming ends — new beads likely created during the turn
  useEffect(() => {
    if (!isStreaming) {
      const t = setTimeout(loadBeads, 500); // small delay to let server flush
      return () => clearTimeout(t);
    }
  }, [isStreaming, loadBeads]);

  const handleResume = useCallback(async (beadId: string) => {
    try {
      const result = await beadApi.resumeBead(conversationId, beadId);
      message.success(`Resumed: ${result.resumed_bead.content}`);
      if (onResume) onResume(result.suggested_message);
      loadBeads();
    } catch (e) {
      message.error('Failed to resume bead');
    }
  }, [conversationId, onResume, loadBeads]);

  // Compute display state — always render so the user can see system status
  const beadCount = tree?.beads.length ?? 0;
  const parkedCount = tree?.parked_count ?? 0;
  const completedCount = tree?.completed_count ?? 0;
  const activeCount = beadCount - parkedCount - completedCount;
  const rootBeads = tree?.beads.filter(b => !b.parent_id) ?? [];

  // Visual state: empty | active-only | has-parked
  const visualState = parkedCount > 0 ? 'parked' : (beadCount > 0 ? 'active' : 'empty');
  const indicatorColor = {
    parked: '#f59e0b',     // amber when threads are pending
    active: '#10b981',     // green when actively tracking
    empty:  isDarkMode ? '#475569' : '#94a3b8',  // dim when no beads yet
  }[visualState];
  const indicatorBg = {
    parked: isDarkMode ? 'rgba(245, 158, 11, 0.15)' : 'rgba(245, 158, 11, 0.1)',
    active: isDarkMode ? 'rgba(16, 185, 129, 0.12)' : 'rgba(16, 185, 129, 0.08)',
    empty:  'transparent',
  }[visualState];
  const indicatorBorder = {
    parked: `1px solid ${isDarkMode ? '#f59e0b44' : '#f59e0b33'}`,
    active: `1px solid ${isDarkMode ? '#10b98144' : '#10b98133'}`,
    empty:  `1px solid ${isDarkMode ? '#33415544' : '#cbd5e144'}`,
  }[visualState];

  const tooltipText = (() => {
    if (loading) return 'Loading bead tree…';
    if (beadCount === 0) return 'Bead tracking online — no threads yet';
    if (parkedCount > 0) return `${parkedCount} parked thread${parkedCount !== 1 ? 's' : ''} (${beadCount} total)`;
    return `${activeCount} active, ${completedCount} completed`;
  })();

  const content = (
    <div style={{
      maxWidth: 360,
      minWidth: 280,
      maxHeight: 400,
      overflowY: 'auto',
      padding: 8,
    }}>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        fontSize: 11,
        color: isDarkMode ? '#64748b' : '#94a3b8',
        marginBottom: 8,
        borderBottom: `1px solid ${isDarkMode ? '#1e293b' : '#e2e8f0'}`,
        paddingBottom: 6,
      }}>
        <span>Task threads — click ⏸ resume to switch</span>
        <button
          onClick={loadBeads}
          disabled={loading}
          style={{
            background: 'none',
            border: 'none',
            color: isDarkMode ? '#64748b' : '#94a3b8',
            cursor: loading ? 'wait' : 'pointer',
            fontSize: 11,
            padding: '0 4px',
          }}
          title="Refresh bead tree"
        >
          {loading ? <Spin size="small" /> : '↻'}
        </button>
      </div>
      <div style={{
        fontSize: 10,
        color: isDarkMode ? '#475569' : '#94a3b8',
        marginBottom: 8,
        fontFamily: 'monospace',
      }}>
        {beadCount} total · {activeCount} active · {parkedCount} parked · {completedCount} done
      </div>
      {rootBeads.length === 0 ? (
        <div style={{
          padding: '20px 12px',
          textAlign: 'center',
          fontSize: 12,
          color: isDarkMode ? '#64748b' : '#94a3b8',
        }}>
          <div style={{ fontSize: 24, marginBottom: 8, opacity: 0.4 }}>📿</div>
          <div>No threads tracked yet</div>
          <div style={{ fontSize: 10, marginTop: 4, opacity: 0.7 }}>
            Beads appear when the model identifies subtasks or unfollowed forks during a conversation.
          </div>
        </div>
      ) : (
        rootBeads.map(b => (
          <BeadNode
            key={b.id}
            bead={b}
            allBeads={tree.beads}
            depth={0}
            isDarkMode={isDarkMode}
            onResume={handleResume}
          />
        ))
      )}
    </div>
  );

  return (
    <Popover
      content={content}
      trigger="click"
      open={open}
      onOpenChange={setOpen}
      placement="topLeft"
      title={null}
    >
      <Tooltip title={tooltipText}>
        <span style={{
          cursor: 'pointer',
          fontSize: 13,
          padding: '2px 8px',
          borderRadius: 12,
          background: indicatorBg,
          color: indicatorColor,
          border: indicatorBorder,
          opacity: visualState === 'empty' ? 0.6 : 1,
          transition: 'all 0.2s',
          userSelect: 'none',
          display: 'inline-flex',
          alignItems: 'center',
          gap: 4,
        }}>
          📿{beadCount > 0 ? ` ${parkedCount > 0 ? parkedCount : beadCount}` : ''}
        </span>
      </Tooltip>
    </Popover>
  );
};

export default BeadTree;
