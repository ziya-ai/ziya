/**
 * Single source of truth for resolving a conversation's FULL message list.
 *
 * The sidebar renders conversations from getConversationShells() — records
 * whose message bodies are stripped (content: '') to save memory.  Any UI
 * that operates on an *arbitrarily-selected* row (not the active chat, which
 * ChatContext keeps hydrated) therefore risks seeing empty messages: the
 * export modal showed "0 rounds", the info modal showed "0 chars", and a
 * fork of a never-opened conversation silently produced empty history.
 *
 * This helper resolves messages in order local → IDB → server, gating on
 * real CONTENT length (not array length — a shell has entries with empty
 * content), mirroring the active-chat lazy-loader in ChatContext.
 *
 * Callers decide how to treat an empty/failed result:
 *   - export / info: degrade to an empty view AND surface the error state
 *     (result.source === 'empty' / result.error set).
 *   - fork: treat empty as a HARD failure (never persist a truncated fork).
 */
import type { Conversation, Message } from './types';
import { db } from './db';
import * as syncApi from '../api/conversationSyncApi';

export type HydrationSource = 'local' | 'idb' | 'server' | 'empty';

export interface HydrationResult {
    messages: Message[];
    /** projectId resolved during hydration (from local/IDB record), if any. */
    projectId?: string;
    /** Which tier satisfied the request. 'empty' = nothing had content. */
    source: HydrationSource;
    /** Set when the server fetch was attempted and threw. */
    error?: unknown;
}

/** Sum of string-content length across messages (shells sum to 0). */
function contentLength(messages: Message[] | undefined | null): number {
    if (!Array.isArray(messages)) return 0;
    let n = 0;
    for (const m of messages) {
        if (typeof m?.content === 'string') n += m.content.length;
    }
    return n;
}

/**
 * Resolve a conversation's full messages, hydrating from IDB then the server
 * when the provided/local record is a shell or absent.
 *
 * @param conversationId  bare id (no 'conv-' prefix)
 * @param opts.local      an in-hand record (e.g. sidebar state) to try first
 * @param opts.projectId  fallback project id when the record lacks one
 */
export async function hydrateConversationMessages(
    conversationId: string,
    opts: { local?: Conversation | null; projectId?: string } = {},
): Promise<HydrationResult> {
    // 1. In-hand record (sidebar state) — may be a full record already.
    let messages: Message[] = Array.isArray(opts.local?.messages)
        ? (opts.local!.messages as Message[])
        : [];
    let projectId: string | undefined = opts.local?.projectId || opts.projectId;
    if (contentLength(messages) > 0) {
        return { messages, projectId, source: 'local' };
    }

    // 2. IndexedDB full record.
    try {
        const rec = await db.getConversation(conversationId);
        if (rec) {
            projectId = rec.projectId || projectId;
            const idbMsgs = Array.isArray(rec.messages) ? (rec.messages as Message[]) : [];
            if (contentLength(idbMsgs) > 0) {
                return { messages: idbMsgs, projectId, source: 'idb' };
            }
            // Keep the shell's (empty) messages only as a last-resort shape.
            messages = idbMsgs;
        }
    } catch {
        /* fall through to server */
    }

    // 3. Server (the conversation may live only on the server — never opened).
    if (projectId) {
        try {
            const serverChat = await syncApi.getChat(projectId, conversationId);
            const srvMsgs = (serverChat?.messages as Message[] | undefined) ?? [];
            if (srvMsgs.length > 0) {
                return { messages: srvMsgs, projectId, source: 'server' };
            }
        } catch (error) {
            // Surface the failure so callers can distinguish "genuinely empty"
            // from "couldn't reach the server" (fork hard-fails on this).
            return { messages, projectId, source: 'empty', error };
        }
    }

    return { messages, projectId, source: 'empty' };
}
