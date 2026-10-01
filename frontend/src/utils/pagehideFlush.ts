/**
 * Flush the dual-write debounce's pending set when the page is going away.
 *
 * ChatContext writes to IndexedDB immediately but pushes to the server on a
 * 2s debounce.  Close the tab inside that window and the timer dies with
 * it: the conversation exists only in this browser's IndexedDB, and the
 * "next sync will retry" assumption needs a tab that no longer exists.  Now
 * that IndexedDB is scoped per server instance with no adoption path, "it
 * reached the server" is what makes a cache droppable, so this is the most
 * common way a conversation could still end up cache-only.
 *
 * Scope is deliberately narrow: the SAME dirty set the debounce accumulated,
 * through the SAME filter, to the SAME endpoint with the SAME server-side
 * version/regression guards.  No new judgement about what is "dirty".  The
 * only difference is `keepalive: true`, which lets the browser finish the
 * request after the document is torn down.
 */

import { isInstanceHalted } from './instanceIdentity';

/** Chromium/WebKit cap on in-flight keepalive body bytes per page. */
export const KEEPALIVE_BODY_LIMIT = 64 * 1024;

type ConvLike = {
    id: string;
    isActive?: boolean;
    _isShell?: boolean;
    title?: string;
    messages?: unknown[];
    projectId?: string;
};

/**
 * The dual-write push filter, extracted so the debounce timers and the
 * pagehide flush cannot drift apart.  Keeps: ids in the batch, active, not a
 * shell, and not an empty "New Conversation" (an empty shell on the server
 * trips the isEmptyShell skip race on later syncs).
 */
export function selectDirtyConversations<T extends ConvLike>(
    conversations: readonly T[],
    batchIds: ReadonlySet<string>,
): T[] {
    return conversations.filter(c =>
        batchIds.has(c.id)
        && c.isActive !== false
        && !c._isShell
        && !(c.title === 'New Conversation' && (!c.messages || c.messages.length === 0)),
    );
}

export type FlushOutcome =
    | { kind: 'nothing' }
    | { kind: 'halted' }
    | { kind: 'too-large'; bytes: number }
    | { kind: 'sent'; projects: number; conversations: number };

export interface FlushDeps {
    /** Live conversation state (conversationsRef.current). */
    conversations: readonly any[];
    /** pendingDirtyIdsRef.current — cleared by this call once consumed. */
    pendingIds: Set<string>;
    /** Project the tab is on; per-conversation projectId wins when set. */
    fallbackProjectId: string | null | undefined;
    toServerChat: (conv: any, projectId: string) => unknown;
    fetchFn?: typeof fetch;
}

/**
 * Send the pending dirty set with keepalive.  Fire-and-forget by nature: the
 * page cannot observe the result, so the return value describes only what
 * was handed to the browser.  Never throws -- a pagehide handler that throws
 * accomplishes nothing and can mask other handlers.
 */
export function flushPendingDirtyOnPagehide(deps: FlushDeps): FlushOutcome {
    const { conversations, pendingIds, fallbackProjectId, toServerChat } = deps;
    const fetchFn = deps.fetchFn ?? (typeof fetch === 'function' ? fetch : undefined);
    if (pendingIds.size === 0 || !fallbackProjectId || !fetchFn) return { kind: 'nothing' };
    if (isInstanceHalted()) return { kind: 'halted' };

    const batch = new Set(pendingIds);
    const dirty = selectDirtyConversations(conversations, batch);
    if (dirty.length === 0) {
        pendingIds.clear();
        return { kind: 'nothing' };
    }

    const byProject = new Map<string, unknown[]>();
    for (const c of dirty) {
        const pid = c.projectId || fallbackProjectId;
        if (!byProject.has(pid)) byProject.set(pid, []);
        byProject.get(pid)!.push(toServerChat(c, pid));
    }

    const bodies: Array<[string, string]> = [];
    let totalBytes = 0;
    for (const [pid, chats] of byProject) {
        const body = JSON.stringify({ chats });
        totalBytes += body.length;
        bodies.push([pid, body]);
    }
    if (totalBytes > KEEPALIVE_BODY_LIMIT) {
        // Browsers reject oversized keepalive requests synchronously; there
        // is no partial send.  Leave the ids pending (a bfcache resume or the
        // next periodic sync can still pick them up) and say so honestly.
        console.warn(`📡 PAGEHIDE_FLUSH: ${totalBytes} bytes exceeds keepalive limit; not sent`);
        return { kind: 'too-large', bytes: totalBytes };
    }

    const projectPath = (window as any).__ZIYA_CURRENT_PROJECT_PATH__;
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (projectPath) headers['X-Project-Root'] = projectPath;

    let sent = 0;
    for (const [pid, body] of bodies) {
        try {
            fetchFn(`/api/v1/projects/${pid}/chats/bulk-sync`, {
                method: 'POST', headers, body, keepalive: true,
            }).catch(() => { /* page is gone; nothing to do */ });
            sent += 1;
        } catch (e) {
            console.warn('📡 PAGEHIDE_FLUSH: fetch refused', e);
        }
    }
    if (sent === 0) return { kind: 'nothing' };
    // Consumed: a bfcache resume must not re-send the same batch.  (A
    // duplicate would be harmless -- the server's version guard skips it --
    // but it would also count against the keepalive budget.)
    pendingIds.clear();
    console.debug(`📡 PAGEHIDE_FLUSH: sent ${dirty.length} conversation(s) across ${sent} project(s)`);
    return { kind: 'sent', projects: sent, conversations: dirty.length };
}
