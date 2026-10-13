/**
 * Rendering for a ToolDigest (utils/toolDigest.ts).
 *
 *   DigestPreview     the collapsed body of a tool block: headline, chips,
 *                     facts or output lines.  Replaces the first-200-bytes
 *                     preview that showed a lone "{" for JSON.
 *   ToolResultViewer  the expanded body for JSON payloads: Table / Tree / Raw
 *                     tabs for collections and records, a prose view for a
 *                     message, with the digest headline pinned above.
 *
 * Both are pure renderers; the digest is computed by the caller.
 */
import React, { useMemo, useState } from 'react';
import type { DigestChip, ToolDigest } from '../utils/toolDigest';

const TONE: Record<DigestChip['tone'], { bg: string; fg: string; dbg: string; dfg: string }> = {
    ok:      { bg: '#dcfce7', fg: '#166534', dbg: 'rgba(34,197,94,0.18)',  dfg: '#86efac' },
    warn:    { bg: '#fef3c7', fg: '#92400e', dbg: 'rgba(245,158,11,0.18)', dfg: '#fcd34d' },
    err:     { bg: '#fee2e2', fg: '#991b1b', dbg: 'rgba(239,68,68,0.18)',  dfg: '#fca5a5' },
    info:    { bg: '#e0f2fe', fg: '#075985', dbg: 'rgba(14,165,233,0.18)', dfg: '#7dd3fc' },
    neutral: { bg: '#f1f5f9', fg: '#334155', dbg: 'rgba(148,163,184,0.18)', dfg: '#cbd5e1' },
};

const Chip: React.FC<{ chip: DigestChip; isDarkMode: boolean }> = ({ chip, isDarkMode }) => {
    const t = TONE[chip.tone];
    return (
        <span style={{
            display: 'inline-block', borderRadius: 10, padding: '1px 8px', fontSize: 11, lineHeight: '16px',
            background: isDarkMode ? t.dbg : t.bg, color: isDarkMode ? t.dfg : t.fg, whiteSpace: 'nowrap',
        }}>{chip.label}</span>
    );
};

const Chips: React.FC<{ chips: DigestChip[]; isDarkMode: boolean }> = ({ chips, isDarkMode }) =>
    chips.length ? (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
            {chips.map((c, i) => <Chip key={i} chip={c} isDarkMode={isDarkMode} />)}
        </div>
    ) : null;

const Headline: React.FC<{ digest: ToolDigest }> = ({ digest }) => (
    <div data-testid="digest-headline" style={{ fontFamily: '-apple-system, "Segoe UI", sans-serif', fontSize: 13 }}>
        <b>{digest.headline}</b>
        {digest.range && (
            <span data-testid="digest-range" style={{ opacity: 0.6, marginLeft: 6 }}>
                ({digest.range.first} → {digest.range.last})
            </span>
        )}
    </div>
);

const Warning: React.FC<{ text?: string; isDarkMode: boolean }> = ({ text, isDarkMode }) => text ? (
    <div data-testid="digest-warning" style={{
        marginTop: 8, fontSize: 12, borderRadius: 6, padding: '5px 8px',
        background: isDarkMode ? TONE.warn.dbg : TONE.warn.bg, color: isDarkMode ? TONE.warn.dfg : TONE.warn.fg,
        fontFamily: '-apple-system, "Segoe UI", sans-serif',
    }}>⚠ {text}</div>
) : null;

const LinkButton: React.FC<{ link?: ToolDigest['link'] }> = ({ link }) => link ? (
    <a href={link.href} target="_blank" rel="noopener noreferrer" onClick={e => e.stopPropagation()}
       style={{ background: '#0d9488', color: '#fff', borderRadius: 6, padding: '4px 12px', fontWeight: 600,
                textDecoration: 'none', fontSize: 12, display: 'inline-block', marginTop: 8 }}>
        {link.label} ↗
    </a>
) : null;

export interface DigestPreviewProps {
    digest: ToolDigest;
    isDarkMode: boolean;
    onExpand: () => void;
}

