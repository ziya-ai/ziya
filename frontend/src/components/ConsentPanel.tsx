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

import React, { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Input, Select, Tooltip } from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined } from '@ant-design/icons';
import {
  CONSENT_ANSWERED_EVENT, CONSENT_OPENED_EVENT, SCOPE_LABELS,
  answerConsent, applyAnswered, applyOpened, applyServerList, describe,
  fetchOpenConsents, openFor,
} from '../utils/consentStore';
import type { ConsentRecord, ConsentScope, OpenConsents } from '../utils/consentStore';
import { ConsentPreflight, originalText } from './ConsentPreflight';
import { lazyWithRetry } from '../utils/lazyWithRetry';
import './TaskCard/task-card-inline-tile.css';

// The chat's diff renderer without its Apply button.  Lazy because
// MarkdownRenderer is its own chunk (StreamedContent loads it the same way).
const ReadOnlyDiffView = lazyWithRetry(() =>
  import('./MarkdownRenderer').then(m => ({ default: m.ReadOnlyDiffView })));

// write_grants.write_preview appends this when it caps a preview.
const CAP_MARKER = /\n?… \((\d+) more characters\)\n?$/;

/**
 * Shape a preflight diff for the chat diff renderer: drop the display-cap
 * marker (not a diff line), and add the ``diff --git`` header difflib does
 * not emit.  ``truncated`` is the character count the cap cut, 0 if none.
 */
export function asRenderableDiff(raw: string, fallbackPath = 'file'): { diff: string; truncated: number } {
  const m = CAP_MARKER.exec(raw);
  let diff = m ? raw.slice(0, m.index) + '\n' : raw;
  if (!/^diff --git /.test(diff)) {
    const path = /^\+\+\+ b\/(.+)$/m.exec(diff)?.[1]
      ?? /^--- a\/(.+)$/m.exec(diff)?.[1]
      ?? fallbackPath;
    diff = `diff --git a/${path} b/${path}\n${diff}`;
  }
  return { diff, truncated: m ? Number(m[1]) : 0 };
}

// A chunk that fails to load must not take the approval panel down with it:
// fall back to the plain text the panel showed before.
class DiffFallbackBoundary extends React.Component<
  { fallback: React.ReactNode; children: React.ReactNode }, { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

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
    edits: Record<string, string> = {},
  ) => {
    setBusy(rec.request_id);
    const res = await answerConsent(rec.request_id, decision, scope, text, edits);
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
          onAnswer={(d, s, t, e) => answer(rec, d, s, t, e)}
        />
      ))}
    </div>
  );
};

