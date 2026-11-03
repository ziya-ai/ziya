/**
 * MemoryCard — chat rendering for the memory tools: propose / save /
 * delete / retract (one memory) and search / expand / context (recall).
 *
 * Design A1: a single fixed "memory" accent (teal) regardless of layer.  The
 * layer is identified by icon + label in a neutral chip only.  LAYER_COLORS
 * is deliberately NOT used here: the decision layer is amber, which the chat
 * surface reserves for warnings/errors, and any future layer palette must
 * not leak into tool chrome.
 *
 * The body folds.  A single-memory card opens by default (the text is the
 * point); a recall card opens folded showing the count, and unfolds to the
 * list of memories the model loaded, each with its id, layer and tags.
 */
import React, { useState } from 'react';
import { Tooltip } from 'antd';
import { LAYER_ICONS, LAYER_LABELS } from '../api/memoryApi';
import type { MemoryCardData, MemoryEntry } from '../utils/mcpFormatter';

export const MEMORY_ACCENT = '#14b8a6';

const HEADER_LABEL: Record<MemoryCardData['kind'], string> = {
    proposed: 'Memory proposed',
    saved: 'Memory saved',
    deleted: 'Memory deleted',
    retracted: 'Proposal retracted',
    recalled: 'Memory recalled',
};

const LIFECYCLE_HINT: Record<MemoryCardData['kind'], string> = {
    proposed:
        'Probationary. Promotes to durable memory automatically once it is ' +
        'corroborated and used; expires if it decays unused. No review needed.',
    saved: 'Written directly to durable memory and placed in the mind-map.',
    deleted: 'Removed from durable memory, including its mind-map references.',
    retracted: 'A proposal this conversation made, withdrawn before review. Nothing was stored.',
    recalled: 'Memories loaded into this turn from the persistent store. Unfold to see which.',
};

interface MemoryCardProps {
    data: MemoryCardData;
    isDarkMode: boolean;
    verified?: boolean;
}

const shorten = (id: string) => (id.length > 12 ? `${id.slice(0, 9)}…` : id);

const RecallEntry: React.FC<{ entry: MemoryEntry; chip: React.CSSProperties; muted: string; border: string }> =
    ({ entry, chip, muted, border }) => (
        <div data-testid="memory-card-entry" style={{ padding: '8px 0', borderTop: `1px solid ${border}` }}>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', marginBottom: 4 }}>
                <span style={{ ...chip, borderRadius: 999 }}>
                    {LAYER_ICONS[entry.layer] || '📝'} {LAYER_LABELS[entry.layer] || entry.layer.replace(/_/g, ' ')}
                </span>
                {entry.flag && (
                    <span data-testid="memory-card-entry-flag" style={{ ...chip, fontStyle: 'italic' }}>{entry.flag}</span>
                )}
                {entry.tags.map(tag => <span key={tag} style={chip}>{tag}</span>)}
                <span title={entry.id} style={{ marginLeft: 'auto', fontSize: 11, color: muted, fontFamily: 'Menlo, Monaco, monospace' }}>
                    {shorten(entry.id)}
                </span>
            </div>
            <div style={{ fontSize: 13, lineHeight: 1.45, whiteSpace: 'pre-wrap' }}>{entry.content}</div>
        </div>
    );

