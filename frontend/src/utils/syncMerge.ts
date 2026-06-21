/**
 * Pure decision core of the periodic server-sync pull merge.
 *
 * Extracted from ChatContext so the version/count comparison logic — where
 * every sync bug so far has lived — is unit-testable in isolation.  These
 * functions are PURE: no refs, no React, no I/O.  ChatContext remains the
 * orchestrator (fetching, refs, commit, hydration); it feeds plain values
 * in and applies the returned decisions.
 *
 * Behavior is copied verbatim from the inline implementation; comments are
 * preserved because they document hard-won invariants.
 */

/** Server chat summary as returned by syncApi.listChats(projectId, false). */
export interface ServerChatSummary {
    id: string;
    title?: string;
    projectId?: string;
    groupId?: string | null;
    folderId?: string | null;
    delegateMeta?: any;
    isGlobal?: boolean;
    lastActiveAt?: number;
    messageCount?: number;
    openBeadCount?: number;
    openWorkItemCount?: number;
    _version?: number;
}

/** Local conversation shell (from db.getConversationShells). */
export interface LocalShell {
    id: string;
    title?: string;
    messages?: any[];
    folderId?: string | null;
    projectId?: string;
    delegateMeta?: any;
    isGlobal?: boolean;
    lastAccessedAt?: number;
    lastActiveAt?: number;
    _isShell?: boolean;
    _fullMessageCount?: number;
    openBeadCount?: number;
    openWorkItemCount?: number;
    _version?: number;
}

// ---------------------------------------------------------------------------
// Fetch decision: does this server chat need a full-body fetch?
// ---------------------------------------------------------------------------

export interface FetchDecisionCtx {
    /** sc.id === the conversation the user is actively viewing (polling only). */
    isActiveConv: boolean;
    /** sc.id is in recentlyFetchedFullIds (already fetched this session). */
    alreadyFetchedThisSession: boolean;
}

/**
 * Decide whether a server chat needs its full body fetched.
 * Mirrors the per-chat body of the needFullFetch loop.
 */
export function shouldFetchFull(
    sc: ServerChatSummary,
    local: LocalShell | undefined,
    ctx: FetchDecisionCtx
): boolean {
    if (!local) {
        // Server-only conversation (new from another instance).
        // Skip if we already fetched full data this session
        // (React.startTransition may not have committed yet).
        return !ctx.alreadyFetchedThisSession;
    }
    if (ctx.isActiveConv) {
        // The active conversation is authoritative in React state during
        // polling.  Fetching stale server data just creates merge risk.
        // Metadata (delegateMeta, title) is updated via summary.
        return false;
    }
    // Always fetch full data if server has delegate metadata
    // or folder assignment that local is missing.
    const serverHasDelegateMeta = sc.delegateMeta && !local.delegateMeta;
    const serverHasFolder = (sc.groupId || sc.folderId) && !local.folderId;
    if (serverHasDelegateMeta || serverHasFolder) {
        return !ctx.alreadyFetchedThisSession;
    }
    const serverVer = sc._version || sc.lastActiveAt || 0;
    // Shell conversations have _version: undefined, making them appear
    // stale on every sync cycle.  Treat them as current to prevent
    // repeated full fetches before lazy-load completes.
    //
    // Exception: if the shell reports _fullMessageCount === 0 but the
    // server's summary says messageCount > 0, the local IDB record is
    // genuinely empty and the server has the real data.  Pin localVer
    // to 0 so the comparison below forces a pull.  Without this, a
    // wiped-local/populated-server state is a permanent trap:
    // localVer=Infinity blocks the pull forever.
    const localFullCount = local._fullMessageCount;
    const serverSummaryMsgs = typeof sc.messageCount === 'number' ? sc.messageCount : 0;
    const emptyLocalPopulatedServer = local._isShell && localFullCount === 0 && serverSummaryMsgs > 0;
    const localVer = emptyLocalPopulatedServer
        ? 0
        : (local._isShell ? Infinity : (local._version || local.lastAccessedAt || 0));
    // Symmetric message-count divergence check (mirror of the push-side
    // filter).  If server reports strictly more messages than we have
    // locally, fetch — even if versions match.  Without this, a local
    // copy that fell behind the server with coincident _version stays
    // permanently behind.  Shells are excluded (they intentionally carry
    // a reduced message count until lazy-load completes).
    const localMsgCount = local._isShell
        ? Infinity
        : (Array.isArray(local.messages) ? local.messages.length : 0);
    const countDiverged = serverSummaryMsgs > localMsgCount;
    const versionDiverged = serverVer > localVer;
    if (countDiverged || versionDiverged) {
        // For version divergence, skip if already fetched this session.
        // For count divergence, always fetch — local state is behind
        // right now regardless of what was fetched earlier this session.
        if (versionDiverged && !countDiverged && ctx.alreadyFetchedThisSession) {
            return false;
        }
        return true;
    }
    return false;
}

