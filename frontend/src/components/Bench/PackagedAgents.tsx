/**
 * Agents installed with packages, listed by package under the board
 * (design/agent-overlay.md).  A renderer: the list and the import record
 * arrive through the model, and "use in this chat" is reported up.
 *
 * Collapsed to one line by default; it renders nothing when no agent is
 * installed.
 */
import React, { useState } from 'react';
import type { AgentImport, AgentImportRow, AgentSummary } from '../../hooks/useAgent';
import { agentsByPackage } from '../AgentBar';
import { AgentImportRows } from '../AgentImportRows';

export interface PackagedAgentsModel {
    agents: AgentSummary[];
    /** The agent on in the open conversation, if any. */
    active: string | null;
    /** False when there is no saved conversation to turn an agent on in. */
    canUse: boolean;
    onUse: (name: string) => void;
    importOf: (name: string) => Promise<AgentImport>;
}

const border = '1px solid var(--mockup-border, rgba(127,127,127,0.35))';
const linkBtn: React.CSSProperties = {
    border: 'none', background: 'none', color: 'inherit', cursor: 'pointer',
    textDecoration: 'underline', padding: 0, fontSize: 11,
};

export const PackagedAgents: React.FC<{ model: PackagedAgentsModel }> = ({ model }) => {
    const { agents, active, canUse, onUse, importOf } = model;
    const [open, setOpen] = useState(false);
    const [inspecting, setInspecting] = useState<string | null>(null);
    const [rows, setRows] = useState<AgentImportRow[] | null>(null);
    if (agents.length === 0) return null;

    const inspect = (name: string) => {
        if (inspecting === name) { setInspecting(null); return; }
        setInspecting(name);
        setRows(null);
        importOf(name).then(
            imp => setRows(imp.rows),
            () => setRows([]));
    };

    return (
        <section data-testid="bench-agents" style={{ border, borderRadius: 6, padding: '4px 10px', fontSize: 11,
                                                    maxHeight: open ? 260 : undefined, overflowY: open ? 'auto' : undefined }}>
            <button type="button" data-testid="bench-agents-toggle" aria-expanded={open} onClick={() => setOpen(o => !o)}
                    style={{ border: 'none', background: 'none', color: 'inherit', cursor: 'pointer', padding: 0, fontSize: 11 }}>
                {open ? '▾' : '▸'} <b>Agents</b>
                <span style={{ opacity: 0.6 }}> · {agents.length} installed{active ? ` · ${active} is on in this chat` : ''}</span>
            </button>
            {open && agentsByPackage(agents).map(({ pkg, agents: list }) => (
                <div key={pkg} style={{ marginTop: 4 }}>
                    <div style={{ fontSize: 10, letterSpacing: '.08em', opacity: 0.6 }}>{pkg}</div>
                    {list.map(a => {
                        const usable = a.runnable && canUse && a.name !== active;
                        const why = !a.runnable ? a.blockers.join('; ')
                            : !canUse ? 'Open a conversation and send a message first'
                            : a.name === active ? 'Already on in this chat' : `Turn ${a.name} on for the open conversation`;
                        return (
                            <div key={a.name} data-testid={`bench-agent-${a.name}`} style={{ padding: '2px 0' }}>
                                <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                                    <b>{a.name}</b>
                                    <span style={{ opacity: 0.6, flex: 1, minWidth: 0, overflow: 'hidden',
                                                   textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                        {a.runnable ? a.description : `not available yet: ${a.blockers.join('; ')}`}
                                    </span>
                                    <button type="button" data-testid={`bench-agent-inspect-${a.name}`}
                                            aria-expanded={inspecting === a.name} onClick={() => inspect(a.name)} style={linkBtn}>
                                        inspect
                                    </button>
                                    <button type="button" data-testid={`bench-agent-use-${a.name}`} disabled={!usable} title={why}
                                            onClick={() => onUse(a.name)}
                                            style={{ ...linkBtn, opacity: usable ? 1 : 0.5, cursor: usable ? 'pointer' : 'default',
                                                     textDecoration: usable ? 'underline' : 'none' }}>
                                        {a.name === active ? 'on in this chat' : 'use in this chat'}
                                    </button>
                                </div>
                                {inspecting === a.name && (
                                    <div data-testid={`bench-agent-import-${a.name}`} style={{ margin: '2px 0 4px 12px' }}>
                                        {rows === null && <span style={{ opacity: 0.6 }}>Loading…</span>}
                                        {rows !== null && rows.length === 0 && <span style={{ opacity: 0.6 }}>The import record is not available.</span>}
                                        <AgentImportRows rows={rows ?? []} />
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            ))}
        </section>
    );
};