export const MemoryCard: React.FC<MemoryCardProps> = ({ data, isDarkMode, verified }) => {
    const { kind, content, layer, tags, id, pendingCount, entries, count, subject, reason } = data;
    const isRecall = kind === 'recalled';
    const [open, setOpen] = useState(!isRecall);
    const isSaved = kind === 'saved';
    const headerColor = isDarkMode
        ? (isSaved ? '#5eead4' : '#2dd4bf')
        : (isSaved ? '#0f766e' : '#0d9488');
    const border = isDarkMode ? '#2d2d44' : '#cbd5e0';
    const muted = isDarkMode ? '#94a3b8' : '#64748b';
    const bg = isDarkMode ? 'rgba(20,184,166,0.08)' : 'rgba(20,184,166,0.06)';
    const layerIcon = LAYER_ICONS[layer] || '📝';
    const layerLabel = LAYER_LABELS[layer] || layer.replace(/_/g, ' ');
    const shortId = shorten(id);
    const n = typeof count === 'number' ? count : (entries?.length ?? 0);

    const chip: React.CSSProperties = {
        fontSize: 11, padding: '1px 7px', borderRadius: 4,
        border: `1px solid ${border}`, color: muted, whiteSpace: 'nowrap',
    };

    return (
        <div
            data-testid="memory-card"
            data-memory-kind={kind}
            data-memory-open={open ? 'true' : 'false'}
            style={{
                display: 'flex',
                margin: '12px 0',
                padding: '10px 14px',
                borderRadius: 8,
                background: bg,
                borderLeft: `4px solid ${MEMORY_ACCENT}`,
                borderTop: `1px solid ${border}`,
                borderRight: `1px solid ${border}`,
                borderBottom: `1px solid ${border}`,
                fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
            }}
        >
            <div style={{ fontSize: 22, lineHeight: 1, marginRight: 12, marginTop: 2 }} aria-hidden>🧠</div>
            <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: open ? 6 : 0, flexWrap: 'wrap' }}>
                    <button
                        type="button"
                        data-testid="memory-card-toggle"
                        aria-expanded={open}
                        aria-label={open ? 'Fold memory details' : 'Unfold memory details'}
                        onClick={() => setOpen(o => !o)}
                        style={{
                            background: 'none', border: 'none', padding: 0, cursor: 'pointer',
                            color: muted, fontSize: 11, lineHeight: 1, width: 14,
                        }}
                    >{open ? '▼' : '▶'}</button>
                    <span style={{
                        fontSize: 12, fontWeight: 600, letterSpacing: '0.4px',
                        textTransform: 'uppercase', color: headerColor,
                    }}>
                        {verified && <span title="Verified tool result" style={{ marginRight: 4 }}>🔐</span>}
                        {HEADER_LABEL[kind]}
                    </span>
                    {isRecall ? (
                        <span data-testid="memory-card-count" style={{ ...chip, borderRadius: 999, padding: '1px 8px' }}>
                            {n} {n === 1 ? 'memory' : 'memories'}
                        </span>
                    ) : (
                        <span data-testid="memory-card-layer" style={{ ...chip, borderRadius: 999, padding: '1px 8px' }}>
                            {layerIcon} {layerLabel}
                        </span>
                    )}
                    {isRecall && subject && (
                        <span data-testid="memory-card-subject" title={subject} style={{
                            fontSize: 12, color: muted, fontStyle: 'italic', overflow: 'hidden',
                            textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 320,
                        }}>“{subject}”</span>
                    )}
                    <Tooltip title={LIFECYCLE_HINT[kind]}>
                        <span
                            aria-label="About this memory state"
                            style={{
                                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                                width: 14, height: 14, borderRadius: '50%', fontSize: 10,
                                border: `1px solid ${muted}`, color: muted, cursor: 'help',
                            }}
                        >i</span>
                    </Tooltip>
                    {!isSaved && typeof pendingCount === 'number' && (
                        <span data-testid="memory-card-pending" style={{ marginLeft: 'auto', fontSize: 11, color: muted }}>
                            {pendingCount} on probation
                        </span>
                    )}
                </div>
                {open && isRecall && (
                    entries && entries.length > 0 ? (
                        <div data-testid="memory-card-entries">
                            {entries.map(e => (
                                <RecallEntry key={e.id} entry={e} chip={chip} muted={muted} border={border} />
                            ))}
                        </div>
                    ) : (
                        <div data-testid="memory-card-content" style={{ fontSize: 13, lineHeight: 1.45, whiteSpace: 'pre-wrap' }}>
                            {content}
                        </div>
                    )
                )}
                {open && !isRecall && (
                    <>
                        <div data-testid="memory-card-content" style={{ fontSize: 14, lineHeight: 1.45, whiteSpace: 'pre-wrap' }}>
                            {content}
                        </div>
                        {reason && (
                            <div data-testid="memory-card-reason" style={{ fontSize: 12, color: muted, marginTop: 6, fontStyle: 'italic' }}>
                                Reason: {reason}
                            </div>
                        )}
                        <div style={{ display: 'flex', gap: 6, marginTop: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                            {tags.map(tag => (
                                <span key={tag} data-testid="memory-card-tag" style={chip}>{tag}</span>
                            ))}
                            {id && (
                                <span
                                    title={id}
                                    style={{
                                        marginLeft: 'auto', fontSize: 11, color: muted,
                                        fontFamily: 'Menlo, Monaco, monospace',
                                    }}
                                >{shortId}</span>
                            )}
                        </div>
                    </>
                )}
            </div>
        </div>
    );
};

export default MemoryCard;
