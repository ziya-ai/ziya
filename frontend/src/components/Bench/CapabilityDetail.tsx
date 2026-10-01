/**
 * Detail drawer for the selected card (design/capabilities-hub.md, slice 6a).
 *
 * Opens BESIDE the board, not inside a lane, so a server with dozens of
 * tools gets a full-height table: switch · name · own token cost ·
 * description -- what the MCP Servers panel's Tools tab showed.  Also: where
 * the item sits at every layer and who put it there, where it came from,
 * the placement verbs, and a skill's read-only body.
 *
 * A renderer over the bench model: heavy detail arrives through the
 * ``detail`` callback, moves go through ``place`` via the same
 * ``moveOptions`` the board and palette use, tool switches through
 * ``onToolToggle``.  Fetches nothing.  Read-only for every provenance in
 * this phase; "copy path" stands in for opening SKILL.md
 * (backlog-skills-fork-to-project.md).
 */
import React, { useEffect, useMemo, useState } from 'react';
import type { BenchItemDetail, BenchItemView, ReauthorizeResult, ReauthorizeVerdict, ToolDetail } from '../../hooks/useBench';
import type { Layer, PlacementState } from '../../utils/activation';
import { useBenchActions } from './BenchActions';
import { DropIntent, moveOptions, rejectionText } from './laneDnd';
import { VIA_TEXT } from './LensStrip';
import { DeltaChip } from './DeltaChip';
import { KindMark } from './KindMark';

const LAYER_LABEL: Record<Layer, string> = { user: 'me', project: 'project', conversation: 'this chat' };
const LAYERS: Layer[] = ['user', 'project', 'conversation'];
// Lane names are frontend strings; the model speaks always|ondemand|off.
const STATE_LABEL: Record<string, string> = { always: 'on hand', ondemand: 'within reach', off: 'shelved' };

export interface CapabilityDetailProps {
    item: BenchItemView;
    onClose: () => void;
    detail?: (key: string) => Promise<BenchItemDetail>;
    /** Placement writes: verbs at the current lens, and per-layer clear. */
    place?: (key: string, state: PlacementState | null, layer?: Layer) => Promise<void>;
    /** Per-tool switch on an MCP server; resolves to the refreshed detail. */
    onToolToggle?: (server: string, tool: string, enabled: boolean) => Promise<BenchItemDetail>;
    /** Lift the server's rug-pull quarantine; the verdict may be a refusal with an override offered. */
    onReauthorize?: (server: string, force?: boolean) => Promise<ReauthorizeResult>;
    /** Transient: how this item's tokens just moved. */
    delta?: number;
}

const label: React.CSSProperties = { fontSize: 10, letterSpacing: '.08em', opacity: 0.6, margin: '10px 0 4px' };
const cell: React.CSSProperties = { padding: '1px 8px 1px 0', verticalAlign: 'top' };
const muted: React.CSSProperties = { opacity: 0.6 };
const border = '1px solid var(--mockup-border, rgba(127,127,127,0.35))';
const linkBtn: React.CSSProperties = { border: 'none', background: 'none', color: 'inherit', cursor: 'pointer',
                                       textDecoration: 'underline', padding: 0, fontSize: 10 };