export const DigestPreview: React.FC<DigestPreviewProps> = ({ digest, isDarkMode, onExpand }) => {
    const muted = { opacity: 0.6, fontSize: 11 } as const;
    return (
        <div data-testid="digest-preview" onClick={onExpand} style={{ padding: '10px 16px 12px', cursor: 'pointer' }}>
            <Headline digest={digest} />
            <Chips chips={digest.chips} isDarkMode={isDarkMode} />
            {digest.facts.length > 0 && (
                <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '2px 12px', fontSize: 12, marginTop: 8 }}>
                    {digest.facts.map(f => (
                        <React.Fragment key={f.key}>
                            <span style={{ opacity: 0.6 }}>{f.key}</span>
                            <span style={{ wordBreak: 'break-word' }}>{f.value}</span>
                        </React.Fragment>
                    ))}
                </div>
            )}
            {!!digest.moreFields && (
                <div data-testid="digest-more" style={{ ...muted, marginTop: 6 }}>+ {digest.moreFields} more fields</div>
            )}
            {digest.preview.length > 0 && (
                <pre data-testid="digest-lines" style={{
                    margin: '8px 0 0', padding: '0 0 0 8px', fontSize: 12, opacity: 0.85, whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word', borderLeft: `3px solid ${isDarkMode ? '#475569' : '#cbd5e1'}`,
                }}>
                    {digest.preview.join('\n')}
                    {!!digest.moreLines && <span style={{ opacity: 0.5 }}>{'\n'}… {digest.moreLines} more</span>}
                </pre>
            )}
            {digest.keys && digest.keys.length > 0 && (
                <div style={{ ...muted, marginTop: 6 }}>keys: {digest.keys.join(' · ')}</div>
            )}
            <Warning text={digest.warning} isDarkMode={isDarkMode} />
            <LinkButton link={digest.link} />
            <div style={{ ...muted, marginTop: 8, textAlign: 'right' }}>click to expand</div>
        </div>
    );
};

/** Which body the tool block shows when expanded: the digest viewer or the raw payload. */
export function chooseBody(digest: ToolDigest | null, isCollapsible: boolean): 'viewer' | 'raw' {
    if (!digest) return 'raw';
    if (digest.kind === 'collection' || digest.kind === 'message') return 'viewer';
    if (digest.kind === 'record') return isCollapsible ? 'viewer' : 'raw';
    return 'raw';
}

const cellText = (v: unknown): string => {
    if (v == null) return '';
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
};

const ROW_CAP = 50;

