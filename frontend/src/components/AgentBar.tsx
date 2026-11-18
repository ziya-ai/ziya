/**
 * AgentBar -- the agent running in this conversation (design/agent-overlay.md).
 *
 * Renders nothing unless an agent is active, the user asked for the list
 * (``/agent``), or there is something to say about a request.  While an
 * agent is active it is one chip: click for how the definition was
 * imported, × to turn it off.
 *
 * The panels are drawn inline, inside the composer's own surface, so they
 * carry no background of their own.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useAgent } from '../hooks/useAgent';
import type { AgentImportRow, AgentSummary } from '../hooks/useAgent';
import { AGENT_COMMAND_EVENT } from '../utils/agentCommand';
import type { AgentCommand, AgentCommandDetail } from '../utils/agentCommand';
import { AgentImportRows } from './AgentImportRows';

export interface AgentBarProps {
    projectId?: string | null;
    conversationId?: string | null;
    /** The conversation's own model pin, when it has one: it wins over the
     *  agent's model, and the chip says so. */
    pinnedModel?: string | null;
    /** An agent was turned on or off in ``conversationId`` (the composer
     *  leaves a seam in the transcript). */
    onChange?: (kind: 'started' | 'ended', name: string, conversationId: string | null) => void;
}

const border = '1px solid rgba(127,127,127,0.35)';
const linkBtn: React.CSSProperties = {
    border: 'none', background: 'none', color: 'inherit', cursor: 'pointer',
    textDecoration: 'underline', padding: 0, fontSize: 12,
};

/** Why a turn-on was refused, in the user's terms. */
export function refusalText(e: unknown, name: string): string {
    const err = e as { status?: number; blockers?: string[]; message?: string };
    if (err?.blockers && err.blockers.length > 0) {
        return `${name} cannot be turned on yet: ${err.blockers.join('; ')}.`;
    }
    if (err?.status === 404 && /conversation/i.test(err.message ?? '')) {
        return 'This conversation has not been saved yet. Send a message first, then turn the agent on.';
    }
    if (err?.status === 404) return `No agent named ${name} is installed.`;
    return `Could not turn on ${name}.`;
}

/** Agents by package, packages A-Z. */
export function agentsByPackage(agents: AgentSummary[]): Array<{ pkg: string; agents: AgentSummary[] }> {
    const by = new Map<string, AgentSummary[]>();
    for (const a of agents) by.set(a.package || 'Other', [...(by.get(a.package || 'Other') ?? []), a]);
    return Array.from(by.entries()).sort(([a], [b]) => a.localeCompare(b)).map(([pkg, list]) => ({ pkg, agents: list }));
}

