/**
 * HandoffBanner — the in-conversation half of a handoff's visible trail.
 *
 * Two shapes, decided by the conversation record:
 *   - SOURCE (handedOffTo set): a footer-style notice that this segment has
 *     been continued elsewhere, with a link forward.  Informational only —
 *     the source is never locked.
 *   - CONTINUATION (handoff set): a collapsible card showing the inherited
 *     document with an inline editor.  Edits go to the record and take
 *     effect on the next turn; no message is created.
 *
 * Sits in the LineageBar slot (design/conversation-handoff.md).
 */
import React, { useEffect, useState } from 'react';
import { Button, Input, Space, Typography, message } from 'antd';
import { useTheme } from '../context/ThemeContext';
import * as handoffApi from '../api/handoffApi';

const { Text } = Typography;

export interface HandoffConversationLike {
  id: string;
  title?: string;
  handedOffTo?: string;
  lineageKind?: string;
  handoff?: { document: string; editedAt?: number | null; sourceMessageCount?: number };
  branchedFrom?: string;
}

interface Props {
  conversation: HandoffConversationLike | undefined;
  conversations: HandoffConversationLike[];
  onNavigate: (id: string) => void;
  onDocumentSaved?: (id: string, handoff: any) => void;
}

const HandoffBanner: React.FC<Props> = ({ conversation, conversations, onNavigate, onDocumentSaved }) => {
  const { isDarkMode } = useTheme();
  if (!conversation) return null;
  const border = isDarkMode ? '#2a3a52' : '#cfe0f2';
  const bg = isDarkMode ? '#1a2230' : '#eef4fb';
  const link = isDarkMode ? '#4cc9f0' : '#1890ff';

  const parts: React.ReactNode[] = [];

  if (conversation.handedOffTo) {
    const child = conversations.find(c => c.id === conversation.handedOffTo);
    parts.push(
      <div key="source" data-testid="handoff-source-banner"
        style={{ background: bg, borderBottom: `1px solid ${border}`, padding: '8px 14px', fontSize: 13 }}>
        <span style={{ color: isDarkMode ? '#e2e8f0' : '#1e293b' }}>
          ⇢ This segment was continued in{' '}
          <span onClick={() => onNavigate(conversation.handedOffTo!)} style={{ color: link, cursor: 'pointer', fontWeight: 600 }}>
            {child?.title || 'the next segment'}
          </span>
        </span>
        <div style={{ fontSize: 11, color: '#64748b', marginTop: 3 }}>
          You are reading an earlier part of the track. It still works — anything you send here stays here.
        </div>
      </div>,
    );
  }

  if (conversation.handoff?.document && conversation.lineageKind === 'handoff') {
    parts.push(
      <ContinuationCard key="child" conversation={conversation} conversations={conversations}
        onNavigate={onNavigate} onDocumentSaved={onDocumentSaved} />,
    );
  }

  if (parts.length === 0) return null;
  return <div style={{ position: 'sticky', top: 0, zIndex: 5, marginBottom: 8 }}>{parts}</div>;
};

const ContinuationCard: React.FC<Props> = ({ conversation, conversations, onNavigate, onDocumentSaved }) => {
  const { isDarkMode } = useTheme();
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(conversation?.handoff?.document ?? '');
  const [saving, setSaving] = useState(false);
  useEffect(() => { setText(conversation?.handoff?.document ?? ''); }, [conversation?.id, conversation?.handoff?.document]);
  if (!conversation?.handoff) return null;

  const border = isDarkMode ? '#2a3a52' : '#cfe0f2';
  const bg = isDarkMode ? '#1a2230' : '#eef4fb';
  const link = isDarkMode ? '#4cc9f0' : '#1890ff';
  const source = conversation.branchedFrom ? conversations.find(c => c.id === conversation.branchedFrom) : undefined;
  const count = conversation.handoff.sourceMessageCount;

  const save = async () => {
    if (!text.trim()) { message.warning('The handoff document cannot be empty'); return; }
    setSaving(true);
    try {
      const res = await handoffApi.editInheritedHandoff(conversation.id, text);
      onDocumentSaved?.(conversation.id, res.handoff);
      setEditing(false);
      message.success('Handoff document updated — takes effect on the next turn');
    } catch (e: any) {
      message.error(e?.message || 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div data-testid="handoff-card" style={{ background: bg, borderBottom: `1px solid ${border}`, padding: '8px 14px', fontSize: 13 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 15 }}>⛓</span>
        <span style={{ color: isDarkMode ? '#e2e8f0' : '#1e293b' }}>
          Continues{' '}
          {conversation.branchedFrom ? (
            <span onClick={() => onNavigate(conversation.branchedFrom!)} style={{ color: link, cursor: 'pointer', fontWeight: 600 }}
              title="Open the earlier segment">
              {source?.title || 'the earlier segment'}
            </span>
          ) : 'an earlier segment'}
          {count ? <span style={{ color: '#64748b' }}> · {count} messages there</span> : null}
        </span>
        <span style={{ flex: 1 }} />
        <Button size="small" type="text" onClick={() => setExpanded(v => !v)}>
          {expanded ? 'Hide handoff' : 'Show handoff'}
        </Button>
        {expanded && !editing && <Button size="small" type="text" onClick={() => setEditing(true)}>Edit</Button>}
      </div>
      {expanded && (
        <div style={{ marginTop: 8 }}>
          {editing ? (
            <Space direction="vertical" style={{ width: '100%' }} size={6}>
              <Input.TextArea value={text} onChange={e => setText(e.target.value)} autoSize={{ minRows: 6, maxRows: 20 }}
                style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12 }} />
              <Space>
                <Button size="small" type="primary" onClick={save} loading={saving}>Save</Button>
                <Button size="small" onClick={() => { setEditing(false); setText(conversation.handoff!.document); }}>Cancel</Button>
                <Text type="secondary" style={{ fontSize: 11 }}>Injected every turn; editing creates no message.</Text>
              </Space>
            </Space>
          ) : (
            <pre style={{ margin: 0, whiteSpace: 'pre-wrap', fontSize: 12, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              color: isDarkMode ? '#cbd5e1' : '#334155', maxHeight: 320, overflow: 'auto' }}>
              {conversation.handoff.document}
            </pre>
          )}
          <div style={{ fontSize: 11, color: '#64748b', marginTop: 6 }}>
            {conversation.handoff.editedAt ? 'Edited by you. ' : ''}
            Open threads are live from the shared task tree and are not part of this text.
          </div>
        </div>
      )}
    </div>
  );
};

export default HandoffBanner;
