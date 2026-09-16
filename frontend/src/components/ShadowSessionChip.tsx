/**
 * ShadowSessionChip — shows the `ziya shadow` terminals attached to the
 * current conversation, in the chat input box's chip strip.
 *
 * Design: Docs/design/shadow-sessions.md §9.  The user asked for a clear
 * indicator on the input box of any sessions attached to this chat, so it
 * is never a surprise which terminal the model can see (or drive).
 *
 * Data comes from GET /api/shadow/attached — the same helper that builds
 * the model's per-turn context tag — so the chip and the model always
 * agree.  Polled every few seconds; the endpoint is a registry scan plus
 * one short socket round-trip per attached session, so this is cheap.
 */
import React, { useEffect, useState } from 'react';
import { Tooltip } from 'antd';
import { CodeOutlined } from '@ant-design/icons';
import { useTheme } from '../context/ThemeContext';

export interface AttachedShadowSession {
    session_id: string;
    label: string;
    display: string;
    segmentation: string;
    headless: boolean;
    pending_ask: boolean;
    reachable: boolean;
    records: number | null;
    last_activity: string | null;
    control: { restriction: string; policy: string; granted: boolean } | null;
}

const POLL_MS = 5000;

export function useAttachedShadowSessions(conversationId: string | null | undefined): AttachedShadowSession[] {
    const [sessions, setSessions] = useState<AttachedShadowSession[]>([]);
    useEffect(() => {
        if (!conversationId) {
            setSessions([]);
            return;
        }
        let cancelled = false;
        const tick = async () => {
            try {
                const r = await fetch(`/api/shadow/attached?conversation_id=${encodeURIComponent(conversationId)}`);
                if (!r.ok) return;
                const data = await r.json();
                if (!cancelled) setSessions(Array.isArray(data?.sessions) ? data.sessions : []);
            } catch {
                /* backend unreachable: keep the last known state */
            }
        };
        tick();
        const id = window.setInterval(tick, POLL_MS);
        return () => { cancelled = true; window.clearInterval(id); };
    }, [conversationId]);
    return sessions;
}

const controlText = (s: AttachedShadowSession): string | null => {
    if (!s.control) return null;
    if (!s.control.granted) return 'control requested — grant at the terminal';
    return `control: ${s.control.restriction} · ${s.control.policy}`;
};

export const ShadowSessionChip: React.FC<{ session: AttachedShadowSession }> = ({ session }) => {
    const { isDarkMode } = useTheme();
    const control = controlText(session);
    // Amber when this chat can type into the terminal; neutral when it can
    // only read.  Actuation is the thing the user most needs to notice.
    const active = !!session.control?.granted;
    const border = active
        ? (isDarkMode ? '#d89614' : '#faad14')
        : (isDarkMode ? '#333' : '#d9d9d9');
    const tip = [
        `${session.display}${session.headless ? ' (headless)' : ''}`,
        `segmentation: ${session.segmentation}`,
        session.records != null ? `${session.records} journal records` : null,
        session.last_activity ? `last activity ${session.last_activity}` : null,
        control ?? 'observe only — the model can read this terminal, not type into it',
        session.pending_ask ? 'the human at the terminal asked a question' : null,
        session.reachable ? null : 'host not responding',
    ].filter(Boolean).join('\n');

    return (
        <Tooltip title={<span style={{ whiteSpace: 'pre-line' }}>{tip}</span>}>
            <span
                data-testid="shadow-session-chip"
                style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                    padding: '4px 10px',
                    borderRadius: 8,
                    fontSize: 13,
                    background: isDarkMode ? '#1f1f1f' : '#f0f0f0',
                    border: `1px solid ${border}`,
                    userSelect: 'none',
                    maxWidth: 260,
                    whiteSpace: 'nowrap',
                    opacity: session.reachable ? 1 : 0.6,
                }}
            >
                <CodeOutlined />
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', fontWeight: 500 }}>
                    {session.label}
                </span>
                <span style={{ fontSize: 11, opacity: 0.6, flexShrink: 0 }}>{session.session_id}</span>
                {active && <span style={{ fontSize: 11, color: border, flexShrink: 0 }}>● control</span>}
                {session.pending_ask && <span style={{ fontSize: 11, flexShrink: 0 }}>? ask</span>}
            </span>
        </Tooltip>
    );
};

export default ShadowSessionChip;
