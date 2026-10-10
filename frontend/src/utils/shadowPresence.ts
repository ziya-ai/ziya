/**
 * Is a `ziya shadow` terminal in play for the active conversation?
 *
 * A terminal pane only needs its LOCAL tag when a REMOTE pane could appear
 * beside it; with no shadow in play every command ran locally and the tag
 * is noise.  "In play" means either:
 *
 *   offered  a shadow session is attached to this conversation (published by
 *            useAttachedShadowSessions, the poller behind the input-box chip,
 *            so there is one poll per window, not one per tool block), or
 *   used     the conversation contains a shadow tool call: found in its saved
 *            messages, or marked when a remote pane mounts (which also covers
 *            a call still streaming).
 *
 * Unattached live sessions elsewhere on the machine do not count.
 */
import { useMemo, useSyncExternalStore } from 'react';
import { useActiveChatOptional } from '../context/ActiveChatContext';

const attached = new Set<string>();
const used = new Set<string>();
const listeners = new Set<() => void>();
let version = 0;

const emit = () => { version++; listeners.forEach(l => l()); };
const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l); }; };
const getVersion = () => version;

export function publishAttachedShadow(conversationId: string, hasSessions: boolean): void {
    if (attached.has(conversationId) === hasSessions) return;
    if (hasSessions) attached.add(conversationId); else attached.delete(conversationId);
    emit();
}

export function markShadowUsed(conversationId: string): void {
    if (used.has(conversationId)) return;
    used.add(conversationId);
    emit();
}

export function isShadowInPlay(conversationId: string): boolean {
    return attached.has(conversationId) || used.has(conversationId);
}

/** Tests only. */
export function resetShadowPresence(): void {
    attached.clear();
    used.clear();
    emit();
}

const SHADOW_FENCE_RE = /`{3,}tool:(?:mcp_)?shadow_\w+/;

export function contentUsesShadow(content: unknown): boolean {
    return typeof content === 'string' && SHADOW_FENCE_RE.test(content);
}

// Every tool block calls the hook; scan a given messages array once.
const scanCache = new WeakMap<object, boolean>();
function messagesUseShadow(messages: ReadonlyArray<{ content?: unknown }> | undefined): boolean {
    if (!messages) return false;
    let hit = scanCache.get(messages);
    if (hit === undefined) {
        hit = messages.some(m => contentUsesShadow(m?.content));
        scanCache.set(messages, hit);
    }
    return hit;
}

export interface ShadowPresence { key: string; inPlay: boolean; }

export function useShadowPresence(): ShadowPresence {
    const chat = useActiveChatOptional();
    const key = chat?.currentConversationId ?? '';
    const messages = chat?.currentMessages;
    const inMessages = useMemo(() => messagesUseShadow(messages as any), [messages]);
    const v = useSyncExternalStore(subscribe, getVersion, getVersion);
    // `v` is a dependency so the store read below re-runs on every change.
    const inStore = useMemo(() => isShadowInPlay(key), [key, v]);
    return { key, inPlay: inMessages || inStore };
}
