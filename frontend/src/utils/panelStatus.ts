/**
 * Panel status store (design/visual-refresh.md, stage 3 — collapsed strip).
 *
 * The sidebar footer shows the model name (FolderTree) and the token
 * breakdown (TokenCountDisplay); both compute those values from fetches
 * and local state deep inside large components.  When the panel is
 * collapsed the footer unmounts with it, and the strip above the composer
 * must show the SAME numbers without re-running the fetches.  So the two
 * producers publish here and the strip subscribes.  Module-level like
 * utils/appearance.ts; no provider to thread.
 */
import { useEffect, useState } from 'react';

export interface PanelTokenStatus {
    files: number;
    chat: number;
    ast: number;
    tools: number;
    total: number;
    limit: number;
    /** null until the cache-health poll has answered. */
    cacheWorking: boolean | null;
    /** Past TokenCountDisplay's warning threshold; the bar takes an orange outline. */
    warning?: boolean;
    /** Past TokenCountDisplay's danger threshold; the bar's track turns red. */
    danger?: boolean;
}

export interface PanelModelStatus {
    id: string;
    display: string;
    pinned: boolean;
}

export interface PanelStatus {
    tokens: PanelTokenStatus | null;
    model: PanelModelStatus | null;
}

let current: PanelStatus = { tokens: null, model: null };
const listeners = new Set<(s: PanelStatus) => void>();

export function getPanelStatus(): PanelStatus {
    return current;
}

export function publishPanelStatus(patch: Partial<PanelStatus>): void {
    current = { ...current, ...patch };
    listeners.forEach(l => l(current));
}

export function subscribePanelStatus(fn: (s: PanelStatus) => void): () => void {
    listeners.add(fn);
    return () => { listeners.delete(fn); };
}

export function usePanelStatus(): PanelStatus {
    const [s, setS] = useState<PanelStatus>(getPanelStatus);
    useEffect(() => subscribePanelStatus(setS), []);
    return s;
}

/** Test seam. */
export function _resetPanelStatusForTests(): void {
    current = { tokens: null, model: null };
    listeners.clear();
}
