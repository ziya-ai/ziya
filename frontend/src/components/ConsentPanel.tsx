/**
 * ConsentPanel — renders the tool-consent request(s) a live chat turn is
 * parked on, and answers them (design/consent-runtime.md step 3).
 *
 * Sources of truth, in order:
 *   1. On mount / conversation change: GET /api/consent/open — this is the
 *      reconnect path.  A tab that reloaded while a consent was pending, or
 *      a second window on the same conversation, sees the request even when
 *      its ``consent_opened`` frame is not in the relay replay.
 *   2. Live: chatApi forwards ``consent_opened`` / ``consent_answered`` as
 *      DOM CustomEvents (same pattern as ``feedbackDelivered``).
 *
 * First answer wins server-side, so two windows answering at once is safe;
 * a 409 here means the turn stopped waiting (cancelled or expired) and the
 * panel simply drops the request.
 *
 * Reuses the task-card Ask panel's CSS so both human-in-the-loop surfaces
 * look like the same thing, which they are.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Input, Select, Tooltip } from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined } from '@ant-design/icons';
import {
  CONSENT_ANSWERED_EVENT, CONSENT_OPENED_EVENT, SCOPE_LABELS,
  answerConsent, applyAnswered, applyOpened, applyServerList, describe,
  fetchOpenConsents, openFor,
} from '../utils/consentStore';
import type { ConsentRecord, ConsentScope, OpenConsents } from '../utils/consentStore';
import './TaskCard/task-card-inline-tile.css';

interface Props {
  conversationId: string;
}

const ConsentPanel: React.FC<Props> = ({ conversationId }) => {
  const [all, setAll] = useState<OpenConsents>([]);
  const [busy, setBusy] = useState<string | null>(null);
  // Requests that arrived on the live stream AFTER the reconnect fetch was
  // issued.  The server's reply is authoritative for everything it could
  // have known about, but it cannot include a consent opened after the
  // request left, so those must survive the replace.  Without this a
  // consent_opened landing in the fetch window was silently dropped.
  const liveSinceFetch = useRef<Set<string>>(new Set());

  // Reconnect / cross-window path.
  useEffect(() => {
    if (!conversationId) return;
    let live = true;
    liveSinceFetch.current = new Set();
    fetchOpenConsents(conversationId).then(list => {
      if (!live) return;
      const keep = liveSinceFetch.current;
      setAll(prev => {
        const arrivedMeanwhile = openFor(prev, conversationId)
          .filter(r => keep.has(r.request_id) && !list.some(s => s.request_id === r.request_id));
        return applyServerList(prev, conversationId, [...list, ...arrivedMeanwhile]);
      });
    });
    return () => { live = false; };
  }, [conversationId]);

  // Live path.
  useEffect(() => {
    const onOpened = (e: Event) => {
      const rec = (e as CustomEvent<ConsentRecord>).detail;
      if (rec?.request_id) {
        liveSinceFetch.current.add(rec.request_id);
        setAll(prev => applyOpened(prev, rec));
      }
    };
    const onAnswered = (e: Event) => {
      const ev = (e as CustomEvent<{ request_id: string }>).detail;
      if (ev?.request_id) setAll(prev => applyAnswered(prev, ev));
    };
    document.addEventListener(CONSENT_OPENED_EVENT, onOpened);
    document.addEventListener(CONSENT_ANSWERED_EVENT, onAnswered);
    return () => {
      document.removeEventListener(CONSENT_OPENED_EVENT, onOpened);
      document.removeEventListener(CONSENT_ANSWERED_EVENT, onAnswered);
    };
  }, []);

  const answer = useCallback(async (
    rec: ConsentRecord, decision: 'approve' | 'reject', scope: ConsentScope, text: string,
  ) => {
    setBusy(rec.request_id);
    const res = await answerConsent(rec.request_id, decision, scope, text);
    setBusy(null);
    // Success, or the server says nothing is waiting any more: either way
    // the request leaves the panel.  The stream's consent_answered frame
    // will also arrive on success; applyAnswered is idempotent.
    if (res.ok || res.status === 404 || res.status === 409) {
      setAll(prev => applyAnswered(prev, { request_id: rec.request_id }));
    }
  }, []);

  const mine = openFor(all, conversationId);
  if (mine.length === 0) return null;

  return (
    <div className="consent-panel-stack">
      {mine.map(rec => (
        <ConsentRequest
          key={rec.request_id}
          rec={rec}
          busy={busy === rec.request_id}
          onAnswer={(d, s, t) => answer(rec, d, s, t)}
        />
      ))}
    </div>
  );
};

const ConsentRequest: React.FC<{
  rec: ConsentRecord;
  busy: boolean;
  onAnswer: (decision: 'approve' | 'reject', scope: ConsentScope, text: string) => void;
}> = ({ rec, busy, onAnswer }) => {
  const [scope, setScope] = useState<ConsentScope>('once');
  const [text, setText] = useState('');
  const p = rec.payload;
  const scopes: ConsentScope[] = rec.scopes ?? ['once', 'conversation', 'session', 'always'];
  const preflight = p.kind === 'tool_call' ? p.preflight : null;
  const args = p.kind === 'tool_call' ? p.args : null;

  return (
    <div className="tc-ask-panel" role="group" aria-label="Tool call — awaiting your approval">
      <div className="tc-ask-panel__q">
        <span aria-hidden className="tc-ask-panel__icon">?</span>
        <span className="tc-ask-panel__question">{describe(rec)}</span>
      </div>

      {/* You approve what WILL happen: preflight first, raw args as fallback. */}
      {(preflight || args) && (
        <pre className="tc-ask-panel__detail" style={{ maxHeight: 220, overflow: 'auto', fontSize: 12 }}>
          {JSON.stringify(preflight ?? args, null, 2)}
        </pre>
      )}

      <div className="tc-ask-panel__free" style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <Select<ConsentScope>
          size="small"
          value={scope}
          onChange={setScope}
          disabled={busy}
          style={{ minWidth: 220 }}
          options={scopes.map(s => ({ value: s, label: SCOPE_LABELS[s] }))}
          aria-label="Approval scope"
        />
        <Tooltip title="Approve this tool call with the selected scope">
          <Button
            type="primary"
            size="small"
            icon={<CheckCircleOutlined />}
            loading={busy}
            onClick={() => onAnswer('approve', scope, text)}
          >
            Approve
          </Button>
        </Tooltip>
        <Input
          size="small"
          placeholder="Reason (optional, shown to the model on reject)"
          value={text}
          onChange={e => setText(e.target.value)}
          disabled={busy}
          style={{ flex: 1, minWidth: 180 }}
          onPressEnter={() => onAnswer('reject', 'once', text)}
        />
        <Button
          danger
          size="small"
          icon={<CloseCircleOutlined />}
          disabled={busy}
          onClick={() => onAnswer('reject', 'once', text)}
        >
          Reject
        </Button>
      </div>
    </div>
  );
};

export default ConsentPanel;
