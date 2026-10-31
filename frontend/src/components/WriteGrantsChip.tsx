/**
 * WriteGrantsChip — the standing consent grants in effect for this chat
 * (design/scope-consent.md D4).
 *
 * A grant is what an approval at a wider rung or duration leaves behind:
 * "file_write may write anything under frontend/src/ for the rest of this
 * conversation".  Those are invisible once the panel closes, so this chip
 * sits in the input-box row whenever any stands, and opens a table with
 * tool, paths, duration, when it was granted, how often it was used, and a
 * Revoke button.  Revoking makes the next covered call ask again.
 *
 * Source: GET /api/consent/grants?conversation_id= (the conversation's own
 * grants plus the project's), refreshed when a consent is answered and on
 * a slow poll so a revocation from another window shows up.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { Button, Popover, Tooltip } from 'antd';
import { SafetyCertificateOutlined } from '@ant-design/icons';
import { CONSENT_ANSWERED_EVENT } from '../utils/consentStore';
import { useTheme } from '../context/ThemeContext';

/** Wire shape of app/utils/consent_grants.py ``Grant.public``. */
export interface WriteGrant {
  id: string;
  tool: string;
  level: 'conversation' | 'project';
  root: string;
  conversation_id?: string | null;
  op?: string | null;
  path?: string | null;
  kind?: 'file' | 'dir' | 'project' | null;
  rule: string;
  granted_at: number;   // seconds since the epoch
  used: number;
  path_display?: string;
}

/** What a grant covers, as the chip and table show it. */
export function grantTarget(g: WriteGrant): string {
  if (g.kind === 'project') return 'whole project';
  if (g.kind === 'dir') return `${g.path_display ?? g.path ?? ''}/**`;
  if (g.kind === 'file') return g.path_display ?? g.path ?? '';
  return g.op && g.op !== '*' ? `${g.tool} ${g.op}` : g.tool;
}

/** "3 write grants · frontend/src/** +2" */
export function chipText(grants: WriteGrant[]): string {
  const n = grants.length;
  if (n === 0) return '';
  const head = `${n} write grant${n === 1 ? '' : 's'} · ${grantTarget(grants[0])}`;
  return n > 1 ? `${head} +${n - 1}` : head;
}

