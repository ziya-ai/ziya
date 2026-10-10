/**
 * TerminalPane — the body of a shell or shadow-terminal tool block.
 *
 * One shape for both so they read as the same kind of thing; the indicator
 * colour (left stripe, badge, prompt glyph, header tint) tells them apart:
 * teal for a LOCAL command run by the server, fuchsia for a REMOTE command
 * driven through a `ziya shadow` session.  Fuchsia was chosen over orange
 * because amber/orange already means warning in the tool chrome and red
 * means error; a remote command is neither.
 *
 * Remote panes carry a seq gutter: every line came from a numbered journal
 * record, and the seq is what `shadow_read(from_seq=…)` takes.
 */
import React, { useMemo, useState } from 'react';
import type { ChipTone, PaneChip, PaneLineKind, TerminalPaneModel } from '../utils/terminalPane';

export const COLLAPSED_ROWS = 8;

const TONE: Record<ChipTone, { bg: string; fg: string; dbg: string; dfg: string }> = {
    ok:      { bg: '#dcfce7', fg: '#166534', dbg: 'rgba(34,197,94,0.18)',  dfg: '#86efac' },
    warn:    { bg: '#fef3c7', fg: '#92400e', dbg: 'rgba(245,158,11,0.18)', dfg: '#fcd34d' },
    err:     { bg: '#fee2e2', fg: '#991b1b', dbg: 'rgba(239,68,68,0.18)',  dfg: '#fca5a5' },
    info:    { bg: '#e0f2fe', fg: '#075985', dbg: 'rgba(14,165,233,0.18)', dfg: '#7dd3fc' },
    neutral: { bg: '#f1f5f9', fg: '#334155', dbg: 'rgba(148,163,184,0.18)', dfg: '#cbd5e1' },
};

const THEME = {
    local: {
        badge: 'LOCAL', prompt: '$',
        accent: { dark: '#2dd4bf', light: '#0d9488' },
        accentBg: { dark: 'rgba(45,212,191,0.15)', light: 'rgba(13,148,136,0.12)' },
        headerBg: { dark: '#0f172a', light: '#ecfdf5' },
        where: { dark: '#64748b', light: '#64748b' },
    },
    remote: {
        badge: 'REMOTE', prompt: '▶',
        accent: { dark: '#e879f9', light: '#a21caf' },
        accentBg: { dark: 'rgba(232,121,249,0.16)', light: 'rgba(162,28,175,0.12)' },
        headerBg: { dark: '#1c1021', light: '#fdf4ff' },
        where: { dark: '#a855f7', light: '#86198f' },
    },
} as const;

const MONO = 'Menlo, Monaco, "Ubuntu Mono", monospace';
const SANS = '-apple-system, "Segoe UI", sans-serif';

const Chip: React.FC<{ chip: PaneChip; isDarkMode: boolean }> = ({ chip, isDarkMode }) => {
    const t = TONE[chip.tone];
    return (
        <span data-testid="tp-chip" title={chip.title} style={{
            display: 'inline-block', borderRadius: 10, padding: '1px 8px', fontSize: 11, lineHeight: '16px',
            fontFamily: SANS, fontWeight: 'normal', letterSpacing: 0, whiteSpace: 'nowrap',
            background: isDarkMode ? t.dbg : t.bg, color: isDarkMode ? t.dfg : t.fg,
        }}>{chip.label}</span>
    );
};

interface Row { seq?: number; kind: PaneLineKind; text: string; first: boolean; }

function toRows(model: TerminalPaneModel): Row[] {
    const rows: Row[] = [];
    for (const l of model.lines) {
        l.text.split('\n').forEach((text, i) => rows.push({ seq: i === 0 ? l.seq : undefined, kind: l.kind, text, first: i === 0 }));
    }
    return rows;
}