export const AgentBar: React.FC<AgentBarProps> = ({ projectId, conversationId, pinnedModel, onChange }) => {
    const agent = useAgent(projectId, conversationId);
    const [picking, setPicking] = useState(false);
    const [detail, setDetail] = useState(false);
    const [rows, setRows] = useState<AgentImportRow[] | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [confirm, setConfirm] = useState<{ name: string; missing: string[] } | null>(null);

    // Panels belong to the conversation they were opened in.
    useEffect(() => {
        setPicking(false); setDetail(false); setRows(null); setNotice(null); setConfirm(null);
    }, [projectId, conversationId]);

    const enter = useCallback(async (name: string, confirmed = false) => {
        setNotice(null);
        const known = agent.agents.find(a => a.name === name);
        if (known && !known.runnable) {
            setNotice(`${name} cannot be turned on yet: ${known.blockers.join('; ')}.`);
            return;
        }
        if (known && !confirmed) {
            try {
                const imp = await agent.importOf(name);
                const missing = imp.rows.find(r => r.part === 'Servers' && r.outcome === 'unavailable')?.items ?? [];
                if (missing.length > 0) { setConfirm({ name, missing }); return; }
            } catch { /* the check is advisory; the server still decides */ }
        }
        setConfirm(null);
        setPicking(false);
        try {
            await agent.turnOn(name);
            onChange?.('started', name, conversationId ?? null);
        } catch (e) {
            setNotice(refusalText(e, name));
        }
    }, [agent, onChange, conversationId]);

    const leave = useCallback(async () => {
        setNotice(null); setDetail(false); setRows(null);
        const was = agent.active;
        try {
            await agent.turnOff();
            if (was) onChange?.('ended', was, conversationId ?? null);
        } catch { setNotice('Could not turn the agent off.'); }
    }, [agent, onChange, conversationId]);

    const run = useCallback((command: AgentCommand) => {
        if (command.kind === 'pick') { setNotice(null); setConfirm(null); setPicking(true); }
        else if (command.kind === 'off') void leave();
        else void enter(command.name);
    }, [enter, leave]);

    // The composer announces "/agent ..." for a conversation; only that
    // conversation's bar acts on it.
    const runRef = useRef(run);
    runRef.current = run;
    useEffect(() => {
        const onCommand = (e: Event) => {
            const d = (e as CustomEvent<AgentCommandDetail>).detail;
            if (!d || (d.conversationId ?? null) !== (conversationId ?? null)) return;
            runRef.current(d.command);
        };
        window.addEventListener(AGENT_COMMAND_EVENT, onCommand);
        return () => window.removeEventListener(AGENT_COMMAND_EVENT, onCommand);
    }, [conversationId]);

    // The import record loads when the detail panel opens.
    const importOf = agent.importOf;
    useEffect(() => {
        if (!detail || !agent.active) return;
        let live = true;
        importOf(agent.active).then(
            imp => { if (live) setRows(imp.rows); },
            () => { if (live) setRows([]); });
        return () => { live = false; };
    }, [detail, agent.active, importOf]);

    // The model this conversation's turns use while the agent is on: the
    // conversation's own pin when it has one, else the agent's.
    const asked = agent.activeAgent?.model_alias ?? null;
    const modelInUse = pinnedModel ? `${pinnedModel} (your pin)` : asked;

    if (!agent.active && !picking && !notice && !confirm) return null;

    return (
        <div data-testid="agent-bar" style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 8, fontSize: 12 }}>
            {notice && (
                <div role="status" data-testid="agent-notice" style={{ color: '#d46b08', display: 'flex', gap: 8 }}>
                    <span>{notice}</span>
                    <button type="button" onClick={() => setNotice(null)} style={linkBtn}>Dismiss</button>
                </div>
            )}
            {confirm && (
                <div role="alertdialog" data-testid="agent-confirm"
                     style={{ border: '1px solid #d48806', borderRadius: 6, padding: '6px 10px' }}>
                    <b>{confirm.name}</b> needs {confirm.missing.length} server{confirm.missing.length === 1 ? '' : 's'} that
                    {confirm.missing.length === 1 ? ' is' : ' are'} not installed here: {confirm.missing.join(', ')}.
                    <div style={{ marginTop: 4, display: 'flex', gap: 12 }}>
                        <button type="button" data-testid="agent-confirm-yes"
                                onClick={() => void enter(confirm.name, true)} style={linkBtn}>Start anyway</button>
                        <button type="button" data-testid="agent-confirm-no"
                                onClick={() => setConfirm(null)} style={linkBtn}>Cancel</button>
                    </div>
                </div>
            )}
            {picking && (
                <div data-testid="agent-picker" style={{ border, borderRadius: 6, padding: '6px 10px' }}>
                    {agent.agents.length === 0 && <span style={{ opacity: 0.6 }}>No agents are installed.</span>}
                    {agentsByPackage(agent.agents).map(({ pkg, agents }) => (
                        <div key={pkg} style={{ marginBottom: 4 }}>
                            <div style={{ fontSize: 10, letterSpacing: '.08em', opacity: 0.6 }}>{pkg}</div>
                            {agents.map(a => (
                                <div key={a.name} style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                                    <button type="button" data-testid={`agent-pick-${a.name}`} disabled={!a.runnable}
                                            title={a.runnable ? a.description : a.blockers.join('; ')}
                                            onClick={() => void enter(a.name)}
                                            style={{ ...linkBtn, textDecoration: a.runnable ? 'underline' : 'none',
                                                     opacity: a.runnable ? 1 : 0.55, cursor: a.runnable ? 'pointer' : 'default' }}>
                                        <b>{a.name}</b>
                                    </button>
                                    <span style={{ opacity: 0.6, flex: 1, minWidth: 0, overflow: 'hidden',
                                                   textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                        {a.runnable ? a.description : 'not available yet'}
                                    </span>
                                </div>
                            ))}
                        </div>
                    ))}
                    <button type="button" data-testid="agent-picker-close" onClick={() => setPicking(false)} style={linkBtn}>Close</button>
                </div>
            )}
            {agent.active && (
                <div>
                    <span data-testid="agent-chip"
                          style={{ display: 'inline-flex', alignItems: 'center', gap: 6, background: '#134e4a',
                                   color: '#ccfbf1', borderRadius: 12, padding: '2px 4px 2px 10px' }}>
                        <button type="button" data-testid="agent-chip-open" aria-expanded={detail}
                                title="How this agent was imported" onClick={() => setDetail(d => !d)}
                                style={{ border: 'none', background: 'none', color: 'inherit', cursor: 'pointer', padding: 0, fontSize: 12 }}>
                            {agent.active}{modelInUse ? ` · ${modelInUse}` : ''}
                        </button>
                        <button type="button" data-testid="agent-leave" aria-label={`Turn off ${agent.active}`}
                                title="Turn the agent off for this conversation" onClick={() => void leave()}
                                style={{ border: 'none', background: 'none', color: 'inherit', cursor: 'pointer', padding: '0 4px', fontSize: 12 }}>
                            ×
                        </button>
                    </span>
                </div>
            )}
            {agent.active && detail && (
                <div data-testid="agent-detail" style={{ border, borderRadius: 6, padding: '6px 10px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                        <b>{agent.active}</b>
                        <span style={{ opacity: 0.6 }}>{agent.activeAgent?.package ?? 'no longer installed'}</span>
                    </div>
                    {rows === null && <div style={{ opacity: 0.6 }}>Loading…</div>}
                    {rows !== null && rows.length === 0 && <div style={{ opacity: 0.6 }}>The import record is not available.</div>}
                    {pinnedModel && asked && pinnedModel !== asked && (
                        <div data-testid="agent-model-note" style={{ opacity: 0.8, padding: '2px 0' }}>
                            The agent asks for {asked}; this conversation is pinned to {pinnedModel}.
                        </div>
                    )}
                    <AgentImportRows rows={rows ?? []} />
                    <div style={{ marginTop: 6 }}>
                        <button type="button" data-testid="agent-detail-leave" onClick={() => void leave()} style={linkBtn}>Leave agent</button>
                    </div>
                </div>
            )}
        </div>
    );
};
