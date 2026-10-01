/**
 * Lens selector (design/capabilities-hub.md "Lens").  Pure: the current lens,
 * the lenses the server offers (``conversation`` only when a chat is open) and
 * the per-layer pin counts all arrive as props.  Switching never re-fetches.
 */
import React from 'react';
import type { Layer } from '../../utils/activation';
import type { BenchItemView } from '../../hooks/useBench';

const LABEL: Record<Layer, string> = { user: 'me', project: 'project', conversation: 'this chat' };
export const LENS_HINT: Record<Layer, string> = {
    user: 'Your defaults, in every project',
    project: 'This project, on this machine',
    conversation: 'This conversation only',
};
// Broadest first, matching how the layers stack visually.
const DISPLAY_ORDER: Layer[] = ['user', 'project', 'conversation'];

export interface LensStripProps {
    lenses: Layer[];
    lens: Layer;
    deltas: Record<Layer, number>;
    onChange: (l: Layer) => void;
    /** For the delta tooltip: what is pinned at each layer and by whom. */
    items?: BenchItemView[];
}

export const VIA_TEXT: Record<string, string> = {
    migration: 'migrated from your previous settings',
    hub: 'placed in the bench',
    palette: 'placed from the palette',
    api: 'placed via the API',
};

/** One line per placement pinned at ``layer``: "name · state · how". */
export function pinnedSummary(items: BenchItemView[], layer: Layer): string[] {
    return items
        .filter(i => i.placements[layer] !== null)
        .map(i => {
            const via = i.meta?.[layer]?.via;
            return `${i.name} · ${i.placements[layer]}${via ? ` · ${VIA_TEXT[via] ?? via}` : ''}`;
        });
}

export const LensStrip: React.FC<LensStripProps> = ({ lenses, lens, deltas, onChange, items = [] }) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 12 }}>
        <div role="tablist" aria-label="Lens"
             style={{ display: 'inline-flex', borderRadius: 16, overflow: 'hidden',
                      border: '1px solid var(--mockup-border, rgba(127,127,127,0.35))' }}>
            {DISPLAY_ORDER.filter(l => lenses.includes(l)).map(l => {
                const active = l === lens;
                const n = deltas[l] ?? 0;
                const pinned = pinnedSummary(items, l);
                return (
                    <button key={l} type="button" role="tab" aria-selected={active}
                            data-testid={`lens-${l}`} title={LENS_HINT[l]} onClick={() => onChange(l)}
                            style={{ border: 'none', padding: '4px 12px', cursor: 'pointer', lineHeight: '18px',
                                     display: 'inline-flex', alignItems: 'center', gap: 5,
                                     background: active ? 'rgba(20,184,166,0.15)' : 'transparent',
                                     color: active ? '#0d9488' : 'inherit', fontWeight: active ? 600 : 400 }}>
                        {LABEL[l]}
                        {n > 0 && (
                            <span data-testid={`lens-delta-${l}`}
                                  title={`${n} pinned at this layer:\n${pinned.join('\n')}`}
                                  style={{ fontSize: 10, lineHeight: 1, padding: '2px 5px', borderRadius: 8,
                                           fontWeight: 600, background: 'rgba(127,127,127,0.18)' }}>+{n}</span>
                        )}
                    </button>
                );
            })}
        </div>
        <span data-testid="lens-hint" style={{ opacity: 0.6 }}>
            Editing: {LENS_HINT[lens]} · dashed cards are inherited
        </span>
    </div>
);