export interface TerminalPaneProps {
    model: TerminalPaneModel;
    isDarkMode: boolean;
    verified?: boolean;
    /** Start expanded regardless of length (tests, exports). */
    defaultExpanded?: boolean;
    /**
     * Show the LOCAL badge.  Only worth it when a shadow terminal is in play
     * (utils/shadowPresence.ts); otherwise every command is local and the
     * tag is noise.  REMOTE panes always show their badge.
     */
    showLocalBadge?: boolean;
}

export const TerminalPane: React.FC<TerminalPaneProps> = ({ model, isDarkMode, verified, defaultExpanded, showLocalBadge = false }) => {
    const theme = THEME[model.kind];
    const mode = isDarkMode ? 'dark' : 'light';
    const accent = theme.accent[mode];
    const rows = useMemo(() => toRows(model), [model]);
    const clippable = rows.length > COLLAPSED_ROWS;
    const [expanded, setExpanded] = useState(!!defaultExpanded || !clippable);
    const [showHidden, setShowHidden] = useState(false);
    const visible = expanded ? rows : rows.slice(0, COLLAPSED_ROWS);
    const gutter = model.kind === 'remote';

    const border = isDarkMode ? '#1f2937' : '#d9dee5';
    const paneBg = isDarkMode ? '#0b0f14' : '#f8f9fa';
    const text = isDarkMode ? '#cbd5e1' : '#1e293b';
    const dim = isDarkMode ? '#64748b' : '#6b7280';
    const gutterFg = isDarkMode ? '#4b5563' : '#9ca3af';

    const where: React.ReactNode[] = [];
    if (model.kind === 'remote' && model.session) {
        where.push(<span key="s">shadow <b style={{ color: accent, fontWeight: 600 }}>{model.session}</b>
            {model.sessionLabel ? <span style={{ opacity: 0.8 }}> · {model.sessionLabel}</span> : null}</span>);
    }
    if (model.cwd) where.push(<span key="c" data-testid="tp-cwd" title={model.cwd}>{model.cwd}</span>);

    const rowColor = (k: PaneLineKind) => k === 'cmd' ? accent : k === 'output' ? text : dim;

    return (
        <div data-testid="terminal-pane" data-kind={model.kind} data-tool={model.tool} style={{
            border: `1px solid ${border}`, borderLeft: `4px solid ${accent}`, borderRadius: 10,
            background: paneBg, margin: '14px 0', overflow: 'hidden', fontSize: 12.5, color: text,
            boxShadow: isDarkMode ? '0 2px 6px rgba(0,0,0,0.3)' : '0 2px 6px rgba(0,0,0,0.06)',
        }}>
            <div onClick={() => clippable && setExpanded(e => !e)} style={{
                display: 'flex', alignItems: 'center', gap: 10, padding: '7px 12px', fontFamily: SANS,
                background: theme.headerBg[mode], borderBottom: `1px solid ${border}`,
                cursor: clippable ? 'pointer' : 'default', minWidth: 0,
            }}>
                {(model.kind === 'remote' || showLocalBadge) && (
                    <span data-testid="tp-badge" style={{
                        background: theme.accentBg[mode], color: accent, fontSize: 10, fontWeight: 700,
                        letterSpacing: '0.08em', padding: '2px 7px', borderRadius: 4, flex: 'none',
                    }}>{theme.badge}</span>
                )}
                {where.length > 0 && (
                    <span style={{ fontSize: 11, color: theme.where[mode], flex: 'none', maxWidth: '38%',
                                   overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {where.map((w, i) => <React.Fragment key={i}>{i > 0 && ' · '}{w}</React.Fragment>)}
                    </span>
                )}
                <code data-testid="tp-command" title={model.command} style={{
                    fontFamily: MONO, fontSize: 12.5, color: text, flex: 1, minWidth: 0,
                    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                }}>
                    <span style={{ color: accent }}>{theme.prompt}</span> {model.command}
                </code>
                <span style={{ display: 'flex', gap: 6, flex: 'none' }}>
                    {model.chips.map((c, i) => <Chip key={i} chip={c} isDarkMode={isDarkMode} />)}
                </span>
                <span style={{ color: dim, fontSize: 11, flex: 'none' }} data-testid="tp-toggle">
                    {verified && '🔐 '}{clippable ? (expanded ? '▼' : '▶') : ''}
                </span>
            </div>

            {visible.length > 0 && (
                <div style={{
                    display: 'grid', gridTemplateColumns: gutter ? '44px 1fr' : '1fr',
                    fontFamily: MONO, lineHeight: 1.55, padding: '8px 0',
                    maskImage: !expanded ? 'linear-gradient(to bottom, #000 70%, transparent)' : undefined,
                    WebkitMaskImage: !expanded ? 'linear-gradient(to bottom, #000 70%, transparent)' : undefined,
                }}>
                    {visible.map((r, i) => (
                        <React.Fragment key={i}>
                            {gutter && (
                                <span data-testid="tp-gutter" style={{
                                    color: gutterFg, textAlign: 'right', paddingRight: 8, userSelect: 'none',
                                    borderRight: `1px solid ${border}`, fontSize: 11,
                                }}>{r.seq ?? ''}</span>
                            )}
                            <span data-kind={r.kind} style={{
                                paddingLeft: 12, paddingRight: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                                color: rowColor(r.kind), fontStyle: r.kind === 'meta' ? 'italic' : undefined,
                            }}>
                                {r.kind === 'cmd' && r.first && <span style={{ color: accent }}>$ </span>}{r.text}
                            </span>
                        </React.Fragment>
                    ))}
                </div>
            )}
            {visible.length === 0 && (
                <div style={{ padding: '8px 12px', color: dim, fontFamily: SANS, fontSize: 11 }}>(no output)</div>
            )}

            {(clippable || model.hidden.length > 0 || model.seqRange || model.status || model.next) && (
                <div style={{
                    borderTop: `1px dashed ${border}`, padding: '5px 12px', fontFamily: SANS, fontSize: 11,
                    color: dim, display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'center',
                }}>
                    {clippable && (
                        <button type="button" data-testid="tp-expand" onClick={() => setExpanded(e => !e)} style={linkBtn}>
                            {expanded ? '▴ collapse' : `▸ ${rows.length - COLLAPSED_ROWS} more lines`}
                        </button>
                    )}
                    {model.hidden.length > 0 && (
                        <button type="button" data-testid="tp-hidden-toggle" onClick={() => setShowHidden(s => !s)} style={linkBtn}>
                            {showHidden ? '▾' : '▸'} {model.hidden.length} journal event{model.hidden.length === 1 ? '' : 's'} hidden
                        </button>
                    )}
                    {model.next && (
                        <span data-testid="tp-next">next: <code style={{ fontFamily: MONO, color: text, opacity: 0.8 }}>{model.next}</code></span>
                    )}
                    <span style={{ marginLeft: 'auto' }}>
                        {model.seqRange && <span data-testid="tp-seq">seq {model.seqRange.from} → {model.seqRange.to}</span>}
                        {model.status && <span data-testid="tp-status"> · <span style={{ color: TONE.ok[isDarkMode ? 'dfg' : 'fg'] }}>{model.status}</span></span>}
                    </span>
                </div>
            )}
            {showHidden && (
                <ul data-testid="tp-hidden" style={{
                    margin: 0, padding: '4px 12px 8px 28px', fontFamily: MONO, fontSize: 11, color: dim,
                    borderTop: `1px dashed ${border}`,
                }}>
                    {model.hidden.map((h, i) => <li key={i} style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{h}</li>)}
                </ul>
            )}
        </div>
    );
};

const linkBtn: React.CSSProperties = {
    border: 'none', background: 'none', padding: 0, cursor: 'pointer', color: 'inherit',
    textDecoration: 'underline', fontSize: 11, fontFamily: SANS,
};

export default TerminalPane;
