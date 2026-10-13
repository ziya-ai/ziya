/**
 * Durable store for messages SHELL_GUARD holds back.
 *
 * When shell recovery cannot establish a conversation's full history it
 * holds queued messages instead of appending them to a possibly-truncated
 * list (ChatContext addMessageToConversation).  Human text goes back to the
 * composer; a streamed assistant turn has nowhere to go and used to live
 * only in the in-memory pendingShellMessages ref, so a reload discarded it
 * (2026-10-02: a 38-iteration, 13-minute agent turn).  This keeps those
 * messages in localStorage, per conversation, until a recovery succeeds and
 * applies them.
 */

const PREFIX = 'ZIYA_HELD_MESSAGES:';
// localStorage is ~5 MB per origin; refuse rather than evict everything else.
const MAX_CHARS = 2_000_000;

export function heldMessagesKey(conversationId: string): string {
    return PREFIX + conversationId;
}

function store(): Storage | null {
    try {
        return typeof window !== 'undefined' ? window.localStorage : null;
    } catch {
        return null;
    }
}

export function loadHeldMessages<T = any>(conversationId: string): T[] {
    const s = store();
    if (!s) return [];
    try {
        const raw = s.getItem(heldMessagesKey(conversationId));
        const parsed = raw ? JSON.parse(raw) : null;
        return Array.isArray(parsed) ? (parsed as T[]) : [];
    } catch {
        return [];
    }
}

/** Replace the held set (an empty list clears it).  Returns whether it was stored. */
export function saveHeldMessages(conversationId: string, messages: unknown[]): boolean {
    const s = store();
    if (!s) return false;
    const key = heldMessagesKey(conversationId);
    try {
        if (messages.length === 0) {
            s.removeItem(key);
            return true;
        }
        const json = JSON.stringify(messages);
        if (json.length > MAX_CHARS) {
            console.error(
                `HELD_STORE: ${messages.length} held message(s) for ${conversationId.substring(0, 8)} ` +
                `are ${json.length} chars, over the ${MAX_CHARS} limit; not persisted`);
            return false;
        }
        s.setItem(key, json);
        return true;
    } catch (e) {
        console.error('HELD_STORE: could not persist held messages:', e);
        return false;
    }
}

export function clearHeldMessages(conversationId: string): void {
    try {
        store()?.removeItem(heldMessagesKey(conversationId));
    } catch {
        /* storage unavailable */
    }
}

/** Ids of conversations that currently have held messages. */
export function listHeldConversations(): string[] {
    const s = store();
    if (!s) return [];
    const ids: string[] = [];
    for (let i = 0; i < s.length; i++) {
        const k = s.key(i);
        if (k && k.startsWith(PREFIX)) ids.push(k.slice(PREFIX.length));
    }
    return ids;
}
