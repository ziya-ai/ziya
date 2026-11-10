/**
 * ReferencesLine — the 📎 line in ActiveContextBar (design/conversation-
 * references.md §Surface 1–2).  Zero footprint until a reference exists:
 * renders nothing for a conversation with none.  One line with the bar's dot
 * grammar (solid = pinned, dashed accent = picked up and useful, dashed grey
 * = picked up and not), a teal marker when something was picked up since the
 * user last opened it, and a click-to-open popover: pinned items, then one
 * folded line for the picked-up ones (📌 promotes, ✕ removes).  Faded items
 * are counted, not listed.
 */
import React, { useState } from 'react';
import { Popover } from 'antd';
import { useConversationReferences } from '../hooks/useConversationReferences';
import type { ConversationReference } from '../apis/referencesApi';

export const REFERENCE_DOT_CAP = 12;
const KIND_ICON: Record<string, string> = { url: '🌐', file: '📄', pdf: '📕', chat: '💬' };

export function referenceLabel(r: ConversationReference): string {
    if (r.title) return r.title;
    if (r.kind === 'url') return r.locator.replace(/^https?:\/\//, '');
    if (r.kind === 'chat') return r.locator.replace(/^ziya:\/\/chat\//, 'thread ');
    return r.locator;
}

export function referenceState(r: ConversationReference): string {
    const cited = r.uses.filter(u => u.kind === 'cited').length;
    const reads = new Set(r.uses.filter(u => u.kind === 'read').map(u => u.turn)).size;
    if (cited) return `×${cited}`;
    if (reads) return `read ${reads === 1 ? 'once' : `${reads}×`}`;
    return 'unused';
}

interface Props { projectId: string | null | undefined; conversationId: string | null | undefined; text: string; accent: string; muted: string; }

export const ReferencesLine: React.FC<Props> = ({ projectId, conversationId, text, accent, muted }) => {
    const refs = useConversationReferences(projectId, conversationId);
    const [open, setOpen] = useState(false);
    const [showPicked, setShowPicked] = useState(false);
    if (refs.count === 0) return null;

    const pinned = refs.references.filter(r => r.provenance === 'pinned');
    const picked = refs.references.filter(r => r.provenance !== 'pinned' && !r.faded);
    const s = refs.summary;
    const dots = [
        ...pinned.map(r => ({ key: r.id, style: { background: accent } })),
        ...picked.map(r => ({ key: r.id, style: { border: `1px dashed ${r.useful ? accent : muted}`, boxSizing: 'border-box' as const } })),
    ];

    const Row: React.FC<{ r: ConversationReference; dim?: boolean }> = ({ r, dim }) => (
        <div data-testid={`reference-row-${r.id}`} className="ziya-reference-row"
            style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '3px 4px', opacity: dim ? 0.75 : 1 }}>
            <span aria-hidden>{KIND_ICON[r.kind] || '📎'}</span>
            <span title={r.locator} style={{ flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {referenceLabel(r)}
            </span>
            <span style={{ fontSize: 11, color: muted }}>{referenceState(r)}</span>
            {r.provenance !== 'pinned' && (
                <button type="button" aria-label={`Pin ${referenceLabel(r)}`} title="Pin to this conversation"
                    onClick={() => refs.promote(r.id).catch(e => console.error('pin failed', e))}
                    style={{ border: 'none', background: 'none', cursor: 'pointer', padding: 0, color: text }}>📌</button>
            )}
            <button type="button" aria-label={`Remove ${referenceLabel(r)}`} title="Remove reference"
                onClick={() => refs.unpin(r.id).catch(e => console.error('unpin failed', e))}
                style={{ border: 'none', background: 'none', cursor: 'pointer', padding: 0, color: muted }}>✕</button>
        </div>
    );

    const content = (
        <div data-testid="references-popover" style={{ width: 340, fontSize: 13, color: text }}>
            {pinned.length > 0 ? pinned.map(r => <Row key={r.id} r={r} />)
                : <div style={{ color: muted, fontSize: 12, padding: '3px 4px' }}>Nothing pinned yet</div>}
            {s.picked_up > 0 && (
                <div style={{ marginTop: 8, paddingTop: 6, borderTop: `1px solid ${muted}`, fontSize: 12, color: muted }}>
                    <button type="button" data-testid="references-picked-toggle" aria-expanded={showPicked}
                        onClick={() => setShowPicked(v => !v)}
                        style={{ border: 'none', background: 'none', cursor: 'pointer', padding: 0, color: muted, fontSize: 12 }}>
                        {showPicked ? '▾' : '▸'} {s.picked_up} picked up by tools
                        {s.useful ? ` · ${s.useful} look${s.useful === 1 ? 's' : ''} useful` : ''}
                        {s.faded ? ` · ${s.faded} faded` : ''}
                    </button>
                    {showPicked && picked.map(r => <Row key={r.id} r={r} dim />)}
                </div>
            )}
        </div>
    );

    return (
        <Popover
            content={content}
            trigger="click"
            placement="bottomLeft"
            open={open}
            onOpenChange={o => { setOpen(o); if (o) refs.markSeen(); }}
        >
            <button
                type="button"
                data-testid="references-line"
                aria-label={`${refs.count} references${s.picked_up ? `, ${s.picked_up} picked up` : ''}; show references`}
                title={refs.references.filter(r => !r.faded).map(referenceLabel).join(' · ')}
                style={{
                    display: 'flex', alignItems: 'center', gap: 8, width: '100%', marginTop: 6,
                    background: 'none', border: 'none', padding: 0, cursor: 'pointer',
                    color: text, fontSize: 11, textAlign: 'left', fontStyle: 'normal',
                }}
            >
                <span aria-hidden>📎</span>
                <span style={{ display: 'inline-flex', gap: 2, alignItems: 'center' }}>
                    {dots.slice(0, REFERENCE_DOT_CAP).map(d => (
                        <i key={d.key} data-testid="reference-dot"
                            style={{ width: 7, height: 7, borderRadius: '50%', display: 'inline-block', ...d.style }} />
                    ))}
                    {dots.length > REFERENCE_DOT_CAP && (
                        <span style={{ fontSize: 9, margin: '0 2px', opacity: 0.7 }}>+{dots.length - REFERENCE_DOT_CAP}</span>
                    )}
                </span>
                <span style={{ whiteSpace: 'nowrap' }}>
                    {refs.count} reference{refs.count === 1 ? '' : 's'}{s.picked_up ? ` · ${s.picked_up} picked up` : ''}
                </span>
                {refs.hasNew && (
                    <i data-testid="references-new" title="Picked up since you last looked"
                        style={{ width: 6, height: 6, borderRadius: '50%', background: accent, display: 'inline-block' }} />
                )}
                <span aria-hidden="true" style={{ marginLeft: 'auto', color: accent }}>▸</span>
            </button>
        </Popover>
    );
};

export default ReferencesLine;
