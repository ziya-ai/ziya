/**
 * Single mutation path for conversation METADATA changes.
 *
 * Problem this solves: conversation persistence historically had four
 * distinct write paths (queueSave fast path, the dirty-id flusher, direct
 * db.saveConversation calls, and ad-hoc bulkSync calls), each with
 * different sync semantics.  Mutations that used a path which skipped the
 * server push (e.g. sidebar rename) were silently reverted by the next
 * periodic sync whenever a server-side _version bump (bead writes,
 * context_add_file) made the server record look newer.
 *
 * Every metadata mutation (title, folderId, pin, etc.) must follow the
 * same contract, enforced here:
 *   1. hydrate the FULL record from IndexedDB (sidebar state holds
 *      content-stripped shells — never authoritative for messages)
 *   2. apply the patch and bump _version
 *   3. persist to IndexedDB
 *   4. broadcast cross-tab so same-project tabs converge
 *   5. push to the server immediately, so subsequent server-side writes
 *      carry the new metadata forward instead of reverting it
 *
 * This module is deliberately framework-free (no React imports) so it can
 * be unit-tested in isolation.  Callers are responsible for updating React
 * state — use the returned merged record so state, IDB, and server agree.
 */
import { db } from './db';
import { projectSync } from './projectSync';
import * as syncApi from '../api/conversationSyncApi';
import type { Conversation } from './types';

export interface MutateOptions {
    /** Project to push to when the record itself has no projectId. */
    projectId?: string;
    /**
     * Set false to skip the server push (e.g. ephemeral-mode callers).
     * IDB write and cross-tab broadcast still happen.
     */
    pushToServer?: boolean;
    /**
     * Fallback record used when IndexedDB has no entry for the id
     * (e.g. a brand-new conversation not yet persisted).  Must be a FULL
     * record — shells are rejected.
     */
    fallback?: Conversation;
}

export interface MutateResult {
    ok: boolean;
    /** The merged full record after the patch — use to update React state. */
    conversation?: Conversation;
    /** True when the server accepted the push this call. */
    serverPushed: boolean;
    error?: unknown;
}

/** Mirror of the queueSave dual-write guard: empty "New Conversation"
 *  shells must never reach the server (isEmptyShell skip-race, see
 *  ChatContext).  Exported for tests. */
export function isPushableToServer(conv: Conversation): boolean {
    if ((conv as any).isEphemeral) return false;
    if ((conv as any)._isShell) return false;
    if (conv.isActive === false) return false;
    if (conv.title === 'New Conversation' && (!conv.messages || conv.messages.length === 0)) return false;
    return true;
}

/**
 * Apply a metadata patch to a conversation through the unified pipeline.
 *
 * The patch must NOT contain `messages` — message writes belong to the
 * queueSave pipeline, which owns streaming coalescing and shell guards.
 * This function throws synchronously on such misuse so the bug is caught
 * in development rather than silently corrupting history.
 */
export async function mutateConversationMeta(
    conversationId: string,
    patch: Partial<Conversation>,
    opts: MutateOptions = {}
): Promise<MutateResult> {
    if ('messages' in patch) {
        throw new Error(
            'mutateConversationMeta: patch must not contain `messages` — '
            + 'use the queueSave pipeline for message writes'
        );
    }

    // 1. Hydrate the full record.  React-state sidebar entries are shells
    //    (content stripped), so IDB is the source of truth for the body.
    let full: Conversation | null = null;
    try {
        full = await db.getConversation(conversationId);
    } catch (e) {
        console.warn('mutateConversationMeta: IDB read failed:', e);
    }
    if (!full && opts.fallback && !(opts.fallback as any)._isShell) {
        full = opts.fallback;
    }
    if (!full) {
        return { ok: false, serverPushed: false, error: new Error(`conversation ${conversationId} not found`) };
    }

    // 2. Apply patch + version bump.  _version is the sync tie-breaker:
    //    bumping it here is what lets the periodic sync's push side win
    //    over a stale server copy.
    const merged: Conversation = {
        ...full,
        ...patch,
        id: conversationId,
        _version: Date.now(),
    } as Conversation;

    // 3. Persist to IndexedDB.  saveConversation's shell guard is a
    //    no-op here since `merged` is a full record.
    try {
        await db.saveConversation(merged);
    } catch (e) {
        // IDB failure is fatal for the mutation — don't push a state to
        // the server that local storage doesn't hold.
        return { ok: false, serverPushed: false, error: e };
    }

    // 4. Cross-tab broadcast (same payload shape as queueSave's fast path).
    try {
        projectSync.post('conversations-changed', { ids: [conversationId] });
    } catch { /* BroadcastChannel unavailable — non-fatal */ }

    // 5. Immediate server push.  Without this, any server-side _version
    //    bump during a chat turn reverts the mutation at the next sync.
    let serverPushed = false;
    const pushProjectId = (merged as any).projectId || opts.projectId;
    if (opts.pushToServer !== false && pushProjectId && isPushableToServer(merged)) {
        try {
            await syncApi.bulkSync(pushProjectId, [
                syncApi.conversationToServerChat(merged, pushProjectId),
            ]);
            serverPushed = true;
        } catch (e) {
            // Non-fatal: IDB is authoritative locally and the periodic
            // sync's push side will retry (merged._version is now newer
            // than the server's).
            console.warn('mutateConversationMeta: server push failed (will retry on next sync):', e);
        }
    }

    return { ok: true, conversation: merged, serverPushed };
}