// ---------------------------------------------------------------------------
// Merge decision: which record wins for one server chat?
// ---------------------------------------------------------------------------

export interface MergeDecisionCtx {
    projectId: string;
    /** sc.id === currentConversationRef.current */
    isActiveConv: boolean;
    /** Date.now() at sync time (injected for testability). */
    now: number;
    /** Age threshold for staging stale empty shells for server-side GC. */
    staleShellAgeMs: number;
}

export type MergeDecision =
    /** Empty "New Conversation" shell from the server — drop it.
     *  staleDeleteEligible: caller may stage a server-side delete
     *  (subject to its per-cycle cap and attempted-id dedup). */
    | { action: 'skip-empty-shell'; staleDeleteEligible: boolean }
    /** Use this record in the merged map. */
    | { action: 'set'; record: any }
    /** Local copy wins — leave the merged map entry as-is. */
    | { action: 'keep-local' };

/**
 * Decide the merge outcome for one server chat against the local copy.
 * Mirrors the per-chat body of the three-way-merge forEach.
 */
export function mergeServerChat(
    sc: ServerChatSummary,
    local: LocalShell | undefined,
    full: any | undefined,
    ctx: MergeDecisionCtx
): MergeDecision {
    const serverVersion = sc._version || 0;
    const localVersion = local?._version || 0;

    if (!local) {
        // Skip empty "New Conversation" shells from the server.  These are
        // stale empties that the GC purged locally; re-importing them
        // defeats the cleanup.
        // Exception: if the shell IS the user's active conversation,
        // dropping it strands currentConversationId pointing at a
        // conversation that's not in state.
        const isEmptyShell = sc.title === 'New Conversation'
            && (!full?.messages || full.messages.length === 0);
        if (isEmptyShell && !ctx.isActiveConv) {
            // Stage for server-side delete if this empty shell belongs to
            // the current project (don't delete cross-project globals) and
            // is stale enough that no live tab is mid-creation.
            const shellProjectId = sc.projectId || ctx.projectId;
            const shellAge = ctx.now - (sc.lastActiveAt || 0);
            return {
                action: 'skip-empty-shell',
                staleDeleteEligible: shellProjectId === ctx.projectId
                    && shellAge > ctx.staleShellAgeMs,
            };
        }
        if (full) {
            return {
                action: 'set',
                record: {
                    ...full,
                    _isShell: false,
                    _fullMessageCount: undefined,
                    projectId: full.projectId || ctx.projectId,
                    folderId: full.groupId || full.folderId || sc.groupId || sc.folderId || null,
                    delegateMeta: full.delegateMeta || null,
                    lastAccessedAt: full.lastAccessedAt || full.lastActiveAt,
                    isActive: full.isActive !== false,
                    _version: full._version || ctx.now,
                    openBeadCount: sc.openBeadCount ?? 0,
                    openWorkItemCount: sc.openWorkItemCount ?? 0,
                },
            };
        }
        // Server-only conversation, full fetch deferred (or failed).
        // Add as a SHELL with the server's _version so the sidebar
        // populates immediately.  Marking _isShell prevents the IDB write
        // step from saving a zero-message record (FAST_PATH_TOMBSTONE)
        // and prevents the push step from sending it back to the server.
        if (!isEmptyShell) {
            return {
                action: 'set',
                record: {
                    id: sc.id,
                    title: sc.title || 'Loading...',
                    messages: [],
                    _isShell: true,
                    _fullMessageCount: typeof sc.messageCount === 'number' ? sc.messageCount : 0,
                    projectId: sc.projectId || ctx.projectId,
                    folderId: sc.groupId || sc.folderId || null,
                    lastAccessedAt: sc.lastActiveAt || 0,
                    isActive: true,
                    isGlobal: sc.isGlobal ?? false,
                    _version: serverVersion,
                    openBeadCount: sc.openBeadCount ?? 0,
                    openWorkItemCount: sc.openWorkItemCount ?? 0,
                },
            };
        }
        // Empty shell that IS the active conversation with no full body:
        // fall through to keep-local (matches inline behavior, where
        // neither set() branch fired).
        return { action: 'keep-local' };
    }

    if (serverVersion > localVersion) {
        // Server is newer — use full-fetched data if available,
        // otherwise update metadata only from summary.
        if (full) {
            // Message-count guard: if the server has fewer messages than
            // local, keep local messages but update metadata from server.
            // This prevents partial syncs from destroying conversation
            // history.  For shell entries (messages stripped for memory),
            // _fullMessageCount carries the real on-disk count.
            const localMsgCount = local._isShell
                ? (local._fullMessageCount || 0)
                : (local.messages?.length || 0);
            const serverMsgCount = full.messages?.length || 0;
            const merged = { ...full };
            if (serverMsgCount < localMsgCount && localMsgCount > 2) {
                console.warn(`🛡️ SYNC_GUARD: Keeping ${localMsgCount} local messages for ${sc.id?.substring(0, 8)} (server had ${serverMsgCount})`);
                merged.messages = local.messages;
            }
            return {
                action: 'set',
                record: {
                    ...merged,
                    _isShell: false,
                    _fullMessageCount: undefined,
                    projectId: merged.projectId || ctx.projectId,
                    folderId: merged.groupId || merged.folderId || null,
                    delegateMeta: merged.delegateMeta || null,
                    lastAccessedAt: merged.lastAccessedAt || merged.lastActiveAt,
                    isActive: merged.isActive !== false,
                    _version: merged._version || ctx.now,
                    openBeadCount: sc.openBeadCount ?? 0,
                    openWorkItemCount: sc.openWorkItemCount ?? 0,
                },
            };
        }
        // Summary-only update (full fetch wasn't needed or failed).
        // 'local' came from getConversationShells() which strips
        // 'messages', so we must preserve '_isShell' on the merged entry —
        // otherwise the saveConversations step will write it as a real
        // (empty-messages) record and trigger FAST_PATH_TOMBSTONE on every
        // sync cycle.  'isGlobal' is authoritative on the server: a chat
        // marked global on disk must render with the global label in every
        // project regardless of whether IDB has caught up.
        return {
            action: 'set',
            record: {
                ...local,
                title: sc.title || local.title,
                projectId: sc.projectId || local.projectId || ctx.projectId,
                folderId: sc.groupId || sc.folderId || local.folderId || null,
                lastActiveAt: sc.lastActiveAt || local.lastActiveAt,
                isGlobal: sc.isGlobal ?? local.isGlobal,
                _version: serverVersion,
                _isShell: local._isShell,
                openBeadCount: sc.openBeadCount ?? local.openBeadCount ?? 0,
                openWorkItemCount: sc.openWorkItemCount ?? local.openWorkItemCount ?? 0,
            },
        };
    }

    // Versions tie or local is newer → local wins for CONTENT.  But the
    // open-work counts are server-derived signals recomputed fresh in the
    // summary path (from _beads / the fallback store) and are NOT versioned:
    // parking a bead writes the fallback store without bumping the chat
    // record's _version, so serverVersion never exceeds localVersion and the
    // version-newer branch above never fires.  Without this overlay the
    // correct count is discarded on every cycle and the sidebar indicator
    // never appears.  Overlay the counts onto the otherwise-untouched local
    // record when they diverge; otherwise keep-local to avoid per-cycle
    // state churn (once corrected, the counts match and this is a no-op).
    const scBead = sc.openBeadCount ?? 0;
    const scWork = sc.openWorkItemCount ?? 0;
    if (scBead !== (local.openBeadCount ?? 0) || scWork !== (local.openWorkItemCount ?? 0)) {
        return {
            action: 'set',
            record: {
                ...local,
                openBeadCount: scBead,
                openWorkItemCount: scWork,
            },
        };
    }

    return { action: 'keep-local' };
}