export function agoText(grantedAtSeconds: number, nowMs = Date.now()): string {
  const s = Math.max(0, Math.round(nowMs / 1000 - grantedAtSeconds));
  if (s < 60) return 'just now';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.round(h / 24)} days ago`;
}

export async function fetchGrants(conversationId?: string): Promise<WriteGrant[]> {
  try {
    const q = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : '';
    const res = await fetch(`/api/consent/grants${q}`);
    if (!res.ok) return [];
    const body = await res.json();
    return Array.isArray(body) ? (body as WriteGrant[]) : [];
  } catch {
    return [];
  }
}

export async function revokeGrant(id: string): Promise<boolean> {
  try {
    const res = await fetch(`/api/consent/grants/${encodeURIComponent(id)}`, { method: 'DELETE' });
    // 404: already gone (another window revoked it) — the row leaves either way.
    return res.ok || res.status === 404;
  } catch {
    return false;
  }
}

const POLL_MS = 30_000;

export function useWriteGrants(conversationId: string): [WriteGrant[], () => void] {
  const [grants, setGrants] = useState<WriteGrant[]>([]);
  const refresh = useCallback(() => {
    if (!conversationId) { setGrants([]); return; }
    fetchGrants(conversationId).then(setGrants);
  }, [conversationId]);
  useEffect(() => {
    refresh();
    document.addEventListener(CONSENT_ANSWERED_EVENT, refresh);
    const id = window.setInterval(refresh, POLL_MS);
    return () => {
      document.removeEventListener(CONSENT_ANSWERED_EVENT, refresh);
      window.clearInterval(id);
    };
  }, [refresh]);
  return [grants, refresh];
}

const LEVEL_LABEL: Record<WriteGrant['level'], string> = {
  conversation: 'this conversation',
  project: 'this project (all chats)',
};

const GrantsTable: React.FC<{
  grants: WriteGrant[];
  busy: string | null;
  onRevoke: (g: WriteGrant) => void;
  isDarkMode: boolean;
}> = ({ grants, busy, onRevoke, isDarkMode }) => {
  const border = isDarkMode ? '#333' : '#e8e8e8';
  const th: React.CSSProperties = { textAlign: 'left', padding: '4px 8px', borderBottom: `1px solid ${border}`, opacity: 0.7, fontWeight: 500 };
  const td: React.CSSProperties = { padding: '5px 8px', verticalAlign: 'top' };
  return (
    <div style={{ maxWidth: 640, fontSize: 12.5 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
        <strong>Write grants in effect</strong>
        <span style={{ opacity: 0.7 }}>beyond the project write policy</span>
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse' }} aria-label="Write grants">
        <thead>
          <tr><th style={th}>Tool</th><th style={th}>Covers</th><th style={th}>Duration</th><th style={th}>Granted</th><th style={th} /></tr>
        </thead>
        <tbody>
          {grants.map(g => (
            <tr
              key={g.id}
              data-testid="write-grant-row"
              title={g.rule}
              style={{ background: g.level === 'project' ? 'rgba(212,136,6,.10)' : undefined }}
            >
              <td style={td}><code>{g.tool}</code></td>
              <td style={td}><code>{grantTarget(g)}</code></td>
              <td style={td}>{g.level === 'project' ? <b>{LEVEL_LABEL.project}</b> : LEVEL_LABEL.conversation}</td>
              <td style={{ ...td, opacity: 0.7, whiteSpace: 'nowrap' }}>{agoText(g.granted_at)} · used {g.used}×</td>
              <td style={{ ...td, textAlign: 'right' }}>
                <Button size="small" loading={busy === g.id} onClick={() => onRevoke(g)} aria-label={`Revoke ${grantTarget(g)}`}>
                  Revoke
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ marginTop: 8, opacity: 0.7, fontSize: 12 }}>
        Revoking makes the next covered call ask again. Nothing here ever covers <code>.git</code>,
        the install directory, or paths outside the project.
      </div>
    </div>
  );
};

const WriteGrantsChip: React.FC<{ conversationId: string }> = ({ conversationId }) => {
  const { isDarkMode } = useTheme();
  const [grants, refresh] = useWriteGrants(conversationId);
  const [busy, setBusy] = useState<string | null>(null);
  const onRevoke = useCallback(async (g: WriteGrant) => {
    setBusy(g.id);
    const ok = await revokeGrant(g.id);
    setBusy(null);
    if (ok) refresh();
  }, [refresh]);

  if (grants.length === 0) return null;
  const wide = grants.some(g => g.level === 'project' || g.kind === 'project');
  const tone = wide ? '#d48806' : '#2dd4bf';
  return (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
      <Popover
        trigger="click"
        placement="topLeft"
        content={<GrantsTable grants={grants} busy={busy} onRevoke={onRevoke} isDarkMode={isDarkMode} />}
      >
        <Tooltip title="Standing approvals for writes outside the write policy. Click to review or revoke.">
          <span
            role="button"
            tabIndex={0}
            data-testid="write-grants-chip"
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 6, padding: '4px 10px',
              borderRadius: 8, fontSize: 13, cursor: 'pointer', userSelect: 'none',
              background: isDarkMode ? '#1f1f1f' : '#f0f0f0', border: `1px solid ${tone}`,
              maxWidth: 320, whiteSpace: 'nowrap',
            }}
          >
            <SafetyCertificateOutlined style={{ color: tone }} />
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{chipText(grants)}</span>
          </span>
        </Tooltip>
      </Popover>
    </div>
  );
};

export default WriteGrantsChip;
