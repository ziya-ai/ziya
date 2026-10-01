/**
 * ✦ pickup store (design/capabilities-hub.md, slice 5).
 *
 * The server emits {type: 'capability_used', key} when the model loads an
 * on-demand skill; chatApi records it here per conversation, and the next
 * fresh send for that conversation clears it (a reattach continues the same
 * turn and keeps it).  Module-level rather than per-hook: the stream reader
 * and the components that show the ✦ are unrelated in the tree.
 */
import { useEffect, useState } from 'react';

export const PICKUP_EVENT = 'ziyaCapabilityPickup';

const byConversation = new Map<string, Set<string>>();
const EMPTY: ReadonlySet<string> = new Set();

function notify(conversationId: string): void {
    if (typeof document === 'undefined') return;
    document.dispatchEvent(new CustomEvent(PICKUP_EVENT, { detail: { conversationId } }));
}

export function markPickedUp(conversationId: string, key: string): void {
    if (!conversationId || !key) return;
    let keys = byConversation.get(conversationId);
    if (!keys) { keys = new Set(); byConversation.set(conversationId, keys); }
    if (keys.has(key)) return;
    keys.add(key);
    notify(conversationId);
}

export function clearPickups(conversationId: string): void {
    if (conversationId && byConversation.delete(conversationId)) notify(conversationId);
}

export function getPickups(conversationId?: string | null): ReadonlySet<string> {
    return (conversationId && byConversation.get(conversationId)) || EMPTY;
}

/** Re-renders when this conversation's pickups change. */
export function useCapabilityPickups(conversationId?: string | null): ReadonlySet<string> {
    const [snapshot, setSnapshot] = useState<ReadonlySet<string>>(() => new Set(getPickups(conversationId)));
    useEffect(() => {
        setSnapshot(new Set(getPickups(conversationId)));
        const onChange = (e: Event) => {
            if ((e as CustomEvent).detail?.conversationId === conversationId) {
                setSnapshot(new Set(getPickups(conversationId)));
            }
        };
        document.addEventListener(PICKUP_EVENT, onChange);
        return () => document.removeEventListener(PICKUP_EVENT, onChange);
    }, [conversationId]);
    return snapshot;
}

/** Test hook. */
export const _resetPickups = (): void => { byConversation.clear(); };
