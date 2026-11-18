/**
 * Per-conversation state of a turn held for its diffs to be applied
 * (design/await-diff-apply.md).  Fed by the ``diff_wait`` stream event;
 * read by the awaiting-apply chip, by StopStreamButton, and by the
 * conversation list.
 *
 * Subscribers read narrow snapshots so an apply re-renders only what shows
 * its count.  Each apply sends a new ``remaining``; with one global version
 * every subscriber, including the whole conversation list, re-rendered on
 * each click, which stuttered scrolling.  Now:
 *   - useDiffWait(id) changes only when THAT conversation's state changes;
 *   - useDiffWaitLookup() changes only when a wait starts or ends;
 *   - an event that changes nothing notifies nobody.
 */
import { useSyncExternalStore } from 'react';

export interface DiffWait {
    total: number;
    remaining: number;
    remainingPaths: string[];
}

const waits = new Map<string, DiffWait>();
const listeners = new Set<() => void>();
// Replaced only when the set of waiting conversations changes.
let lookup: (conversationId?: string | null) => boolean = () => false;

function emit(): void {
    listeners.forEach(l => l());
}

function rebuildLookup(): void {
    const ids = new Set(waits.keys());
    lookup = (conversationId) => !!conversationId && ids.has(conversationId);
}

function sameWait(a: DiffWait, b: DiffWait): boolean {
    return a.total === b.total && a.remaining === b.remaining
        && a.remainingPaths.length === b.remainingPaths.length
        && a.remainingPaths.every((p, i) => p === b.remainingPaths[i]);
}

/** Apply one ``diff_wait`` event: ``waiting`` sets the state, anything else clears it. */
export function applyDiffWaitEvent(conversationId: string, ev: any): void {
    if (!conversationId || !ev) return;
    if (ev.state === 'waiting') {
        const next: DiffWait = {
            total: Number(ev.total) || 0,
            remaining: Number(ev.remaining) || 0,
            remainingPaths: Array.isArray(ev.remaining_paths) ? ev.remaining_paths : [],
        };
        const prev = waits.get(conversationId);
        if (prev && sameWait(prev, next)) return;
        waits.set(conversationId, next);
        if (!prev) rebuildLookup();
    } else {
        if (!waits.delete(conversationId)) return;
        rebuildLookup();
    }
    emit();
}

export function clearDiffWait(conversationId: string): void {
    if (conversationId && waits.delete(conversationId)) {
        rebuildLookup();
        emit();
    }
}

export function getDiffWait(conversationId: string): DiffWait | undefined {
    return waits.get(conversationId);
}

/** The current lookup; its identity changes only when membership does. */
export function getDiffWaitLookup(): (conversationId?: string | null) => boolean {
    return lookup;
}

export function subscribeDiffWait(l: () => void): () => void {
    listeners.add(l);
    return () => { listeners.delete(l); };
}

export function useDiffWait(conversationId?: string | null): DiffWait | undefined {
    const snap = () => (conversationId ? waits.get(conversationId) : undefined);
    return useSyncExternalStore(subscribeDiffWait, snap, snap);
}

/** For lists: "is this conversation waiting for its diffs to be applied?"
 *  Re-renders only when a wait starts or ends, not on each apply. */
export function useDiffWaitLookup(): (conversationId?: string | null) => boolean {
    return useSyncExternalStore(subscribeDiffWait, getDiffWaitLookup, getDiffWaitLookup);
}
