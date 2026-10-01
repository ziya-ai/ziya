/**
 * Command palette over the bench (design/capabilities-hub.md, slice 7).
 *
 * A second SHELL over the same useBench() model as the board: it imports no
 * board component and no API client, and every verb routes through
 * ``dropIntent`` so the palette cannot place something the board would
 * refuse.  If the palette can do everything the board can without touching
 * it, the view-model seam is real -- that is the point of building it now.
 *
 *   type      filter installed items by name / kind / provenance
 *   ↑ ↓       move; ↵ apply the highlighted verb; ⇥ next verb; ⇧⇥ change lens
 *   esc       close
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import type { BenchItemView } from '../../hooks/useBench';
import type { Layer, PlacementState } from '../../utils/activation';
import { KindMark } from './KindMark';
import { LANES, MoveOption, moveOptions, rejectionText } from './laneDnd';

export interface BenchPaletteModel {
    items: BenchItemView[];
    lens: Layer;
    lenses: Layer[];
    setLens: (l: Layer) => void;
    place: (key: string, state: PlacementState | null, layer?: Layer) => Promise<void>;
}

export interface BenchPaletteProps {
    bench: BenchPaletteModel;
    open: boolean;
    onClose: () => void;
}

const LENS_LABEL: Record<Layer, string> = { user: 'me', project: 'project', conversation: 'this chat' };
const laneLabel = (s: PlacementState) => LANES.find(l => l.state === s)!.label.toLowerCase();

/** Case-insensitive match on name, kind, provenance; empty query matches all. */
export function filterItems(items: BenchItemView[], q: string): BenchItemView[] {
    const needle = q.trim().toLowerCase();
    const placeable = items.filter(i => i.tier !== 'environment');
    if (!needle) return placeable;
    return placeable.filter(i =>
        i.name.toLowerCase().includes(needle) || i.kind.includes(needle) || i.provenance.toLowerCase().includes(needle));
}