const TableView: React.FC<{ digest: ToolDigest; isDarkMode: boolean }> = ({ digest, isDarkMode }) => {
    const [showHidden, setShowHidden] = useState(false);
    const [showAll, setShowAll] = useState(false);
    const table = digest.table!;
    const columns = showHidden ? [...table.columns, ...table.hidden] : table.columns;
    const rows = showAll ? table.rows : table.rows.slice(0, ROW_CAP);
    const border = isDarkMode ? '#2d2d44' : '#e2e8f0';
    const csv = () => {
        const esc = (s: string) => /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
        const all = [...table.columns, ...table.hidden];
        const text = [all.join(','), ...table.rows.map(r => all.map(c => esc(cellText(r[c]))).join(','))].join('\n');
        navigator.clipboard?.writeText(text);
    };
    return (
        <div style={{ overflow: 'auto', fontFamily: '-apple-system, "Segoe UI", sans-serif' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead>
                    <tr style={{ opacity: 0.6, textAlign: 'left' }}>
                        {columns.map(c => <th key={c} style={{ padding: '6px 10px', fontWeight: 500, borderBottom: `1px solid ${border}` }}>{c}</th>)}
                    </tr>
                </thead>
                <tbody>
                    {rows.map((r, i) => (
                        <tr key={i} style={{ background: i % 2 ? 'rgba(127,127,127,0.06)' : 'transparent' }}>
                            {columns.map(c => (
                                <td key={c} style={{ padding: '4px 10px', verticalAlign: 'top', maxWidth: 420,
                                                     overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                                    title={cellText(r[c])}>
                                    {cellText(r[c])}
                                </td>
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
            <div style={{ display: 'flex', gap: 14, padding: '6px 10px', fontSize: 11, opacity: 0.7, borderTop: `1px solid ${border}` }}>
                {table.rows.length > ROW_CAP && !showAll && (
                    <button type="button" data-testid="viewer-show-all" onClick={() => setShowAll(true)} style={linkBtn}>
                        … {table.rows.length - ROW_CAP} more rows · show all
                    </button>
                )}
                {table.hidden.length > 0 && (
                    <button type="button" data-testid="viewer-hidden-columns" onClick={() => setShowHidden(s => !s)} style={linkBtn}>
                        {showHidden ? 'hide' : `${table.hidden.length} hidden`} column{table.hidden.length === 1 ? '' : 's'}
                        {!showHidden && `: ${table.hidden.join(' · ')}`}
                    </button>
                )}
                <button type="button" onClick={csv} style={{ ...linkBtn, marginLeft: 'auto' }}>⤓ copy CSV</button>
            </div>
        </div>
    );
};

const linkBtn: React.CSSProperties = {
    border: 'none', background: 'none', padding: 0, cursor: 'pointer', color: 'inherit',
    textDecoration: 'underline', fontSize: 11,
};

const TreeNode: React.FC<{ name: string | null; value: unknown; depth: number; isDarkMode: boolean }> =
({ name, value, depth, isDarkMode }) => {
    const [open, setOpen] = useState(depth < 2);
    const isObj = value !== null && typeof value === 'object';
    const label = name !== null ? <span style={{ color: isDarkMode ? '#a78bfa' : '#6b46c1' }}>{name}: </span> : null;
    if (!isObj) {
        const color = typeof value === 'string' ? (isDarkMode ? '#86efac' : '#166534')
            : typeof value === 'number' ? (isDarkMode ? '#7dd3fc' : '#075985') : (isDarkMode ? '#fcd34d' : '#92400e');
        return <div style={{ paddingLeft: 14 }}>{label}<span style={{ color }}>{typeof value === 'string' ? value : JSON.stringify(value)}</span></div>;
    }
    const entries = Array.isArray(value) ? value.map((v, i) => [String(i), v] as [string, unknown])
        : Object.entries(value as Record<string, unknown>);
    const brackets = Array.isArray(value) ? ['[', ']'] : ['{', '}'];
    return (
        <div style={{ paddingLeft: name !== null ? 14 : 0 }}>
            <span data-testid={name !== null ? `tree-toggle-${name}` : 'tree-toggle-root'} onClick={() => setOpen(o => !o)}
                  style={{ cursor: 'pointer', userSelect: 'none' }}>
                <span style={{ display: 'inline-block', width: 12, opacity: 0.6 }}>{open ? '▾' : '▸'}</span>
                {label}
                <span style={{ opacity: 0.6 }}>{brackets[0]}{!open && ` ${entries.length} `}{!open && brackets[1]}</span>
            </span>
            {open && entries.map(([k, v]) => <TreeNode key={k} name={k} value={v} depth={depth + 1} isDarkMode={isDarkMode} />)}
            {open && <div style={{ opacity: 0.6, paddingLeft: 12 }}>{brackets[1]}</div>}
        </div>
    );
};

const TreeView: React.FC<{ value: unknown; isDarkMode: boolean }> = ({ value, isDarkMode }) => (
    <div data-testid="json-tree" style={{ padding: '8px 12px', fontSize: 12, lineHeight: 1.6, overflow: 'auto' }}>
        <TreeNode name={null} value={value} depth={0} isDarkMode={isDarkMode} />
    </div>
);

const MessageView: React.FC<{ digest: ToolDigest; isDarkMode: boolean }> = ({ digest, isDarkMode }) => {
    const paragraphs = useMemo(() => (digest.text ?? '').split(/\n\s*\n/).map(p => p.trim()).filter(Boolean), [digest.text]);
    return (
        <div data-testid="message-body" style={{ padding: '4px 16px 12px', fontFamily: '-apple-system, "Segoe UI", sans-serif', fontSize: 13 }}>
            <LinkButton link={digest.link} />
            <Warning text={digest.warning} isDarkMode={isDarkMode} />
            {paragraphs.map((p, i) => (
                p === digest.warning || /^https?:\/\/\S+$/.test(p) ? null : (
                    <p key={i} style={{ margin: '8px 0', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{p}</p>
                )
            ))}
        </div>
    );
};

type Tab = 'Message' | 'Table' | 'Tree' | 'Raw';

export interface ToolResultViewerProps {
    digest: ToolDigest;
    isDarkMode: boolean;
    /** The block's existing raw rendering (pre / markdown / highlighted). */
    raw: React.ReactNode;
}

export const ToolResultViewer: React.FC<ToolResultViewerProps> = ({ digest, isDarkMode, raw }) => {
    const tabs: Tab[] = useMemo(() => {
        const t: Tab[] = [];
        if (digest.kind === 'message') t.push('Message');
        if (digest.table) t.push('Table');
        if (digest.tree !== undefined && typeof digest.tree === 'object') t.push('Tree');
        t.push('Raw');
        return t;
    }, [digest]);
    const [tab, setTab] = useState<Tab>(tabs[0]);
    const border = isDarkMode ? '#2d2d44' : '#e2e8f0';
    const accent = '#0d9488';
    return (
        <div>
            <div style={{ padding: '8px 16px 0' }}>
                <Headline digest={digest} />
                <Chips chips={digest.chips} isDarkMode={isDarkMode} />
            </div>
            <div role="tablist" style={{ display: 'flex', gap: 2, padding: '6px 12px 0', borderBottom: `1px solid ${border}`,
                                         fontFamily: '-apple-system, "Segoe UI", sans-serif', fontSize: 12 }}>
                {tabs.map(t => (
                    <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
                            style={{ border: 'none', background: 'none', cursor: 'pointer', padding: '4px 10px',
                                     color: tab === t ? accent : 'inherit', fontWeight: tab === t ? 600 : 400,
                                     opacity: tab === t ? 1 : 0.65,
                                     borderBottom: `2px solid ${tab === t ? accent : 'transparent'}` }}>
                        {t}
                    </button>
                ))}
            </div>
            {tab === 'Message' && <MessageView digest={digest} isDarkMode={isDarkMode} />}
            {tab === 'Table' && <TableView digest={digest} isDarkMode={isDarkMode} />}
            {tab === 'Tree' && <TreeView value={digest.tree} isDarkMode={isDarkMode} />}
            {tab === 'Raw' && raw}
        </div>
    );
};
