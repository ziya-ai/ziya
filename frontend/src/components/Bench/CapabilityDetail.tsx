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
 * ``onToolToggle``, skill edits through ``onUpdateSkill`` / ``onDeleteSkill``
 * (offered only when the detail says the skill is editable: custom skills;
 * a SKILL.md is edited on disk, "copy path" stands in).  Fetches nothing.
 */
import React, { useEffect, useMemo, useState } from 'react';
import type { BenchItemDetail, BenchItemView, ReauthorizeResult, ReauthorizeVerdict, ToolDetail } from '../../hooks/useBench';
import type { Layer, PlacementState } from '../../utils/activation';
import { useBenchActions } from './BenchActions';
import { DropIntent, moveOptions, rejectionText } from './laneDnd';
import { VIA_TEXT } from './LensStrip';
import { DeltaChip } from './DeltaChip';
import { KindMark } from './KindMark';
import { SkillDraft, SkillEditor } from './SkillEditor';

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
    /** What a later definition change does: ask (hold) or follow (accept if it scans clean). */
    onUpdatePolicy?: (server: string, policy: 'ask' | 'follow', scope?: 'server' | 'publisher') => Promise<BenchItemDetail>;
    /** Edit a custom skill's text, by the Skill.id the detail names. */
    onUpdateSkill?: (skillId: string, draft: SkillDraft) => Promise<void>;
    /** Delete a custom skill; the drawer closes on success. */
    onDeleteSkill?: (skillId: string, key: string) => Promise<void>;
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

