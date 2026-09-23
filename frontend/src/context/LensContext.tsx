/**
 * Which layer the hub is EDITING (design/capabilities-hub.md "Lens").
 * Client-only: GET /bench returns every layer, so switching lens never
 * re-fetches. Falls back to a local default when no provider is mounted so
 * useBench() works in surfaces (ActiveContextBar) that predate the hub.
 */
import React, { createContext, useContext, useMemo, useState } from 'react';
import type { Layer } from '../utils/activation';
import type { BenchViewItem } from '../apis/benchApi';

interface LensValue { lens: Layer; setLens: (l: Layer) => void; }
const LensCtx = createContext<LensValue | null>(null);

export const LensProvider: React.FC<{ initial?: Layer; children: React.ReactNode }> = ({ initial = 'project', children }) => {
    const [lens, setLens] = useState<Layer>(initial);
    const value = useMemo(() => ({ lens, setLens }), [lens]);
    return <LensCtx.Provider value={value}>{children}</LensCtx.Provider>;
};

export function useLens(): LensValue {
    const v = useContext(LensCtx);
    const [lens, setLens] = useState<Layer>('project');
    return v ?? { lens, setLens };
}

/** How one item renders at a lens: pinned here, inherited (ghost), or fixed. */
export interface LensView {
    state: BenchViewItem['effective'];
    ghost: boolean;          // inherited from a lower layer or the default
    pinnedHigher: boolean;   // a higher-precedence layer overrides this lens
    origin: BenchViewItem['origin'];
}

const ORDER: Layer[] = ['conversation', 'project', 'user'];

export function viewAtLens(item: BenchViewItem, lens: Layer): LensView {
    if (item.tier === 'environment') return { state: 'always', ghost: false, pinnedHigher: false, origin: 'environment' };
    const here = item.placements[lens];
    const higher = ORDER.slice(0, ORDER.indexOf(lens)).some(l => item.placements[l] !== null);
    return {
        state: here ?? item.effective,
        ghost: here === null,
        pinnedHigher: higher,
        origin: item.origin,
    };
}
