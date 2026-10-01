/**
 * Find view (design/capabilities-hub.md, slice 8b).
 *
 * Browse is a QUERY, not a shelf.  The sources behind the bench hold tens of
 * thousands of skills and MCP servers, so this view never lists a catalog:
 *
 *   empty query   panels derived from THIS bench -- skills here that need a
 *                 server the config lacks, installed skills whose source moved
 *                 on, skills in the sources whose requirements the servers on
 *                 hand already satisfy.  No curated default, no trust tier.
 *   a query       one column per source: top-N hits, "+M more", and a link to
 *                 the full registry.  A registry support level is a badge on
 *                 a hit, nothing more.
 *
 * A renderer: results arrive through ``model.find``, installs go through
 * ``model.install`` (which lands files at the lens's layer and places them
 * there in one call).  It fetches nothing.
 */
import React, { useEffect, useRef, useState } from 'react';
import type { FindColumn, FindHit, FindHome, FindResponse } from '../../hooks/useBench';
import { isFindResults, MCP_FIND_COLUMN } from '../../hooks/useBench';
import type { Layer, PlacementState } from '../../utils/activation';
import { useBenchActions } from './BenchActions';
import { KindMark, columnKind, kindColor } from './KindMark';
import { rejectionText } from './laneDnd';

export interface BenchFindModel {
    find: (q: string, n?: number) => Promise<FindResponse>;
    /** Absent = read-only (no install buttons). */
    install?: (source: string, id: string, state: PlacementState | null) => Promise<unknown>;
    lens: Layer;
}

export interface BenchFindProps { model: BenchFindModel; }

export const DEBOUNCE_MS = 300;
const N = 8;
const LANDS_IN: Record<Layer, string> = {
    user: 'your skills (every project)', project: "this project's skills", conversation: "this project's skills",
};

const border = '1px solid var(--mockup-border, rgba(127,127,127,0.35))';
const muted: React.CSSProperties = { opacity: 0.6 };
const label: React.CSSProperties = { fontSize: 10, letterSpacing: '.08em', opacity: 0.6, margin: '6px 0 4px' };
const smallBtn: React.CSSProperties = { fontSize: 11, padding: '2px 8px', borderRadius: 5, color: 'inherit', background: 'none', border, cursor: 'pointer' };
const linkBtn: React.CSSProperties = { border: 'none', background: 'none', color: 'inherit', cursor: 'pointer',
                                       textDecoration: 'underline', padding: 0, fontSize: 11 };
const shortVersion = (v?: string | null) => (v ? (v.length > 12 ? v.slice(0, 8) : v) : '—');

