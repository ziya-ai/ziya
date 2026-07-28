/**
 * BacklogTable - flat triage view.
 */
import React from 'react';
import { Button, Tooltip } from 'antd';
import { EyeOutlined } from '@ant-design/icons';
import { useTheme } from '../../context/ThemeContext';
import type { BacklogItem } from '../../api/backlogApi';
import { STATUS_GLYPH, formatAge, stalenessColor, stalenessMarker } from './staleness';

interface BacklogTableProps {
  items: BacklogItem[];
  onPeek: (item: BacklogItem) => void;
  /** Bead last peeked/acted on — row stays highlighted for orientation. */
  selectedBeadId?: string | null;
}

const BacklogTable: React.FC<BacklogTableProps> = ({ items, onPeek, selectedBeadId }) => {
  const { isDarkMode } = useTheme();
  const subtle = isDarkMode ? '#94a3b8' : '#64748b';
  const faint = isDarkMode ? '#64748b' : '#94a3b8';
  const rowBorder = isDarkMode ? '#1e293b' : '#f1f5f9';

  if (items.length === 0) return null;

  const cell: React.CSSProperties = { padding: '6px 6px', fontSize: 12, verticalAlign: 'top' };
  const th: React.CSSProperties = {
    padding: '4px 6px', fontSize: 9, textTransform: 'uppercase', letterSpacing: 0.5,
    color: faint, textAlign: 'left', fontWeight: 600, position: 'sticky', top: 0,
    background: isDarkMode ? '#141414' : '#ffffff', zIndex: 1,
  };

  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', tableLayout: 'fixed' }}>
      <thead>
        <tr>
          <th style={{ ...th, width: 44 }}>Age</th>
          <th style={th}>Thread</th>
          <th style={{ ...th, width: '38%' }}>Conversation</th>
          <th style={{ ...th, width: 34 }} />
        </tr>
      </thead>
      <tbody>
        {items.map(it => {
          const ageColor = stalenessColor(it.age_ms, isDarkMode);
          const marker = stalenessMarker(it.age_ms);
          const abandoned = it.bead.status === 'abandoned';
          const selected = it.bead.id === selectedBeadId;
          return (
            <tr
              key={it.bead.id}
              style={{
                borderTop: `1px solid ${rowBorder}`, cursor: 'pointer',
                opacity: abandoned ? 0.55 : 1,
                background: selected
                  ? (isDarkMode ? 'rgba(245,158,11,0.12)' : 'rgba(245,158,11,0.10)')
                  : 'transparent',
              }}
              onClick={() => onPeek(it)}
            >
              <td style={{ ...cell, color: ageColor || subtle, whiteSpace: 'nowrap' }}>
                {marker && <span style={{ marginRight: 2 }}>{marker}</span>}
                {formatAge(it.age_ms)}
              </td>
              <td style={cell}>
                <span style={{ color: abandoned ? '#ef4444' : '#f59e0b', marginRight: 4 }}>
                  {STATUS_GLYPH[it.bead.status] || '\u25D0'}
                </span>
                <span style={{
                  color: isDarkMode ? '#e2e8f0' : '#334155',
                  textDecoration: abandoned ? 'line-through' : 'none',
                }}>
                  {it.bead.content}
                </span>
                {it.descendant_parked_count > 0 && (
                  <span style={{ color: faint, marginLeft: 4 }}>+{it.descendant_parked_count}</span>
                )}
              </td>
              <td style={{ ...cell, color: subtle, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {it.conversation_title || 'Untitled'}
              </td>
              <td style={{ ...cell, textAlign: 'right' }}>
                <Tooltip title="Peek">
                  <Button
                    type="text"
                    size="small"
                    icon={<EyeOutlined />}
                    onClick={e => { e.stopPropagation(); onPeek(it); }}
                  />
                </Tooltip>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
};

export default BacklogTable;