const ConsentRequest: React.FC<{
  rec: ConsentRecord;
  busy: boolean;
  onAnswer: (decision: 'approve' | 'reject', scope: ConsentScope, text: string,
             edits?: Record<string, string>) => void;
}> = ({ rec, busy, onAnswer }) => {
  const [scope, setScope] = useState<ConsentScope>('once');
  const [text, setText] = useState('');
  const [edits, setEdits] = useState<Record<string, string>>({});
  const p = rec.payload;
  const scopes: ConsentScope[] = rec.scopes ?? ['once', 'conversation', 'session', 'always'];
  const preflight = p.kind === 'tool_call' ? p.preflight : null;
  const args = p.kind === 'tool_call' ? p.args : null;
  // design/scope-consent.md: paths outside the write policy, and the
  // proposed change when the tool supplies one (file_write).
  const outside = p.kind === 'tool_call' ? (p.outside_policy ?? []) : [];
  const rawDiff = preflight ? preflight['diff'] : undefined;
  const diffText = typeof rawDiff === 'string' ? rawDiff : null;
  const detail = preflight && diffText !== null
    ? Object.fromEntries(Object.entries(preflight).filter(([k]) => k !== 'diff'))
    : (preflight ?? args);
  const renderable = useMemo(
    () => (diffText ? asRenderableDiff(diffText, String(preflight?.['path'] ?? 'file')) : null),
    [diffText, preflight],
  );
  const tool = p.kind === 'tool_call' ? p.tool : '';
  const op = p.kind === 'tool_call' ? p.op : undefined;
  // Only a preflight is editable: its value is the full text, whereas the
  // raw args shown as a fallback are truncated for display.
  const editable = p.kind === 'tool_call' && preflight ? (p.editable ?? []) : [];
  const onEdit = useCallback((k: string, v: string | undefined) => {
    setEdits(prev => {
      const next = { ...prev };
      if (v === undefined) delete next[k]; else next[k] = v;
      return next;
    });
  }, []);
  // Emptying a value the model supplied (a commit message) cannot succeed.
  const blanked = Object.entries(edits).some(([k, v]) =>
    !v.trim() && detail && originalText(detail as Record<string, unknown>, k).trim() !== '');

  return (
    <div className="tc-ask-panel consent-panel" role="group" aria-label="Tool call — awaiting your approval">
      <div className="consent-panel__head">
        <span aria-hidden className="tc-ask-panel__icon">?</span>
        <span className="tc-ask-panel__question">{describe(rec)}</span>
      </div>

      {outside.length > 0 && (
        <div
          className="tc-ask-panel__detail"
          role="alert"
          aria-label="Outside the write policy"
          style={{ borderLeft: '3px solid #d48806', paddingLeft: 8, fontSize: 12 }}
        >
          <strong>Outside the write policy.</strong>{' '}
          Approving once allows writes to exactly these paths, for this call only.
          A conversation or session scope also remembers these exact files for this
          tool, so later writes to them won't ask again (or show a diff).
          <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
            {outside.map(o => (
              <li key={o.path}><code>{o.path}</code></li>
            ))}
          </ul>
        </div>
      )}

      {diffText && renderable && (
        <div className="consent-panel__diff" aria-label="Proposed change" style={{ maxHeight: 420, overflow: 'auto' }}>
          <DiffFallbackBoundary fallback={<pre className="tc-ask-panel__detail" style={{ fontSize: 12 }}>{diffText}</pre>}>
            <Suspense fallback={<pre className="tc-ask-panel__detail" style={{ fontSize: 12 }}>{diffText}</pre>}>
              <ReadOnlyDiffView diff={renderable.diff} elementId={`consent-${rec.request_id}`} />
            </Suspense>
          </DiffFallbackBoundary>
          {renderable.truncated > 0 && (
            <div style={{ fontSize: 12, opacity: 0.7, padding: '4px 8px' }}>
              … {renderable.truncated} more characters not shown
            </div>
          )}
        </div>
      )}

      {/* You approve what WILL happen: preflight first, raw args as fallback. */}
      {detail && Object.keys(detail).length > 0 && (
        <ConsentPreflight
          tool={tool}
          op={op}
          detail={detail as Record<string, unknown>}
          isArgs={!preflight}
          editable={editable}
          edits={edits}
          onEdit={editable.length ? onEdit : undefined}
        />
      )}

      <div className="tc-ask-panel__free consent-actions">
        <div className="consent-actions__approve">
          <Tooltip title={blanked ? 'An edited value is empty' : 'Approve this tool call with the selected scope'}>
            <Button
              type="primary"
              size="small"
              icon={<CheckCircleOutlined />}
              loading={busy}
              disabled={blanked}
              onClick={() => onAnswer('approve', scope, text, edits)}
            >
              Approve
            </Button>
          </Tooltip>
          <Select<ConsentScope>
            size="small"
            value={scope}
            onChange={setScope}
            disabled={busy}
            style={{ minWidth: 150 }}
            options={scopes.map(s => ({ value: s, label: SCOPE_LABELS[s] }))}
            aria-label="Approval scope"
          />
        </div>
        <div className="consent-actions__reject">
          <Input
            size="small"
            placeholder="Reason for rejecting (optional, shown to the model)"
            value={text}
            onChange={e => setText(e.target.value)}
            disabled={busy}
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
    </div>
  );
};

export default ConsentPanel;
