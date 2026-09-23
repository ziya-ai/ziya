/**
 * HandoffDrawer — inspect, edit and commit a conversation handoff.
 *
 * A handoff is a deliberate move to a fresh conversation, not compaction:
 * the source stays whole and searchable; the continuation starts empty
 * with the document below as a per-turn prelude plus the source id for
 * full-fidelity retrieval (chat_read / chat_search).  See
 * design/conversation-handoff.md.
 *
 * The document is the LIVING DRAFT the model maintains via handoff_write;
 * the user edits the same record here.  "Open threads" is read-only — it
 * is the shared bead tree, which the continuation shares rather than
 * copies, so it is never baked into the document.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Button, Drawer, Input, Space, Spin, Tag, Tooltip, Typography, message } from 'antd';
import { useActiveChat } from '../context/ActiveChatContext';
import { useConversationList } from '../context/ConversationListContext';
import { useTheme } from '../context/ThemeContext';
import * as handoffApi from '../api/handoffApi';
import type { HandoffState } from '../api/handoffApi';
import { useHandoffDraftRequest } from '../hooks/useHandoffDraftRequest';

const { Text } = Typography;

interface Props {
  conversationId: string | null;
  onClose: () => void;
}

const HandoffDrawer: React.FC<Props> = ({ conversationId, onClose }) => {
  const { isDarkMode } = useTheme();
  const { loadConversation, currentConversationId, isStreaming } = useActiveChat();
  const { conversations, setConversations } = useConversationList();
  const requestDraft = useHandoffDraftRequest();
  const [state, setState] = useState<HandoffState | null>(null);
  const [loading, setLoading] = useState(false);
  const [doc, setDoc] = useState('');
  const [workingSet, setWorkingSet] = useState<string[]>([]);
  const [busy, setBusy] = useState<'save' | 'commit' | 'draft' | null>(null);

  const open = !!conversationId;

  const reload = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const s = await handoffApi.getHandoffState(id);
      setState(s);
      setDoc(s.draft?.document ?? '');
      setWorkingSet(s.additionalFiles ?? []);
    } catch (e: any) {
      message.error(e?.message || 'Could not load handoff state');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (conversationId) reload(conversationId);
    else { setState(null); setDoc(''); }
  }, [conversationId, reload]);

  const dirty = useMemo(() => (state?.draft?.document ?? '') !== doc, [state, doc]);
  const hasDraft = !!state?.draft?.document || doc.trim().length > 0;
  const canRequestDraft = !!conversationId && conversationId === currentConversationId && !isStreaming;

  // Background, non-persisted turn on the conversation's full context.  The
  // drawer stays open with a spinner; the draft appears here when it lands
  // and a local (muted) notice is added to the transcript by the hook.
  const askModelToDraft = async () => {
    if (!conversationId) return;
    setBusy('draft');
    try {
      const draft = await requestDraft(conversationId);
      if (draft) {
        await reload(conversationId);
        message.success('Handoff draft written');
      } else {
        message.warning('The model finished without saving a draft — try again or write it below.');
      }
    } catch (e: any) {
      message.error(e?.message || 'Draft request failed');
    } finally {
      setBusy(null);
    }
  };

  const saveDraft = async () => {
    if (!conversationId) return;
    setBusy('save');
    try {
      await handoffApi.editHandoffDraft(conversationId, doc);
      await reload(conversationId);
      message.success(doc.trim() ? 'Draft saved' : 'Draft cleared');
    } catch (e: any) {
      message.error(e?.message || 'Save failed');
    } finally {
      setBusy(null);
    }
  };

  const commit = async () => {
    if (!conversationId || !doc.trim()) return;
    setBusy('commit');
    try {
      const res = await handoffApi.commitHandoff(conversationId, { document: doc, workingSet });
      const child = res.child;
      const parent = conversations.find(c => c.id === conversationId);
      const now = Date.now();
      // Insert a lineage-stamped shell now (same pattern as useBranchFromBead)
      // and link the source forward, so the sidebar shows the chain before
      // the next server sync; loadConversation hydrates the shell.
      const shell: any = {
        ...child,
        messages: [],
        projectId: (parent as any)?.projectId ?? child.projectId,
        folderId: (parent as any)?.folderId ?? child.folderId ?? null,
        lastAccessedAt: now,
        isActive: true,
        _isShell: true,
        hasUnreadResponse: false,
      };
      setConversations(prev => prev
        .map(c => (c.id === conversationId ? { ...c, handedOffTo: res.handedOffTo } as any : c))
        .concat(prev.some(c => c.id === shell.id) ? [] : [shell]));
      message.success(`Continuing in "${child.title}" — the original stays intact`);
      onClose();
      loadConversation(res.handedOffTo);
    } catch (e: any) {
      message.error(e?.message || 'Could not create continuation');
    } finally {
      setBusy(null);
    }
  };

  const pressurePct = state?.contextPressure != null ? Math.round(state.contextPressure * 100) : null;
  const muted = isDarkMode ? '#94a3b8' : '#64748b';

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={640}
      title={
        <Space direction="vertical" size={0}>
          <span>Hand off: {state?.title || '…'}</span>
          <Text type="secondary" style={{ fontSize: 11, fontWeight: 400 }}>
            {state ? `${state.messageCount} messages` : ''}
            {pressurePct != null ? ` · context ${pressurePct}%` : ''}
            {state?.draft?.editedAt ? ' · draft edited by you' : state?.draft ? ' · model-maintained draft' : ''}
          </Text>
        </Space>
      }
      footer={
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Text type="secondary" style={{ fontSize: 11 }}>
            The source stays intact and searchable. The continuation can read any turn via chat_read.
          </Text>
          <Space>
            <Button onClick={onClose}>Cancel</Button>
            <Button onClick={saveDraft} disabled={!dirty} loading={busy === 'save'}>
              Save draft
            </Button>
            <Tooltip title={!doc.trim() ? 'Write or request a draft first' : undefined}>
              <Button type="primary" onClick={commit} disabled={!doc.trim()} loading={busy === 'commit'}>
                Create continuation ⇢
              </Button>
            </Tooltip>
          </Space>
        </div>
      }
    >
      {state?.handedOffTo && (
        <Alert
          type="info" showIcon style={{ marginBottom: 12 }}
          message="This conversation has already been continued elsewhere"
          description="Committing again creates a second continuation. The existing one is unaffected."
        />
      )}
      {state?.handoff && (
        <Alert
          type="warning" showIcon style={{ marginBottom: 12 }}
          message="This is itself a continuation"
          description="A new hop starts from THIS conversation's own draft, not from the document it inherited."
        />
      )}

      <Section label="Handoff document" hint="markdown · the model maintains this via handoff_write; edit freely">
        {busy === 'draft' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
            <Spin size="small" />
            <Text type="secondary" style={{ fontSize: 12 }}>
              Drafting from the full conversation context… nothing is added to the transcript.
            </Text>
          </div>
        )}
        {!hasDraft && !loading && busy !== 'draft' && (
          <div style={{ marginBottom: 8 }}>
            <Tooltip title={!canRequestDraft ? (isStreaming ? 'Wait for the current response to finish' : 'Open this conversation first') : undefined}>
              <Button size="small" onClick={askModelToDraft} disabled={!canRequestDraft}>Ask the model to draft it</Button>
            </Tooltip>
            <Text type="secondary" style={{ fontSize: 11, marginLeft: 8 }}>or write it below</Text>
          </div>
        )}
        <Input.TextArea
          value={doc}
          onChange={e => setDoc(e.target.value)}
          autoSize={{ minRows: 10, maxRows: 24 }}
          placeholder="## Objective&#10;…&#10;&#10;## State&#10;…&#10;&#10;## Decisions&#10;…"
          style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12 }}
          disabled={loading || busy === 'draft'}
        />
      </Section>

      <Section label="Open threads" hint="live · shared with the continuation, not copied">
        {state?.openBeads?.length ? (
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {state.openBeads.map(b => (
              <li key={b.id} style={{ fontSize: 13 }}>
                <Tag style={{ fontSize: 10, lineHeight: '16px', padding: '0 5px' }}>{b.status}</Tag>{b.content}
              </li>
            ))}
          </ul>
        ) : <Text type="secondary" style={{ fontSize: 12 }}>none open</Text>}
      </Section>

      <Section label="Working set" hint="files the continuation opens with">
        <Space size={[4, 6]} wrap>
          {workingSet.map(f => (
            <Tag key={f} closable onClose={() => setWorkingSet(ws => ws.filter(x => x !== f))} style={{ fontSize: 11 }}>
              {f}
            </Tag>
          ))}
          {workingSet.length === 0 && <Text type="secondary" style={{ fontSize: 12 }}>none</Text>}
        </Space>
      </Section>

      {state?.predecessors?.length ? (
        <Section label="Earlier segments">
          {state.predecessors.map(p => (
            <div key={p.id} style={{ fontSize: 12, color: muted }}>
              {p.title || p.id}{p.messageCount != null ? ` · ${p.messageCount} messages` : ''}
            </div>
          ))}
        </Section>
      ) : null}
    </Drawer>
  );
};

const Section: React.FC<{ label: string; hint?: string; children: React.ReactNode }> = ({ label, hint, children }) => (
  <div style={{ marginBottom: 16 }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
      <Text style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.04em' }} type="secondary">{label}</Text>
      {hint && <Text type="secondary" style={{ fontSize: 11 }}>{hint}</Text>}
    </div>
    {children}
  </div>
);

export default HandoffDrawer;
