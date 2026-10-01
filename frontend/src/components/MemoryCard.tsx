/**
 * MemoryCard — chat rendering for memory_propose / memory_save results.
 *
 * Design A1: a single fixed "memory" accent (teal) regardless of layer.  The
 * layer is identified by icon + label in a neutral chip only.  LAYER_COLORS
 * is deliberately NOT used here: the decision layer is amber, which the chat
 * surface reserves for warnings/errors, and any future layer palette must
 * not leak into tool chrome.
 */
import React from 'react';
import { Tooltip } from 'antd';
import { LAYER_ICONS, LAYER_LABELS } from '../api/memoryApi';
import type { MemoryCardData } from '../utils/mcpFormatter';

export const MEMORY_ACCENT = '#14b8a6';

const LIFECYCLE_HINT: Record<MemoryCardData['kind'], string> = {
    proposed:
        'Probationary. Promotes to durable memory automatically once it is ' +
        'corroborated and used; expires if it decays unused. No review needed.',
    saved: 'Written directly to durable memory and placed in the mind-map.',
};

interface MemoryCardProps {
    data: MemoryCardData;
    isDarkMode: boolean;
    verified?: boolean;
}

export const MemoryCard: React.FC<MemoryCardProps> = ({ data, isDarkMode, verified }) => {
    const { kind, content, layer, tags, id, pendingCount } = data;
    const isSaved = kind === 'saved';
    const headerColor = isDarkMode
        ? (isSaved ? '#5eead4' : '#2dd4bf')
        : (isSaved ? '#0f766e' : '#0d9488');
    const border = isDarkMode ? '#2d2d44' : '#cbd5e0';
    const muted = isDarkMode ? '#94a3b8' : '#64748b';
    const bg = isDarkMode ? 'rgba(20,184,166,0.08)' : 'rgba(20,184,166,0.06)';
    const layerIcon = LAYER_ICONS[layer] || '📝';
    const layerLabel = LAYER_LABELS[layer] || layer.replace(/_/g, ' ');
    const shortId = id.length > 12 ? `${id.slice(0, 9)}…` : id;

    const chip: React.CSSProperties = {
        fontSize: 11, padding: '1px 7px', borderRadius: 4,
        border: `1px solid ${border}`, color: muted, whiteSpace: 'nowrap',
    };

    return (
        <div
            data-testid="memory-card"
            data-memory-kind={kind}
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
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                    <span style={{
                        fontSize: 12, fontWeight: 600, letterSpacing: '0.4px',
                        textTransform: 'uppercase', color: headerColor,
                    }}>
                        {verified && <span title="Verified tool result" style={{ marginRight: 4 }}>🔐</span>}
                        {isSaved ? 'Memory saved' : 'Memory proposed'}
                    </span>
                    <span data-testid="memory-card-layer" style={{ ...chip, borderRadius: 999, padding: '1px 8px' }}>
                        {layerIcon} {layerLabel}
                    </span>
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
                <div data-testid="memory-card-content" style={{ fontSize: 14, lineHeight: 1.45, whiteSpace: 'pre-wrap' }}>
                    {content}
                </div>
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
            </div>
        </div>
    );
};

export default MemoryCard;