const fmt = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}K` : `${n}`);

/** Rows for the tool table; a server reporting names only still lists them. */
export function toolRows(d: BenchItemDetail): ToolDetail[] {
    if (d.tool_details) return d.tool_details;
    return (d.tools ?? []).map(name => ({ name, description: '', tokens: 0, enabled: d.tool_permissions?.[name] !== false }));
}

export const CapabilityDetail: React.FC<CapabilityDetailProps> = ({ item, onClose, detail, place, onToolToggle, onReauthorize, delta }) => {
    const [d, setD] = useState<BenchItemDetail | null>(null);
    const [err, setErr] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [filter, setFilter] = useState('');
    const [refusal, setRefusal] = useState<ReauthorizeVerdict | null>(null);
    const { openMcpServers } = useBenchActions();

    useEffect(() => {
        setD(null); setErr(null); setNotice(null); setFilter(''); setRefusal(null);
        if (!detail) return;
        let live = true;
        detail(item.key).then(
            x => { if (live) setD(x); },
            e => { if (live) setErr(String((e as Error)?.message ?? e)); });
        return () => { live = false; };
    }, [detail, item.key]);

    const rows = useMemo(() => (d ? toolRows(d) : []), [d]);
    const shownRows = useMemo(() => {
        const q = filter.trim().toLowerCase();
        return q ? rows.filter(r => r.name.toLowerCase().includes(q) || r.description.toLowerCase().includes(q)) : rows;
    }, [rows, filter]);
    const enabled = rows.filter(r => r.enabled);
    const enabledTokens = enabled.reduce((a, r) => a + r.tokens, 0);
    const withheldTokens = rows.filter(r => !r.enabled).reduce((a, r) => a + r.tokens, 0);

    const originText = item.origin === 'default'
        ? `${item.kind === 'mcp' ? 'server' : 'skill'} default`
        : item.origin === 'environment' ? 'the environment' : LAYER_LABEL[item.origin as Layer];
    const verbs = place ? moveOptions(item) : [];

    const fail = (e: unknown, fallback: string) => {
        const reason = (e as { reason?: unknown })?.reason;
        setNotice(typeof reason === 'string' ? rejectionText(reason, item.name) : fallback);
    };
    const run = async (intent: DropIntent) => {
        if (!place || intent.kind !== 'place') return;
        setNotice(null);
        try { await place(intent.key, intent.toState); } catch (e) { fail(e, `Could not move ${item.name}`); }
    };
    const clearAt = async (layer: Layer) => {
        if (!place) return;
        setNotice(null);
        try { await place(item.key, null, layer); } catch (e) { fail(e, `Could not clear ${item.name} at ${LAYER_LABEL[layer]}`); }
    };
    const toggle = (t: ToolDetail, on: boolean) =>
        onToolToggle?.(item.name, t.name, on).then(setD, e => setErr(String((e as Error)?.message ?? e)));
    // The gate from the card's health until the detail arrives, then the detail's.
    const gate = (d ? d.gate : (item.health?.gate as { state?: string; reason?: string } | null | undefined)) ?? null;
    const reauthorize = async (force: boolean) => {
        if (!onReauthorize) return;
        setNotice(null);
        try {
            const r = await onReauthorize(item.name, force);
            setD(r.detail);
            if (r.result.success) { setRefusal(null); setNotice(r.result.message ?? `${item.name} re-authorized`); }
            else setRefusal(r.result);
        } catch (e) { setNotice(`Could not re-authorize ${item.name}: ${String((e as Error)?.message ?? e)}`); }
    };

    return (
        <aside data-testid="bench-detail" aria-label={`Details for ${item.name}`}
               style={{ width: 420, flexShrink: 0, minHeight: 0, overflowY: 'auto', fontSize: 11,
                        paddingLeft: 12, borderLeft: border }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <KindMark kind={item.kind} />
                <b style={{ fontSize: 14 }}>{item.name}</b>
                <span style={{ ...muted, fontSize: 11 }}>
                    {item.provenance === 'builtin' ? 'shipped with Ziya' : `${item.provenance} install`}
                </span>
                <button type="button" data-testid="detail-close" aria-label="Close details" onClick={onClose}
                        style={{ ...linkBtn, marginLeft: 'auto', textDecoration: 'none', fontSize: 14 }}>✕</button>
            </div>
            {d?.description && <div style={{ opacity: 0.8, marginTop: 6 }}>{d.description}</div>}

            <div style={label}>WHERE IT SITS</div>
            <table style={{ borderCollapse: 'collapse' }}><tbody>
                <tr><td style={{ ...cell, ...muted }}>{item.kind === 'mcp' ? 'server says' : 'skill says'}</td>
                    <td style={cell}>{STATE_LABEL[item.default]}</td><td /></tr>
                {LAYERS.map(l => {
                    const s = item.placements[l];
                    const via = item.meta?.[l]?.via;
                    return (
                        <tr key={l} data-testid={`sits-${l}`}>
                            <td style={{ ...cell, ...muted }}>{LAYER_LABEL[l]}</td>
                            <td style={cell}>{s ? <b>{STATE_LABEL[s]}</b> : '—'}</td>
                            <td style={{ ...cell, ...muted }}>
                                {s && via ? (VIA_TEXT[via] ?? via) : ''}
                                {s && place && (
                                    <button type="button" aria-label={`Clear ${item.name} at ${LAYER_LABEL[l]}`}
                                            onClick={() => void clearAt(l)} style={{ ...linkBtn, marginLeft: 6 }}>clear</button>
                                )}
                            </td>
                        </tr>
                    );
                })}
            </tbody></table>
            <div data-testid="sits-effective" style={{ marginTop: 3 }}>
                Effective here: <b>{STATE_LABEL[item.effective]}</b> <span style={muted}>— from {originText}</span>
            </div>
            {verbs.length > 0 && (
                <div data-testid="detail-verbs" style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
                    {verbs.map(o => (
                        <button key={o.label} type="button" disabled={o.disabled} onClick={() => void run(o.intent)}
                                style={{ fontSize: 11, padding: '2px 8px', borderRadius: 5, color: 'inherit', background: 'none',
                                         border, cursor: o.disabled ? 'default' : 'pointer', opacity: o.disabled ? 0.5 : 1 }}>
                            {o.label}
                        </button>
                    ))}
                </div>
            )}
            {notice && <div role="status" style={{ color: '#d46b08', marginTop: 4 }}>{notice}</div>}

            <div style={label}>WHERE IT CAME FROM</div>
            <div>
                {item.provenance === 'builtin' ? 'shipped with Ziya' : `${item.provenance} install`}
                {item.tokens > 0 && <span style={muted}> · {fmt(item.tokens)} tokens{item.kind === 'skill' ? ' when loaded' : ''}</span>}
            </div>
            {d?.path && (
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    <code data-testid="skill-path" style={{ fontSize: 10, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.path}</code>
                    <button type="button" onClick={() => void navigator.clipboard?.writeText(d.path!)}
                            style={{ fontSize: 10, border, borderRadius: 4, background: 'none', color: 'inherit', cursor: 'pointer', padding: '0 5px' }}>
                        copy path
                    </button>
                </div>
            )}

            {gate && (
                <section data-testid="detail-gate" style={{ marginTop: 10, padding: 8, borderRadius: 6, border: '1px solid #d4380d',
                                                           background: 'rgba(212,56,13,0.06)' }}>
                    <div style={{ ...label, color: '#d4380d', opacity: 1, margin: '0 0 4px' }}>⛔ DEFINITIONS CHANGED · NOT SENT</div>
                    <div>{gate.reason}</div>
                    <div style={{ ...muted, marginTop: 2 }}>
                        The card keeps its place; none of its tools reach the model until you accept the new definitions.
                    </div>
                    {refusal && (
                        <div data-testid="detail-gate-refusal" style={{ marginTop: 6 }}>
                            <div style={{ color: '#d4380d' }}>{refusal.message ?? 'Re-authorization refused'}</div>
                            {Object.entries(refusal.blocking_tools ?? {}).map(([tool, warns]) => (
                                <div key={tool} style={{ fontFamily: 'monospace', fontSize: 10 }}><b>{tool}</b>: {warns.join('; ')}</div>
                            ))}
                            {Object.keys(refusal.advisory_tools ?? {}).length > 0 && (
                                <div style={{ ...muted, fontSize: 10 }}>advisory only: {Object.keys(refusal.advisory_tools!).join(', ')}</div>
                            )}
                            <div style={{ ...muted, fontSize: 10, marginTop: 2 }}>Override only if you have read these descriptions and trust this server.</div>
                        </div>
                    )}
                    {onReauthorize && (
                        <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                            {!refusal?.can_force ? (
                                <button type="button" data-testid="detail-reauthorize" onClick={() => void reauthorize(false)}
                                        style={{ fontSize: 11, padding: '2px 10px', borderRadius: 5, cursor: 'pointer', color: '#fff',
                                                 background: '#d4380d', border: '1px solid #d4380d' }}>Re-authorize</button>
                            ) : (
                                <button type="button" data-testid="detail-reauthorize-force" onClick={() => void reauthorize(true)}
                                        style={{ fontSize: 11, padding: '2px 10px', borderRadius: 5, cursor: 'pointer', color: '#d4380d',
                                                 background: 'none', border: '1px solid #d4380d' }}>Re-authorize anyway</button>
                            )}
                        </div>
                    )}
                </section>
            )}

            {detail && (
                <>
                    <div style={{ ...label, display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span>{item.kind === 'mcp' ? `TOOLS${rows.length ? ` · ${rows.length}` : ''}` : 'PROMPT · READ-ONLY'}</span>
                        {item.kind === 'mcp' && openMcpServers && (
                            <button type="button" data-testid="mcp-server-details" onClick={() => openMcpServers(item.name)}
                                    style={{ ...linkBtn, marginLeft: 'auto', letterSpacing: 0, opacity: 1 }}>
                                logs, permissions, reauthorize ▸
                            </button>
                        )}
                    </div>
                    {err ? <div role="alert" style={{ color: '#d46b08' }}>Could not load details: {err}</div>
                     : !d ? <div data-testid="detail-loading" style={muted}>Loading…</div>
                     : item.kind === 'mcp' ? (
                        rows.length === 0
                            ? <div data-testid="detail-tools" style={muted}>{d.connected ? 'no tools reported' : 'not connected'}</div>
                            : (
                            <>
                                <input data-testid="detail-tool-filter" value={filter} onChange={e => setFilter(e.target.value)}
                                       placeholder="filter tools…" aria-label="Filter tools"
                                       style={{ fontSize: 11, padding: '3px 8px', borderRadius: 5, border, background: 'none',
                                                color: 'inherit', marginBottom: 4, width: '100%', boxSizing: 'border-box' }} />
                                <div data-testid="detail-tools" style={{ border, borderRadius: 6 }}>
                                    {shownRows.map((t, idx) => (
                                        <div key={t.name} data-testid={`detail-tool-${t.name}`}
                                             style={{ display: 'grid', gridTemplateColumns: onToolToggle ? '18px 1fr 60px' : '1fr 60px',
                                                      gap: 6, padding: '5px 8px', borderTop: idx ? border : 'none',
                                                      opacity: t.enabled ? 1 : 0.45 }}>
                                            {onToolToggle && (
                                                <input type="checkbox" checked={t.enabled} aria-label={`${t.name} enabled`}
                                                       onChange={e => void toggle(t, e.target.checked)} style={{ margin: '2px 0 0' }} />
                                            )}
                                            <div style={{ minWidth: 0 }}>
                                                <b style={{ textDecoration: t.enabled ? 'none' : 'line-through' }}>{t.name}</b>
                                                {t.description && <div style={{ opacity: 0.7, whiteSpace: 'pre-wrap' }}>{t.description}</div>}
                                            </div>
                                            <span style={{ textAlign: 'right', opacity: 0.7 }}>{t.tokens > 0 ? `${t.tokens.toLocaleString()}t` : ''}</span>
                                        </div>
                                    ))}
                                    {shownRows.length === 0 && <div style={{ ...muted, padding: 6 }}>no tool matches</div>}
                                </div>
                                <div data-testid="detail-tools-summary"
                                     style={{ marginTop: 4, padding: '5px 8px', borderRadius: 6, display: 'flex', alignItems: 'center', gap: 8,
                                              background: withheldTokens ? 'rgba(13,148,136,0.08)' : 'transparent' }}>
                                    <span><b>{enabled.length}</b> of {rows.length} enabled · <b>{enabledTokens.toLocaleString()}t</b> on hand</span>
                                    <DeltaChip delta={delta} testId="detail-delta" />
                                    {withheldTokens > 0 && <span style={{ ...muted, marginLeft: 'auto' }}>{withheldTokens.toLocaleString()}t withheld</span>}
                                </div>
                            </>
                        )
                    ) : (
                        <pre data-testid="skill-prompt"
                             style={{ margin: 0, fontSize: 10, whiteSpace: 'pre-wrap', maxHeight: 320, overflow: 'auto',
                                      padding: 6, borderRadius: 5, border }}>
                            {d.prompt || '(no prompt body)'}
                        </pre>
                    )}
                </>
            )}
        </aside>
    );
};
