/**
 * BacklogList - backlog items grouped by their owning conversation.
 */
import React, { useMemo, useState } from 'react';
import { useTheme } from '../../context/ThemeContext';
import type { BacklogItem } from '../../api/backlogApi';
import { STATUS_GLYPH, formatAge, stalenessColor, stalenessMarker } from './staleness';

interface BacklogListProps {
  items: BacklogItem[];
  onPeek: (item: BacklogItem) => void;
  /** Bead last peeked/acted on — row stays highlighted for orientation. */
  selectedBeadId?: string | null;
}

interface Group {
  conversationId: string;
  title: string;
  items: BacklogItem[];
}

const BacklogList: React.FC<BacklogListProps> = ({ items, onPeek, selectedBeadId }) => {
  const { isDarkMode } = useTheme();
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const groups = useMemo<Group[]>(() => {
    const byConv = new Map<string, Group>();
    for (const it of items) {
      let g = byConv.get(it.conversation_id);
      if (!g) {
        g = { conversationId: it.conversation_id, title: it.conversation_title || 'Untitled', items: [] };
        byConv.set(it.conversation_id, g);
      }
      g.items.push(it);
    }
    const out = Array.from(byConv.values());
    out.forEach(g => g.items.sort((a, b) => b.age_ms - a.age_ms));
    return out;
  }, [items]);

  const subtle = isDarkMode ? '#94a3b8' : '#64748b';
  const faint = isDarkMode ? '#64748b' : '#94a3b8';

  if (items.length === 0) return null;

  return (
    <div>
      {groups.map(g => {
        const isCollapsed = collapsed[g.conversationId] ?? false;
        return (
          <div key={g.conversationId} style={{ marginBottom: 10 }}>
            <div
              onClick={() => setCollapsed(p => ({ ...p, [g.conversationId]: !isCollapsed }))}
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '4px 6px', cursor: 'pointer', borderRadius: 4,
              }}
            >
              <span style={{ color: faint, fontSize: 10 }}>{isCollapsed ? '\u25B6' : '\u25BC'}</span>
              <span style={{ flex: 1, fontSize: 12, fontWeight: 600, color: isDarkMode ? '#e2e8f0' : '#334155', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {g.title}
              </span>
              <span style={{ fontSize: 10, color: faint }}>{g.items.length}</span>
            </div>

            {!isCollapsed && (
              <div style={{ marginLeft: 12, borderLeft: `1px solid ${isDarkMode ? '#1e293b' : '#e2e8f0'}`, paddingLeft: 8 }}>
                {g.items.map(it => {
                  const ageColor = stalenessColor(it.age_ms, isDarkMode);
                  const marker = stalenessMarker(it.age_ms);
                  const abandoned = it.bead.status === 'abandoned';
                  const selected = it.bead.id === selectedBeadId;
                  return (
                    <div
                      key={it.bead.id}
                      onClick={() => onPeek(it)}
                      title="Peek"
                      style={{
                        display: 'flex', alignItems: 'center', gap: 6,
                        padding: '5px 6px', marginBottom: 2, borderRadius: 6,
                        cursor: 'pointer', opacity: abandoned ? 0.55 : 1,
                        background: selected
                          ? (isDarkMode ? 'rgba(245,158,11,0.12)' : 'rgba(245,158,11,0.10)')
                          : 'transparent',
                        outline: selected
                          ? `1px solid ${isDarkMode ? '#f59e0b55' : '#f59e0b44'}`
                          : 'none',
                      }}
                    >
                      <span style={{ color: abandoned ? '#ef4444' : '#f59e0b', fontSize: 12, flexShrink: 0 }}>
                        {STATUS_GLYPH[it.bead.status] || '\u25D0'}
                      </span>
                      <span style={{
                        flex: 1, fontSize: 12, color: isDarkMode ? '#e2e8f0' : '#334155',
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        textDecoration: abandoned ? 'line-through' : 'none',
                      }}>
                        {it.bead.content}
                        {it.descendant_parked_count > 0 && (
                          <span style={{ color: faint, marginLeft: 4 }}>+{it.descendant_parked_count}</span>
                        )}
                      </span>
                      {it.bead.message_index != null && (
                        <span style={{ fontSize: 10, color: faint, fontFamily: 'monospace', flexShrink: 0 }}>
                          #{it.bead.message_index}
                        </span>
                      )}
                      <span style={{ fontSize: 10, color: ageColor || subtle, flexShrink: 0, minWidth: 34, textAlign: 'right' }}>
                        {marker && <span style={{ marginRight: 2 }}>{marker}</span>}
                        {formatAge(it.age_ms)}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

export default BacklogList;
