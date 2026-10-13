/**
 * One capability on the Bench board (design/capabilities-hub.md, slice 6).
 * A renderer: it receives a fully derived item and reports the intent of a
 * move.  It fetches nothing and resolves nothing.
 *
 * Every move has three equivalent gestures — drag, right-click, and the ⋯
 * button (keyboard-reachable) — all built from ``moveOptions``.
 *
 * ``selected`` marks the card whose detail the panel's drawer is showing
 * (CapabilityDetail).  Clicking the title row selects it; nothing unfolds
 * inside the lane.
 */
import React, { useState } from 'react';
import { theme } from 'antd';
import type { BenchItemView } from '../../hooks/useBench';
import { DRAG_MIME, DropIntent, moveOptions } from './laneDnd';
import { DeltaChip } from './DeltaChip';
import { KindMark } from './KindMark';

export interface CapabilityCardProps {
    item: BenchItemView;
    onIntent: (intent: DropIntent) => void;
    selected?: boolean;
    /** null deselects (closes the drawer). */
    onSelect?: (key: string | null) => void;
    /** ✦ the model loaded this on-demand item during the current turn. */
    pickedUp?: boolean;
    /** Transient: how this item's tokens just moved. */
    delta?: number;
}

export const CapabilityCard: React.FC<CapabilityCardProps> = ({ item, onIntent, selected = false, onSelect, pickedUp = false, delta }) => {
    const [menuOpen, setMenuOpen] = useState(false);
    // The menu floats over other cards, so it needs an opaque surface.  Taken
    // from antd's tokens (they follow the ConfigProvider algorithm): the old
    // 'var(--ant-color-bg-elevated, Canvas)' was never defined (no cssVar), and
    // 'Canvas' stays white without a document color-scheme -- white on white.
    const { token } = theme.useToken();
    const v = item.atLens;
    // Permanent: an MCP server with some tools withheld says so on its face.
    const total = Number(item.health?.tool_count ?? 0);
    const enabledCount = Number(item.health?.enabled_count ?? total);
    const withheld = Number(item.health?.withheld_tokens ?? 0);
    const partial = item.kind === 'mcp' && total > 0 && enabledCount < total;
    // An authorization gate: the card keeps its lane but nothing is sent until
    // a human clears it (today: rug-pull quarantine, cleared in the drawer).
    const gate = item.health?.gate as { state?: string; reason?: string } | null | undefined;
    const held = !!gate;

    return (
        <div
            data-testid={`card-${item.key}`}
            data-state={v.state}
            data-ghost={String(v.ghost)}
            data-pinned-higher={String(v.pinnedHigher)}
            data-picked-up={String(pickedUp)}
            data-selected={String(selected)}
            data-held={String(held)}
            draggable
            onDragStart={e => {
                e.dataTransfer.effectAllowed = 'move';
                try {
                    e.dataTransfer.setData(DRAG_MIME, item.key);
                    e.dataTransfer.setData('text/plain', item.key);
                } catch { /* some browsers restrict custom types */ }
            }}
            onContextMenu={e => { e.preventDefault(); setMenuOpen(true); }}
            style={{
                position: 'relative',
                padding: '6px 8px',
                borderRadius: 6,
                // No coloured left-edge bar: that is the error idiom elsewhere
                // in the UI.  Provenance lives in the drawer.  Inherited cards
                // are dashed (the lens hint says so once); the lighter opacity
                // marks them without making them look switched off.
                border: `1px ${v.ghost ? 'dashed' : 'solid'} ${held ? '#d4380d' : pickedUp ? '#b32ca8' : 'var(--mockup-border, rgba(127,127,127,0.35))'}`,
                opacity: v.ghost ? 0.85 : 1,
                fontSize: 12,
                cursor: 'grab',
                background: held
                    ? 'repeating-linear-gradient(135deg, rgba(212,56,13,0.12) 0 4px, transparent 4px 10px)'
                    : 'transparent',
                boxShadow: selected ? '0 0 0 2px #0d9488' : undefined,
            }}
        >
            {/* The whole title row selects the card; "details" is the labelled affordance. */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: onSelect ? 'pointer' : 'grab' }}
                 onClick={e => { if (onSelect && !(e.target as HTMLElement).closest('button')) onSelect(selected ? null : item.key); }}
                 title={onSelect ? (selected ? 'Click to close details' : 'Click for details') : undefined}>
                <KindMark kind={item.kind} />
                <span style={{ fontWeight: 600, flex: 1 }}>{item.name}</span>
                {held && (
                    <span data-testid={`gate-${item.key}`} title={`${gate?.reason ?? 'Needs attention'} — select the card to act`}
                          style={{ color: '#d4380d', fontSize: 10, fontWeight: 600, lineHeight: 1,
                                   padding: '2px 6px', borderRadius: 8, border: '1px solid #d4380d' }}>⛔ needs re-authorization</span>
                )}
                {pickedUp && (
                    <span data-testid={`picked-up-${item.key}`} title="The model loaded this during the current turn"
                          style={{ color: '#b32ca8', fontSize: 10 }}>✦ this turn</span>
                )}
                <DeltaChip delta={delta} testId={`delta-${item.key}`} />
                <span data-testid={`tokens-${item.key}`} style={{ opacity: 0.6, fontSize: 10 }}
                      title={item.kind === 'skill' && v.state === 'ondemand' ? 'when loaded' : undefined}>{item.tokens.toLocaleString()}t</span>
                {partial && (
                    <span data-testid={`partial-${item.key}`} style={{ opacity: 0.7, fontSize: 10 }}
                          title={`${total - enabledCount} tool${total - enabledCount === 1 ? '' : 's'} withheld · ${withheld.toLocaleString()}t not sent`}>
                        {enabledCount}/{total}
                    </span>
                )}
                {onSelect && (
                    <button type="button" aria-label={`Details for ${item.name}`} aria-pressed={selected}
                            onClick={() => onSelect(selected ? null : item.key)}
                            style={{ border: 'none', background: 'none', cursor: 'pointer', color: 'inherit',
                                     padding: '0 2px', opacity: 0.7, fontSize: 10 }}
                    >{selected ? '◂ selected' : 'details ▸'}</button>
                )}
                <button
                    type="button"
                    aria-label={`Move ${item.name}`}
                    aria-haspopup="menu"
                    aria-expanded={menuOpen}
                    onClick={() => setMenuOpen(o => !o)}
                    style={{ border: 'none', background: 'none', cursor: 'pointer', color: 'inherit', padding: '0 2px' }}
                >⋯</button>
            </div>
            {v.pinnedHigher && (
                <div style={{ opacity: 0.8, fontSize: 10 }}>overridden by {item.origin}</div>
            )}
            {menuOpen && (
                <div
                    role="menu"
                    onKeyDown={e => { if (e.key === 'Escape') setMenuOpen(false); }}
                    style={{
                        position: 'absolute', right: 4, top: '100%', zIndex: 10, marginTop: 2,
                        display: 'flex', flexDirection: 'column', minWidth: 170,
                        border: '1px solid ' + token.colorBorderSecondary,
                        borderRadius: 6, padding: 4, background: token.colorBgElevated,
                        color: token.colorText, boxShadow: token.boxShadowSecondary,
                    }}
                >
                    {moveOptions(item).map(o => (
                        <button
                            key={o.label}
                            type="button"
                            role="menuitem"
                            disabled={o.disabled}
                            onClick={() => { setMenuOpen(false); onIntent(o.intent); }}
                            style={{ textAlign: 'left', border: 'none', background: 'none', padding: '4px 6px',
                                     cursor: o.disabled ? 'default' : 'pointer', color: 'inherit', opacity: o.disabled ? 0.5 : 1 }}
                        >{o.label}</button>
                    ))}
                </div>
            )}
        </div>
    );
};