export const BenchPalette: React.FC<BenchPaletteProps> = ({ bench, open, onClose }) => {
    const [q, setQ] = useState('');
    const [row, setRow] = useState(0);
    const [verb, setVerb] = useState(0);
    const [notice, setNotice] = useState<string | null>(null);
    const inputRef = useRef<HTMLInputElement>(null);
    const results = useMemo(() => filterItems(bench.items, q).slice(0, 12), [bench.items, q]);
    const current = results[Math.min(row, results.length - 1)];
    const verbs = current ? moveOptions(current).filter(o => !o.disabled) : [];

    useEffect(() => { if (open) { setQ(''); setRow(0); setVerb(0); setNotice(null); inputRef.current?.focus(); } }, [open]);
    useEffect(() => { setRow(0); setVerb(0); }, [q]);

    const cycleLens = () => {
        const i = bench.lenses.indexOf(bench.lens);
        bench.setLens(bench.lenses[(i + 1) % bench.lenses.length]);
        setVerb(0);
    };

    // ``pick`` overrides the highlighted verb: a mouse click must apply the
    // option clicked, not whichever one the keyboard had highlighted.
    const apply = async (pick?: MoveOption) => {
        const o = pick ?? verbs[verb];
        if (!current || !o || o.intent.kind !== 'place') return;
        try { await bench.place(o.intent.key, o.intent.toState); setNotice(`${current.name} → ${o.label}`); }
        catch (e) {
            const reason = (e as { reason?: unknown })?.reason;
            setNotice(typeof reason === 'string' ? rejectionText(reason, current.name) : `Could not move ${current.name}`);
        }
    };

    const onKey = (e: React.KeyboardEvent) => {
        if (e.key === 'Escape') { e.preventDefault(); onClose(); }
        else if (e.key === 'ArrowDown') { e.preventDefault(); setRow(r => Math.min(r + 1, results.length - 1)); setVerb(0); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); setRow(r => Math.max(r - 1, 0)); setVerb(0); }
        else if (e.key === 'Tab' && e.shiftKey) { e.preventDefault(); cycleLens(); }
        else if (e.key === 'Tab') { e.preventDefault(); if (verbs.length) setVerb(v => (v + 1) % verbs.length); }
        else if (e.key === 'Enter') { e.preventDefault(); void apply(); }
    };

    if (!open) return null;
    return (
        <div role="dialog" aria-label="Bench palette" data-testid="bench-palette" onKeyDown={onKey}
             style={{ position: 'absolute', inset: 0, zIndex: 20, display: 'flex', justifyContent: 'center',
                      alignItems: 'flex-start', paddingTop: 40, background: 'rgba(0,0,0,0.25)' }}
             onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
            <div style={{ width: 'min(640px, 90%)', borderRadius: 10, padding: 10, fontSize: 12,
                          background: 'var(--ant-color-bg-elevated, Canvas)',
                          border: '1px solid var(--mockup-border, rgba(127,127,127,0.35))' }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <span style={{ opacity: 0.6 }}>›</span>
                    <input ref={inputRef} data-testid="palette-input" value={q} onChange={e => setQ(e.target.value)}
                           placeholder="skill or server name…" aria-label="Search the bench"
                           style={{ flex: 1, border: 'none', outline: 'none', background: 'none', color: 'inherit', fontSize: 13 }} />
                    <button type="button" data-testid="palette-lens" onClick={cycleLens} title="⇧⇥ changes lens"
                            style={{ fontSize: 11, border: '1px solid var(--mockup-border, rgba(127,127,127,0.35))',
                                     borderRadius: 10, padding: '1px 8px', background: 'none', color: '#0d9488', cursor: 'pointer' }}>
                        editing {LENS_LABEL[bench.lens]}
                    </button>
                </div>
                <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 2 }}>
                    {results.length === 0 && <div style={{ opacity: 0.6, padding: 6 }}>nothing installed matches</div>}
                    {results.map((i, idx) => {
                        const sel = i === current;
                        return (
                            <div key={i.key} data-testid={`palette-row-${i.key}`} aria-selected={sel} role="option"
                                 onMouseEnter={() => { setRow(idx); setVerb(0); }}
                                 style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '5px 8px', borderRadius: 6,
                                          background: sel ? 'rgba(20,184,166,0.12)' : 'transparent' }}>
                                <KindMark kind={i.kind} compact />
                                <b style={{ flex: 1 }}>{i.name}</b>
                                <span style={{ opacity: 0.6, fontSize: 10 }}>{i.provenance}</span>
                                <span style={{ fontSize: 10 }}>
                                    {laneLabel(i.atLens.state)}{i.atLens.ghost ? ' (inherited)' : ''}
                                </span>
                            </div>
                        );
                    })}
                </div>
                {current && (
                    <div data-testid="palette-verbs" style={{ display: 'flex', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
                        {verbs.map((o, idx) => (
                            <button key={o.label} type="button" role="menuitem" aria-selected={idx === verb}
                                    onClick={() => { setVerb(idx); void apply(o); }}
                                    style={{ fontSize: 11, padding: '2px 8px', borderRadius: 6, cursor: 'pointer', color: 'inherit',
                                             background: idx === verb ? 'rgba(179,44,168,0.15)' : 'none',
                                             border: `1px solid ${idx === verb ? '#b32ca8' : 'var(--mockup-border, rgba(127,127,127,0.35))'}` }}>
                                {o.label}{idx === verb ? ' ↵' : ''}
                            </button>
                        ))}
                        {verbs.length === 0 && <span style={{ opacity: 0.6 }}>nowhere to move at this lens</span>}
                    </div>
                )}
                {notice && <div role="status" style={{ marginTop: 6, fontSize: 11, opacity: 0.8 }}>{notice}</div>}
                <div style={{ marginTop: 6, fontSize: 10, opacity: 0.5 }}>↑↓ item · ⇥ verb · ⇧⇥ lens · ↵ apply · esc close</div>
            </div>
        </div>
    );
};
