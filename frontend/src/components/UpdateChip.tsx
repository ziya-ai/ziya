/**
 * UpdateChip — the header's "Update ready" indicator (option A+C of the
 * 2026-11-11 mockups).  A quiet chip opens a popover that restarts now when
 * nothing is running, or queues the restart behind what is; an opt-in
 * switch restarts automatically whenever nothing is running.  While the
 * server restarts, a card replaces the "server unreachable" banner.
 * State and polling: utils/updateStatus.ts; server: app/utils/update_watch.py.
 */
import React, { useEffect, useState } from 'react';
import { Button, Popover, Switch } from 'antd';
import { useTheme } from '../context/ThemeContext';
import { useConversationList } from '../context/ConversationListContext';
import {
    BusyItem, UpdateStatus, cancelRestart, clearRestartedNotice, dismissUpdate, isDismissed,
    requestRestart, setAutoRestart, startUpdatePolling, updateHooks, useUpdateState,
} from '../utils/updateStatus';

type Tone = 'teal' | 'amber';
const TONES = {
    teal: {
        light: { bg: '#e6f4f1', fg: '#0f5f58', border: '#b9ded7', dot: '#0f766e' },
        dark: { bg: 'rgba(45,212,191,0.12)', fg: '#5eead4', border: 'rgba(45,212,191,0.35)', dot: '#2dd4bf' },
    },
    amber: {
        light: { bg: '#fff7e6', fg: '#8a5a00', border: '#f1d9a6', dot: '#d9a441' },
        dark: { bg: 'rgba(217,164,65,0.14)', fg: '#f3c97a', border: 'rgba(217,164,65,0.4)', dot: '#d9a441' },
    },
};

interface ChipProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
    tone: Tone;
    dark: boolean;
    ring?: boolean;
}

const Chip = React.forwardRef<HTMLButtonElement, ChipProps>(({ tone, dark, ring, children, style, ...rest }, ref) => {
    const c = TONES[tone][dark ? 'dark' : 'light'];
    return (
        <button ref={ref} type="button" data-testid="update-chip" {...rest}
            style={{
                display: 'inline-flex', alignItems: 'center', gap: 6, padding: '2px 10px', borderRadius: 999,
                fontSize: 12, lineHeight: '20px', whiteSpace: 'nowrap', cursor: 'pointer',
                background: c.bg, color: c.fg, border: `1px solid ${c.border}`, ...style,
            }}>
            <span aria-hidden style={ring
                ? { width: 10, height: 10, borderRadius: '50%', border: `2px solid ${c.dot}`, borderTopColor: 'transparent', boxSizing: 'border-box' }
                : { width: 6, height: 6, borderRadius: '50%', background: c.dot }} />
            {children}
        </button>
    );
});
Chip.displayName = 'UpdateChipButton';

const BusyList: React.FC<{ busy: BusyItem[] }> = ({ busy }) => {
    const { conversations } = useConversationList();
    const label = (b: BusyItem) => {
        if (b.kind === 'chat') {
            const title = conversations.find(c => c.id === b.id)?.title;
            return <>A response in <b>{title || 'a conversation'}</b></>;
        }
        return b.kind === 'task_run' ? <>A Task Card run</> : <>Delegated sub-tasks</>;
    };
    return (
        <ul style={{ margin: '0 0 12px', paddingLeft: 18, fontSize: 12 }}>
            {busy.map(b => <li key={`${b.kind}:${b.id}`}>{label(b)}</li>)}
        </ul>
    );
};

const muted: React.CSSProperties = { opacity: 0.75 };

