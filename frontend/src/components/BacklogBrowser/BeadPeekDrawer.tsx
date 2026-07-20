/**
 * BeadPeekDrawer - antd Drawer overlay for triaging a single backlog bead.
 */
import React from 'react';
import { Drawer, Button, Tag, Tooltip, Space } from 'antd';
import {
  PlayCircleOutlined,
  BranchesOutlined,
  AimOutlined,
  DeleteOutlined,
  UndoOutlined,
} from '@ant-design/icons';
import { useTheme } from '../../context/ThemeContext';
import type { BacklogItem } from '../../api/backlogApi';
import { STATUS_GLYPH, formatAge, stalenessColor, stalenessMarker } from './staleness';

interface BeadPeekDrawerProps {
  item: BacklogItem | null;
  open: boolean;
  onClose: () => void;
  isTargetStreaming: boolean;
  busy?: boolean;
  onResume: (item: BacklogItem) => void;
  onBranch: (item: BacklogItem) => void;
  onJump: (item: BacklogItem) => void;
  onAbandon: (item: BacklogItem) => void;
  onRestore: (item: BacklogItem) => void;
}

const BeadPeekDrawer: React.FC<BeadPeekDrawerProps> = ({
  item,
  open,
  onClose,
  isTargetStreaming,
  busy = false,
  onResume,
  onBranch,
  onJump,
  onAbandon,
  onRestore,
}) => {
  const { isDarkMode } = useTheme();

  if (!item) {
    return <Drawer open={open} onClose={onClose} width={420} title="Peek" />;
  }

  const { bead } = item;
  const isAbandoned = bead.status === 'abandoned';
  const ageColor = stalenessColor(item.age_ms, isDarkMode);
  const marker = stalenessMarker(item.age_ms);
  const subtle = isDarkMode ? '#94a3b8' : '#64748b';
  const faint = isDarkMode ? '#64748b' : '#94a3b8';
  const panelBg = isDarkMode ? 'rgba(148,163,184,0.08)' : 'rgba(100,116,139,0.06)';

  const header = (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span style={{ fontSize: 14, fontWeight: 600 }}>
        {item.conversation_title || 'Untitled conversation'}
      </span>
      <span style={{ fontSize: 11, color: ageColor || subtle }}>
        {marker && <span style={{ marginRight: 4 }}>{marker}</span>}
        {formatAge(item.age_ms)} old
        {isAbandoned && (
          <Tag color="error" style={{ marginLeft: 8, fontSize: 10, lineHeight: '16px' }}>
            abandoned
          </Tag>
        )}
      </span>
    </div>
  );

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={420}
      title={header}
      styles={{ body: { padding: 16 } }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 16 }}>
        <span style={{ color: '#f59e0b', fontSize: 15, lineHeight: '20px', flexShrink: 0 }}>
          {STATUS_GLYPH[bead.status] || '\u25D0'}
        </span>
        <span style={{ fontSize: 14, color: isDarkMode ? '#e2e8f0' : '#1e293b', lineHeight: '20px' }}>
          {bead.content}
        </span>
      </div>

      {item.breadcrumb && item.breadcrumb.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 10, textTransform: 'uppercase', color: faint, marginBottom: 4, letterSpacing: 0.4 }}>
            Path
          </div>
          <div style={{ fontSize: 12, color: subtle, lineHeight: '18px' }}>
            {item.breadcrumb.map((crumb, i) => (
              <React.Fragment key={i}>
                {i > 0 && <span style={{ color: faint, margin: '0 4px' }}>{'\u203A'}</span>}
                <span style={{ fontWeight: i === item.breadcrumb.length - 1 ? 600 : 400 }}>{crumb}</span>
              </React.Fragment>
            ))}
          </div>
        </div>
      )}

      {item.descendant_parked_count > 0 && (
        <div style={{ marginBottom: 16, fontSize: 11, color: subtle }}>
          + {item.descendant_parked_count} parked descendant
          {item.descendant_parked_count !== 1 ? 's' : ''} below this thread
        </div>
      )}

      {bead.context_hint && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 10, textTransform: 'uppercase', color: faint, marginBottom: 4, letterSpacing: 0.4 }}>
            Note
          </div>
          <div style={{
            fontSize: 12,
            fontStyle: 'italic',
            color: subtle,
            padding: '8px 10px',
            background: panelBg,
            borderRadius: 6,
          }}>
            {bead.context_hint}
          </div>
        </div>
      )}

      {item.seam_snippet && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 10, textTransform: 'uppercase', color: faint, marginBottom: 4, letterSpacing: 0.4 }}>
            Seam - {item.seam_snippet.role}
            {bead.message_index != null && (
              <span style={{ marginLeft: 6, fontFamily: 'monospace' }}>#{bead.message_index}</span>
            )}
          </div>
          <div style={{
            fontSize: 12,
            color: subtle,
            padding: '8px 10px',
            background: panelBg,
            borderRadius: 6,
            whiteSpace: 'pre-wrap',
            maxHeight: 160,
            overflowY: 'auto',
          }}>
            {item.seam_snippet.text}
          </div>
        </div>
      )}

      <Space wrap style={{ marginTop: 8 }}>
        {!isAbandoned && (
          <Tooltip
            title={isTargetStreaming
              ? 'Target conversation is streaming - resume when it settles'
              : 'Resume this thread in its conversation'}
          >
            <span>
              <Button
                type="primary"
                icon={<PlayCircleOutlined />}
                disabled={isTargetStreaming || busy}
                onClick={() => onResume(item)}
              >
                Resume
              </Button>
            </span>
          </Tooltip>
        )}

        {!isAbandoned && item.can_branch && (
          <Tooltip title="Split this thread into its own conversation from the seam">
            <Button icon={<BranchesOutlined />} disabled={busy} onClick={() => onBranch(item)}>
              Branch
            </Button>
          </Tooltip>
        )}
        {item.can_branch && (
          <Tooltip title="Open the conversation and scroll to where the thread was parked">
            <Button icon={<AimOutlined />} disabled={busy} onClick={() => onJump(item)}>
              Jump to seam
            </Button>
          </Tooltip>
        )}

        {isAbandoned ? (
          <Tooltip title="Restore this thread to the parked backlog">
            <Button icon={<UndoOutlined />} disabled={busy} onClick={() => onRestore(item)}>
              Restore
            </Button>
          </Tooltip>
        ) : (
          <Tooltip title="Abandon this thread (undoable - it stays browsable under the Abandoned filter)">
            <Button danger icon={<DeleteOutlined />} disabled={busy} onClick={() => onAbandon(item)}>
              Abandon
            </Button>
          </Tooltip>
        )}
      </Space>
    </Drawer>
  );
};

export default BeadPeekDrawer;