// ---------------------------------------------------------------------------
// Reference-reuse predicate for the post-merge React-state commit.
// ---------------------------------------------------------------------------

/**
 * Decide whether the previous React-state object for a conversation can be
 * reused (reference-preserved) instead of adopting the freshly merged record.
 *
 * This is a render-perf optimization: reusing the prev reference when nothing
 * user-visible changed prevents a re-render cascade through the sidebar's
 * memoized tree.  But it must compare EVERY field the sidebar renders —
 * including the open-work counts, which are server-derived signals that change
 * WITHOUT a _version bump (parking a bead writes the fallback bead store, not
 * the chat record, so _version is unchanged).  A version-gated comparison
 * therefore can't see a count change; omitting the explicit count check here
 * reused the stale prev object and the corrected count from the merge was
 * silently discarded — the sidebar bead/work indicator never updated.
 *
 * Returns true when `existing` is safe to reuse (no observable change).
 */
export function canReusePrevConversation(mc: any, existing: any): boolean {
    return Boolean(existing)
        && (mc._version || 0) <= (existing._version || 0)
        && (mc.messages?.length || 0) <= (existing.messages?.length || 0)
        && mc.title === existing.title
        && mc.folderId === existing.folderId
        && mc.isGlobal === existing.isGlobal
        && mc.delegateMeta?.status === existing.delegateMeta?.status
        && mc.hasUnreadResponse === existing.hasUnreadResponse
        && (mc.openBeadCount || 0) === (existing.openBeadCount || 0)
        && (mc.openWorkItemCount || 0) === (existing.openWorkItemCount || 0);
}