export const BenchFind: React.FC<BenchFindProps> = ({ model }) => {
    const { find, install, lens } = model;
    const { openRegistry } = useBenchActions();
    const [q, setQ] = useState('');
    const [tick, setTick] = useState(0);          // bumped after an install to re-ask the same query
    const [result, setResult] = useState<FindResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const seq = useRef(0);
    const lastQ = useRef('');

    // The home panels load at once and a re-ask is immediate; a typed query
    // waits for the keys to settle.  A late answer to an earlier ask is dropped.
    useEffect(() => {
        const mine = ++seq.current;
        const delay = q === lastQ.current ? 0 : DEBOUNCE_MS;
        lastQ.current = q;
        const t = setTimeout(() => {
            setLoading(true);
            find(q, N).then(
                r => { if (seq.current === mine) { setResult(r); setError(null); } },
                e => { if (seq.current === mine) setError(String((e as Error)?.message ?? e)); },
            ).finally(() => { if (seq.current === mine) setLoading(false); });
        }, delay);
        return () => clearTimeout(t);
    }, [find, q, tick]);

    const doInstall = async (h: { source: string; id: string; name: string }, state: PlacementState | null) => {
        if (!install) return;
        setNotice(null);
        try {
            await install(h.source, h.id, state);
            setNotice(state ? `${h.name} installed → ${state === 'always' ? 'on hand' : 'within reach'}` : `${h.name} updated`);
            setTick(t => t + 1);
        } catch (e) {
            const reason = (e as { reason?: unknown })?.reason;
            setNotice(typeof reason === 'string' ? rejectionText(reason, h.name)
                                                 : `Could not install ${h.name}: ${String((e as Error)?.message ?? e)}`);
        }
    };

    const hitRow = (h: FindHit, kind: string) => (
        <div key={`${h.source}:${h.id}`} data-testid={`hit-${h.source}:${h.id}`}
             style={{ padding: '5px 8px', borderTop: border, display: 'flex', flexDirection: 'column', gap: 2, fontSize: 11 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <KindMark kind={columnKind(kind)} />
                <b style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.name}</b>
                {h.support_level && (
                    <span data-testid="hit-support" style={{ fontSize: 9, padding: '0 5px', borderRadius: 8, border, opacity: 0.8 }}>{h.support_level}</span>
                )}
                {h.installed && <span data-testid={`hit-installed-${h.id}`} style={{ fontSize: 10, color: '#0d9488' }}>installed</span>}
            </div>
            {h.description && (
                <div style={{ ...muted, fontSize: 10, overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>
                    {h.description}
                </div>
            )}
            {h.matched && h.matched.length > 0 && (
                <div style={{ fontSize: 10 }}><span style={muted}>{kind === 'mcp' ? 'tools' : 'skills'}: </span>{h.matched.join(', ')}</div>
            )}
            {h.missing_requires && h.missing_requires.length > 0 && (
                <div data-testid={`hit-needs-${h.id}`} style={{ fontSize: 10, color: '#d46b08' }}>needs {h.missing_requires.join(', ')}</div>
            )}
            <div style={{ display: 'flex', gap: 6, marginTop: 2 }}>
                {kind === 'mcp'
                    ? (openRegistry && <button type="button" onClick={openRegistry} style={linkBtn}>open registry ▸</button>)
                    : install && !h.installed && (
                        <>
                            <button type="button" aria-label={`Install ${h.name} on hand`} onClick={() => void doInstall(h, 'always')} style={smallBtn}>→ on hand</button>
                            <button type="button" aria-label={`Install ${h.name} within reach`} onClick={() => void doInstall(h, 'ondemand')} style={smallBtn}>→ within reach</button>
                        </>
                    )}
            </div>
        </div>
    );

    const home = (h: FindHome) => {
        const empty = h.needs.length + h.updates.length + h.pairs_with.length === 0;
        return (
            <div data-testid="find-home" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12 }}>
                {h.needs.length > 0 && (
                    <section data-testid="find-needs" style={{ border, borderRadius: 6, padding: 8 }}>
                        <div style={label}>THIS BENCH NEEDS</div>
                        {h.needs.map(n => (
                            <div key={n.key} style={{ fontSize: 11, display: 'flex', gap: 6, alignItems: 'baseline', flexWrap: 'wrap' }}>
                                <b>{n.name}</b><span style={muted}>needs</span>
                                {n.missing.map(m => (
                                    <button key={m} type="button" onClick={() => setQ(m.slice(m.indexOf(':') + 1))} style={linkBtn}
                                            title="Search the sources for this server">{m}</button>
                                ))}
                            </div>
                        ))}
                    </section>
                )}
                {h.updates.length > 0 && (
                    <section data-testid="find-updates" style={{ border, borderRadius: 6, padding: 8 }}>
                        <div style={label}>UPDATES</div>
                        {h.updates.map(u => (
                            <div key={u.key} style={{ fontSize: 11, display: 'flex', gap: 6, alignItems: 'center' }}>
                                <b style={{ flex: 1 }}>{u.name}</b>
                                <span style={muted}>{shortVersion(u.installed_version)} → {shortVersion(u.current_version)}</span>
                                {install && (
                                    <button type="button" aria-label={`Update ${u.name}`} style={smallBtn}
                                            onClick={() => void doInstall({ source: u.source, id: u.id, name: u.name }, null)}>update</button>
                                )}
                            </div>
                        ))}
                    </section>
                )}
                {h.pairs_with.length > 0 && (
                    <section data-testid="find-pairs" style={{ border, borderRadius: 6, padding: 8 }}>
                        <div style={label}>PAIRS WITH WHAT YOU HAVE</div>
                        <div style={{ ...muted, fontSize: 10 }}>skills whose servers are already on this bench</div>
                        {h.pairs_with.map(p => hitRow(p, 'skill'))}
                    </section>
                )}
                {empty && (
                    <div data-testid="find-home-empty" style={{ ...muted, fontSize: 11 }}>
                        Nothing to suggest from this bench yet — type to search the sources.
                    </div>
                )}
            </div>
        );
    };

    const columns = (cols: FindColumn[]) => (
        <div data-testid="find-columns"
             style={{ display: 'grid', gridTemplateColumns: `repeat(${Math.max(1, cols.length)}, minmax(0, 1fr))`, gap: 12, flex: 1, minHeight: 0 }}>
            {cols.map(c => {
                const more = c.total !== null ? Math.max(0, c.total - c.hits.length) : 0;
                const ck = columnKind(c.kind);
                return (
                    <section key={c.source} data-testid={`find-col-${c.source}`} data-kind={ck}
                             style={{ border, borderTop: `3px solid ${kindColor(ck)}`, borderRadius: 6, minHeight: 0, overflowY: 'auto' }}>
                        <div style={{ padding: '6px 8px', fontSize: 11, display: 'flex', gap: 6, alignItems: 'baseline' }}>
                            <KindMark kind={ck} />
                            <b>{c.name}</b>
                            <span style={{ ...muted, fontSize: 10 }}>{ck === 'mcp' ? 'servers' : `skills · ${c.kind}`}</span>
                            {c.total !== null && <span style={{ ...muted, marginLeft: 'auto', fontSize: 10 }}>{c.total} match{c.total === 1 ? '' : 'es'}</span>}
                        </div>
                        {c.error && <div role="alert" style={{ padding: 8, fontSize: 11, color: '#d46b08' }}>unavailable: {c.error}</div>}
                        {!c.error && c.hits.length === 0 && <div style={{ ...muted, padding: 8, fontSize: 11 }}>no match</div>}
                        {c.hits.map(h => hitRow(h, c.kind))}
                        {(more > 0 || c.source === MCP_FIND_COLUMN) && (
                            <div data-testid={`find-more-${c.source}`} style={{ padding: '5px 8px', borderTop: border, fontSize: 10, display: 'flex', gap: 8 }}>
                                {more > 0 && <span style={muted}>+{more} more</span>}
                                {c.source === MCP_FIND_COLUMN && openRegistry && (
                                    <button type="button" data-testid="find-open-registry" onClick={openRegistry} style={linkBtn}>open full registry ▸</button>
                                )}
                            </div>
                        )}
                    </section>
                );
            })}
        </div>
    );

    return (
        <div data-testid="bench-find" style={{ display: 'flex', flexDirection: 'column', gap: 8, flex: 1, minHeight: 0 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <input data-testid="find-input" value={q} onChange={e => setQ(e.target.value)} autoFocus
                       placeholder="search skills and MCP servers…" aria-label="Search the sources"
                       style={{ flex: 1, fontSize: 13, padding: '5px 10px', borderRadius: 6, border, background: 'none', color: 'inherit' }} />
                {loading && <span data-testid="find-loading" style={{ ...muted, fontSize: 11 }}>searching…</span>}
            </div>
            <div style={{ ...muted, fontSize: 10 }}>a query goes to every source; installs land in {LANDS_IN[lens]}</div>
            {error && <div role="alert" style={{ fontSize: 11, color: '#d46b08' }}>Could not search: {error}</div>}
            {notice && <div role="status" style={{ fontSize: 11 }}>{notice}</div>}
            {result && (isFindResults(result) ? columns(result.per_source) : home(result))}
        </div>
    );
};
