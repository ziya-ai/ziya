/**
 * useConversationReferences() — the one owner of the references view for a
 * conversation (design/conversation-references.md, slice 2).  Fetches on
 * conversation change and on REFERENCES_CHANGED_EVENT (a turn's
 * references_updated frame, or a write made through another instance);
 * components derive nothing and call no API.
 *
 * "New since you looked" is client-only: the picked-up count the user last
 * saw in the popover, per conversation, in localStorage.
 */
import { useCallback, useEffect, useState } from 'react';
import {
    fetchReferences, pinReference, promoteReference, unpinReference,
    announceReferencesChanged, REFERENCES_CHANGED_EVENT, ReferencesView,
} from '../apis/referencesApi';

const seenKey = (conversationId: string) => `ZIYA_REFS_SEEN:${conversationId}`;

function readSeen(conversationId: string | null | undefined): number {
    if (!conversationId) return 0;
    try { return parseInt(localStorage.getItem(seenKey(conversationId)) || '0', 10) || 0; }
    catch { return 0; }
}

/**
 * Pin a reference from somewhere that holds no view (the composer's menu,
 * a bare pasted link) and announce it, so every mounted 📎 line re-fetches.
 * Rejects with the API error (``references POST failed: 400`` for an
 * untrackable locator).
 */
export async function addReference(projectId: string, conversationId: string,
                                   locator: string, title?: string) {
    const ref = await pinReference(projectId, conversationId, locator, title);
    announceReferencesChanged(conversationId);
    return ref;
}

/** One-shot read for a component that must not subscribe (a message row's
 *  hover icon: N rows, fetched only when hovered). */
export function loadReferences(projectId: string, conversationId: string): Promise<ReferencesView> {
    return fetchReferences(projectId, conversationId);
}

export function useConversationReferences(projectId: string | null | undefined,
                                          conversationId: string | null | undefined) {
    const [view, setView] = useState<ReferencesView | null>(null);
    const [seen, setSeen] = useState(() => readSeen(conversationId));

    const refresh = useCallback(async () => {
        if (!projectId || !conversationId || typeof fetch === 'undefined') { setView(null); return; }
        try { setView(await fetchReferences(projectId, conversationId)); }
        catch (e) { console.warn('references fetch failed', e); setView(null); }
    }, [projectId, conversationId]);

    useEffect(() => { refresh(); setSeen(readSeen(conversationId)); }, [refresh, conversationId]);

    useEffect(() => {
        if (typeof document === 'undefined') return;
        const onChanged = (e: Event) => {
            if ((e as CustomEvent).detail?.conversationId === conversationId) refresh();
        };
        document.addEventListener(REFERENCES_CHANGED_EVENT, onChanged);
        return () => document.removeEventListener(REFERENCES_CHANGED_EVENT, onChanged);
    }, [conversationId, refresh]);

    // Writes re-fetch through the event so every mounted line (sidebar bar,
    // collapsed strip) moves together.
    const after = useCallback(() => { if (conversationId) announceReferencesChanged(conversationId); }, [conversationId]);

    const pin = useCallback(async (locator: string, title?: string) => {
        if (!projectId || !conversationId) return null;
        const ref = await pinReference(projectId, conversationId, locator, title);
        after();
        return ref;
    }, [projectId, conversationId, after]);

    const promote = useCallback(async (refId: string) => {
        if (!projectId || !conversationId) return null;
        const ref = await promoteReference(projectId, conversationId, refId);
        after();
        return ref;
    }, [projectId, conversationId, after]);

    const unpin = useCallback(async (refId: string) => {
        if (!projectId || !conversationId) return false;
        await unpinReference(projectId, conversationId, refId);
        after();
        return true;
    }, [projectId, conversationId, after]);

    const pickedUp = view?.summary.picked_up ?? 0;
    const markSeen = useCallback(() => {
        if (!conversationId) return;
        try { localStorage.setItem(seenKey(conversationId), String(pickedUp)); } catch { /* private mode */ }
        setSeen(pickedUp);
    }, [conversationId, pickedUp]);

    return {
        view,
        references: view?.references ?? [],
        summary: view?.summary ?? { pinned: 0, picked_up: 0, useful: 0, faded: 0 },
        count: view ? view.summary.pinned + view.summary.picked_up : 0,
        hasNew: pickedUp > seen,
        refresh, pin, promote, unpin, markSeen,
    };
}