export const CapabilityDetail: React.FC<CapabilityDetailProps> = ({ item, onClose, detail, place, onToolToggle, onReauthorize, onUpdatePolicy, onUpdateSkill, onDeleteSkill, delta }) => {
    const [d, setD] = useState<BenchItemDetail | null>(null);
    const [err, setErr] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [filter, setFilter] = useState('');
    const [refusal, setRefusal] = useState<ReauthorizeVerdict | null>(null);
    const [editing, setEditing] = useState(false);
    const [armed, setArmed] = useState(false);   // delete asks twice
    const { openMcpServers } = useBenchActions();

    useEffect(() => {
        setD(null); setErr(null); setNotice(null); setFilter(''); setRefusal(null); setEditing(false); setArmed(false);
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
    const updates = d?.updates ?? null;
    const changes = updates?.changes;
    const setPolicy = (policy: 'ask' | 'follow', scope: 'server' | 'publisher') =>
        onUpdatePolicy?.(item.name, policy, scope)
            .then(setD, e => setNotice(`Could not change the update policy: ${String((e as Error)?.message ?? e)}`));
    // ``follow``: accept this change and stop asking about later ones.  The
    // policy is set only after the accept succeeds, so a refusal changes nothing.
    const reauthorize = async (force: boolean, follow = false) => {
        if (!onReauthorize) return;
        setNotice(null);
        try {
            const r = await onReauthorize(item.name, force);
            setD(r.detail);
            if (r.result.success) {
                setRefusal(null); setNotice(r.result.message ?? `${item.name} re-authorized`);
                if (follow && onUpdatePolicy) setD(await onUpdatePolicy(item.name, 'follow', 'server'));
            }
            else setRefusal(r.result);
        } catch (e) { setNotice(`Could not re-authorize ${item.name}: ${String((e as Error)?.message ?? e)}`); }
    };

    // Edit mode: only a custom skill, and only when the owner can write.
    const editable = item.kind === 'skill' && !!d?.editable && !!d?.skill_id;
    const canEdit = editable && !!onUpdateSkill;
    const canDelete = editable && !!onDeleteSkill;
    const saveEdit = async (draft: SkillDraft) => {
        await onUpdateSkill!(d!.skill_id!, draft);
        setD(prev => (prev ? { ...prev, ...draft } : prev));
        setEditing(false);
    };
    const remove = async () => {
        try { await onDeleteSkill!(d!.skill_id!, item.key); onClose(); }
        catch (e) { setArmed(false); setNotice(`Could not delete ${item.name}: ${String((e as Error)?.message ?? e)}`); }
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
            {/* The gate comes first: the placement, provenance and tool sections below can run long, and the action it asks for must not sit under them. */}
            {gate && (
                <section data-testid="detail-gate" style={{ marginTop: 10, padding: 8, borderRadius: 6, border: '1px solid #d4380d',
                                                           background: 'rgba(212,56,13,0.06)' }}>
                    <div style={{ ...label, color: '#d4380d', opacity: 1, margin: '0 0 4px' }}>⛔ DEFINITIONS CHANGED · NOT SENT</div>
                    <div>{gate.reason}</div>
                    <div style={{ ...muted, marginTop: 2 }}>
                        The card keeps its place; none of its tools reach the model until you accept the new definitions.
                    </div>
                    {changes && (
                        <div data-testid="detail-gate-changes" style={{ marginTop: 6, fontSize: 10 }}>
                            {!changes.known ? (
                                <span style={muted}>What changed is not known: these definitions were approved before Ziya kept a record of them. The next change will be itemised.</span>
                            ) : (
                                <>
                                    {changes.added.length > 0 && <div><b>added</b> <code>{changes.added.join(', ')}</code></div>}
                                    {changes.removed.length > 0 && <div><b>removed</b> <code>{changes.removed.join(', ')}</code></div>}
                                    {changes.changed.map(c => (
                                        <div key={c.name}><b>changed</b> <code>{c.name}</code> <span style={muted}>({c.what.join(', ')})</span></div>
                                    ))}
                                    {changes.added.length + changes.removed.length + changes.changed.length === 0 && (
                                        <span style={muted}>{changes.summary}</span>
                                    )}
                                </>
                            )}
                        </div>
                    )}
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
                                <>
                                    <button type="button" data-testid="detail-reauthorize" onClick={() => void reauthorize(false)}
                                            style={{ fontSize: 11, padding: '2px 10px', borderRadius: 5, cursor: 'pointer', color: '#fff',
                                                     background: '#d4380d', border: '1px solid #d4380d' }}>Approve this update</button>
                                    {onUpdatePolicy && updates && updates.policy !== 'follow' && (
                                        <button type="button" data-testid="detail-reauthorize-follow" onClick={() => void reauthorize(false, true)}
                                                title="Approve this update, and approve later updates to this server automatically. An update that looks like a prompt-injection attempt still turns the server off until you look at it."
                                                style={{ fontSize: 11, padding: '2px 10px', borderRadius: 5, cursor: 'pointer', color: '#d4380d',
                                                         background: 'none', border: '1px solid #d4380d' }}>Approve, and don't ask again for this server</button>
                                    )}
                                </>
                            ) : (
                                <button type="button" data-testid="detail-reauthorize-force" onClick={() => void reauthorize(true)}
                                        style={{ fontSize: 11, padding: '2px 10px', borderRadius: 5, cursor: 'pointer', color: '#d4380d',
                                                 background: 'none', border: '1px solid #d4380d' }}>Re-authorize anyway</button>
                            )}
                        </div>
                    )}
                </section>
            )}
            {d?.description && <div style={{ opacity: 0.8, marginTop: 6 }}>{d.description}</div>}

            {item.kind === 'mcp' && updates && !d?.builtin && onUpdatePolicy && (
                <section data-testid="detail-updates">
                    <div style={label}>WHEN THIS SERVER'S TOOLS CHANGE</div>
                    <label style={{ display: 'block', cursor: 'pointer' }}>
                        <input type="radio" name={`updates-${item.key}`} data-testid="update-policy-ask"
                               checked={updates.policy === 'ask'} onChange={() => void setPolicy('ask', 'server')} />
                        {' '}<b>Turn it off until I approve</b>
                        <div style={{ ...muted, margin: '0 0 4px 18px' }}>Its tools stop working after every update until you click Approve.</div>
                    </label>
                    <label style={{ display: 'block', cursor: 'pointer' }}>
                        <input type="radio" name={`updates-${item.key}`} data-testid="update-policy-follow"
                               checked={updates.policy === 'follow'} onChange={() => void setPolicy('follow', 'server')} />
                        {' '}<b>Keep it working</b>
                        <div style={{ ...muted, margin: '0 0 4px 18px' }}>
                            Updates are approved for you. One that looks like a prompt-injection attempt still turns it off.
                        </div>
                    </label>
                    {updates.publisher && (
                        <label style={{ display: 'block', cursor: 'pointer', marginTop: 2 }}>
                            <input type="checkbox" data-testid="update-policy-publisher" checked={!!updates.publisher_follows}
                                   onChange={ev => void setPolicy(ev.target.checked ? 'follow' : 'ask', 'publisher')} />
                            {' '}Keep all servers installed from {updates.publisher_name ?? updates.publisher} working
                        </label>
                    )}
                    {updates.lapsed && (
                        <div style={{ color: '#d4380d', fontSize: 10, marginTop: 2 }}>
                            The command that starts this server was changed after you chose "Keep it working",
                            so it is back to turning off until you approve.
                        </div>
                    )}
                    {updates.accepted_by === 'policy' && updates.last_change && (
                        <div style={{ ...muted, fontSize: 10, marginTop: 2 }}>
                            Last update approved for you: {updates.last_change}
                            {updates.accepted_at ? ` · ${new Date(updates.accepted_at * 1000).toLocaleDateString()}` : ''}
                        </div>
                    )}
                </section>
            )}

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

            {detail && (
                <>
                    <div style={{ ...label, display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span>{item.kind === 'mcp' ? `TOOLS${rows.length ? ` · ${rows.length}` : ''}` : canEdit ? 'PROMPT' : 'PROMPT · READ-ONLY'}</span>
                        {item.kind === 'mcp' && openMcpServers && (
                            <button type="button" data-testid="mcp-server-details" onClick={() => openMcpServers(item.name)}
                                    style={{ ...linkBtn, marginLeft: 'auto', letterSpacing: 0, opacity: 1 }}>
                                logs, permissions, reauthorize ▸
                            </button>
                        )}
                        {(canEdit || canDelete) && !editing && (
                            <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, letterSpacing: 0, opacity: 1 }}>
                                {canEdit && (
                                    <button type="button" data-testid="detail-edit" onClick={() => setEditing(true)} style={linkBtn}>edit</button>
                                )}
                                {canDelete && !armed && (
                                    <button type="button" data-testid="detail-delete" onClick={() => setArmed(true)} style={linkBtn}>delete</button>
                                )}
                                {canDelete && armed && (
                                    <>
                                        <button type="button" data-testid="detail-delete-confirm" onClick={() => void remove()}
                                                style={{ ...linkBtn, color: '#d4380d' }}>delete {item.name}?</button>
                                        <button type="button" onClick={() => setArmed(false)} style={linkBtn}>keep</button>
                                    </>
                                )}
                            </span>
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
                    ) : editing && canEdit ? (
                        <SkillEditor title="Edit skill" saveLabel="Save"
                                     initial={{ name: d.name, description: d.description ?? '', prompt: d.prompt ?? '' }}
                                     onSave={saveEdit} onCancel={() => setEditing(false)} />
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