export const UpdatePanel: React.FC<{ status: UpdateStatus; onDone: () => void }> = ({ status, onDone }) => {
    const u = status.update!;
    const busy = status.busy;
    const primary = busy.length ? 'Restart when these finish' : (u.ready ? 'Restart now' : 'Restart when ready');
    return (
        <div data-testid="update-panel" style={{ width: 300, fontSize: 13 }}>
            <div style={{ fontWeight: 600, marginBottom: 2 }}>Ziya {u.version} is installed</div>
            {busy.length === 0 ? (
                <div style={{ ...muted, marginBottom: 10 }}>
                    You're running {status.running_version ?? 'an older version'}. Restarting takes a few
                    seconds; this tab reconnects and your conversations stay where they are.
                </div>
            ) : (
                <>
                    <div style={{ ...muted, marginBottom: 6 }}>Restarting now would stop:</div>
                    <BusyList busy={busy} />
                </>
            )}
            {!u.ready && !status.prepare_failed && (
                <div style={{ ...muted, fontSize: 12, marginBottom: 10 }}>Getting {u.version} ready…</div>
            )}
            {status.prepare_failed && (
                <div style={{ fontSize: 12, marginBottom: 10 }}>
                    Could not set up {u.version}. Stop ziya and start it again to use it.
                </div>
            )}
            {!status.can_restart && (
                <div style={{ fontSize: 12, marginBottom: 10 }}>
                    This server cannot restart itself. Stop ziya and start it again to use {u.version}.
                </div>
            )}
            {u.notes_url && (
                <div style={{ fontSize: 12, marginBottom: 12 }}>
                    <a href={u.notes_url} target="_blank" rel="noreferrer">What changed →</a>
                </div>
            )}
            <div style={{ display: 'flex', gap: 8 }}>
                <Button type="primary" size="small" disabled={!status.can_restart || status.prepare_failed}
                    onClick={() => { void requestRestart(); onDone(); }}>{primary}</Button>
                <Button size="small" onClick={() => { dismissUpdate(u.version); onDone(); }}>Later</Button>
            </div>
            <div style={{
                borderTop: '1px solid rgba(128,128,128,0.25)', marginTop: 12, paddingTop: 10,
                display: 'flex', alignItems: 'center', gap: 10,
            }}>
                <Switch size="small" checked={status.auto_restart} disabled={!status.can_restart}
                    aria-label="Restart automatically when nothing is running"
                    onChange={on => { void setAutoRestart(on); }} />
                <span style={{ fontSize: 12 }}>Restart automatically when nothing is running</span>
            </div>
        </div>
    );
};

const RestartingCard: React.FC<{ version: string | null; dark: boolean }> = ({ version, dark }) => (
    <div role="status" data-testid="update-restarting" style={{
        position: 'fixed', top: 64, left: '50%', transform: 'translateX(-50%)', zIndex: 10001,
        padding: '14px 22px', borderRadius: 10, textAlign: 'center',
        background: dark ? '#1f2427' : '#ffffff', color: dark ? '#e6e6e6' : '#1f2328',
        border: `1px solid ${dark ? 'rgba(45,212,191,0.35)' : '#b9ded7'}`, boxShadow: '0 6px 20px rgba(0,0,0,0.12)',
    }}>
        <div style={{ fontWeight: 600 }}>Restarting into {version ?? 'the new version'}…</div>
        <div style={{ ...muted, fontSize: 12 }}>This tab will reconnect by itself.</div>
    </div>
);

export const UpdateChip: React.FC = () => {
    const { isDarkMode } = useTheme();
    const st = useUpdateState();
    const [open, setOpen] = useState(false);
    useEffect(() => startUpdatePolling(), []);

    if (st.restarting) {
        return (
            <>
                <Chip tone="amber" dark={isDarkMode} ring>Restarting…</Chip>
                <RestartingCard version={st.restartingInto} dark={isDarkMode} />
            </>
        );
    }
    if (st.restartedInto) {
        return (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
                <Chip tone="teal" dark={isDarkMode} onClick={() => updateHooks.reload()} title="Reload to use the new version">
                    Updated to {st.restartedInto} · Reload
                </Chip>
                <Button type="text" size="small" aria-label="Dismiss" onClick={clearRestartedNotice}>×</Button>
            </span>
        );
    }
    const s = st.status;
    if (!s || !s.update) return null;
    if (s.restart_requested) {
        const n = s.busy.length;
        return (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
                <Chip tone="amber" dark={isDarkMode} ring title={`Restarting into ${s.update.version}`}>
                    {n ? `Restarting after ${n} ${n === 1 ? 'task' : 'tasks'}` : 'Restarting…'}
                </Chip>
                <Button type="link" size="small" onClick={() => { void cancelRestart(); }}>Cancel</Button>
            </span>
        );
    }
    if (isDismissed(s.update.version)) return null;
    return (
        <Popover open={open} onOpenChange={setOpen} trigger="click" placement="bottomRight"
            content={<UpdatePanel status={s} onDone={() => setOpen(false)} />}>
            <Chip tone="teal" dark={isDarkMode}>Update ready</Chip>
        </Popover>
    );
};
