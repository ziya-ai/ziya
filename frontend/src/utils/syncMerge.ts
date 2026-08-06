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
    branchedFrom?: string;
    branchedAtMessageIndex?: number;
    branchedFromLabel?: string;
    // Triage flags. Unlike the open-work counts below, these are VERSIONED:
    // a flag change goes through mutateConversationMeta, which stamps a new
    // _version. That is why they are adopted only on the server-newer
    // branches and never overlaid onto a keep-local decision.
    flags?: string[];
    flagColor?: string | null;
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
    branchedFrom?: string;
    branchedAtMessageIndex?: number;
    branchedFromLabel?: string;
    flags?: string[];
    flagColor?: string | null;
    _isShell?: boolean;
    _fullMessageCount?: number;
    openBeadCount?: number;
    openWorkItemCount?: number;
    _version?: number;
}

// ---------------------------------------------------------------------------
// Fetch decision: does this server chat need a full-body fetch?
// ---------------------------------------------------------------------------

// How long after a local edit an active conversation is still considered
// "possibly mid-edit" and therefore exempt from the periodic full-fetch.
// Comfortably above the 2s dual-write debounce; well below the 30s poll
// interval, so a genuinely idle-but-open tab still catches up promptly.
const ACTIVE_CONV_IDLE_GRACE_MS = 15_000;

export interface FetchDecisionCtx {
    /** sc.id === the conversation the user is actively viewing (polling only). */
    isActiveConv: boolean;
    /** sc.id is in recentlyFetchedFullIds (already fetched this session). */
    alreadyFetchedThisSession: boolean;
    /** Date.now() at decision time (injected for testability). */
    now?: number;
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
        // The active conversation is authoritative in React state only
        // while an edit could plausibly still be in flight (recent local
        // write, dual-write debounce, etc.) — fetching stale server data
        // during that window just creates merge risk. Metadata
        // (delegateMeta, title) is updated via summary regardless.
        //
        // If the tab has simply had this conversation open and IDLE for a
        // while, there's no in-flight edit to protect: refusing to ever
        // pull lets this tab's local copy silently rot arbitrarily far
        // behind the server (a second tab could have appended dozens of
        // messages), and the next local edit here would overwrite that
        // server state outright. So only suppress the fetch inside the
        // grace window; fall through to the normal comparison otherwise.
        const lastTouch = local.lastAccessedAt || local._version || 0;
        const now = ctx.now ?? Date.now();
        if (!lastTouch || now - lastTouch < ACTIVE_CONV_IDLE_GRACE_MS) {
            return false;
        }
    }
    // Always fetch full data if server has delegate metadata
    // or folder assignment that local is missing.
    const serverHasDelegateMeta = sc.delegateMeta && !local.delegateMeta;
    const serverHasFolder = (sc.groupId || sc.folderId) && !local.folderId;
    // Deliberately "server HAS what local LACKS", not a symmetric
    // divergence check. A symmetric comparison would fire on the far more
    // common case of the LOCAL user having just set a flag (local ahead,
    // push still in flight), re-fetching on every cycle until the push
    // lands — and the merge would discard the result anyway, since local
    // wins on version. This direction only fires when the server genuinely
    // knows something we do not, and self-clears once the merge adopts it.
    //
    // Reachable despite flags being versioned: a coincident _version (two
    // browsers writing in the same millisecond) leaves the flag change with
    // no version signal at all, which is precisely the hole the
    // count-divergence rule below exists to plug for messages.
    const serverHasFlags = (sc.flags?.length ?? 0) > 0
        && (local.flags?.length ?? 0) === 0;
    const serverHasFlagColor = !!sc.flagColor && !local.flagColor;
    if (serverHasDelegateMeta || serverHasFolder
        || serverHasFlags || serverHasFlagColor) {
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
    //
    // Shells produced by getConversationShells() spread the full IDB
    // record, so they carry the record's REAL _version and the true
    // on-disk message count in _fullMessageCount.  Pinning every shell
    // to Infinity discarded both signals and created a second permanent
    // trap: a browser whose IDB fell behind (closed while another
    // instance advanced the conversation) could never detect the
    // divergence and never pulled.  Only fall back to Infinity when the
    // shell genuinely lacks a version (placeholder awaiting hydration).
    const localFullCount = local._fullMessageCount;
    const serverSummaryMsgs = typeof sc.messageCount === 'number' ? sc.messageCount : 0;
    const emptyLocalPopulatedServer = local._isShell && localFullCount === 0 && serverSummaryMsgs > 0;
    const localVer = emptyLocalPopulatedServer
        ? 0
        : (local._isShell
            ? (local._version || local.lastAccessedAt || Infinity)
            : (local._version || local.lastAccessedAt || 0));
    // Symmetric message-count divergence check (mirror of the push-side
    // filter).  If server reports strictly more messages than we have
    // locally, fetch — even if versions match.  Without this, a local
    // copy that fell behind the server with coincident _version stays
    // permanently behind.  Shells compare via _fullMessageCount (the
    // true on-disk count) — their messages array is intentionally
    // reduced and must not be compared directly.
    const localMsgCount = local._isShell
        ? (typeof localFullCount === 'number' ? localFullCount : Infinity)
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
                    branchedFrom: sc.branchedFrom,
                    branchedAtMessageIndex: sc.branchedAtMessageIndex,
                    branchedFromLabel: sc.branchedFromLabel,
                    // This branch enumerates fields rather than spreading, so
                    // an unlisted field is silently dropped — the shell then
                    // renders unflagged until a full fetch happens to land.
                    flags: sc.flags ?? [],
                    flagColor: sc.flagColor ?? null,
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
        //
        // Version stamping is conditional on content currency.  Stamping
        // serverVersion unconditionally onto a record whose content is
        // BEHIND the server (fetch skipped by the active-conv guard or
        // alreadyFetchedThisSession) marks stale content as current:
        // the record then wins version comparisons everywhere — the
        // preservation loop can pin its stale messages, clear the shell
        // markers, and regress IDB — while the missing messages are
        // never pulled.  Keep the local version in that case so the
        // divergence stays visible to shouldFetchFull on later cycles
        // and the pull fires as soon as the guards lift.
        const localContentCount = local._isShell
            ? (local._fullMessageCount
                ?? (Array.isArray(local.messages) ? local.messages.length : 0))
            : (Array.isArray(local.messages) ? local.messages.length : 0);
        const contentBehind = typeof sc.messageCount === 'number'
            && sc.messageCount > localContentCount;
        return {
            action: 'set',
            record: {
                ...local,
                title: sc.title || local.title,
                projectId: sc.projectId || local.projectId || ctx.projectId,
                // Absent-vs-null is load-bearing here (mirror of
                // conversationToServerChat).  The server summary always
                // serializes groupId, so groupId === null is the server
                // EXPLICITLY saying root.  The old falsy || chain fell
                // through a null groupId to the stale local.folderId, and
                // the next push echoed that old folder back to the server
                // — undoing a move-to-root performed in another browser
                // (and re-inheriting global visibility if the old folder
                // was global).  This is the server-newer branch: adopt the
                // server's folder when it said anything at all, and only
                // fall back to local when both fields are genuinely absent.
                folderId: sc.groupId !== undefined ? sc.groupId
                    : sc.folderId !== undefined ? sc.folderId
                    : (local.folderId ?? null),
                lastActiveAt: sc.lastActiveAt || local.lastActiveAt,
                isGlobal: sc.isGlobal ?? local.isGlobal,
                branchedFrom: sc.branchedFrom ?? local.branchedFrom,
                branchedAtMessageIndex: sc.branchedAtMessageIndex ?? local.branchedAtMessageIndex,
                branchedFromLabel: sc.branchedFromLabel ?? local.branchedFromLabel,
                // The server is newer, so its flags win — including an
                // explicit CLEAR. \`??\` and not \`||\`: an empty array (all
                // flags removed) and a null flagColor (color cleared) are
                // real values the server is asserting, and a falsy-fallback
                // chain would restore the stale local value and undo the
                // clear on the very sync meant to propagate it.
                flags: sc.flags ?? local.flags ?? [],
                flagColor: sc.flagColor !== undefined
                    ? sc.flagColor : (local.flagColor ?? null),
                _version: contentBehind ? local._version : serverVersion,
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
        // Both drive a rendered row badge (see chatTreeHash.ts, which hashes
        // the same two fields for the sidebar's tree memo). Omitting them
        // here reuses the stale object and discards the flags the merge just
        // adopted — the same starved-render path as the counts below.
        && (mc.flags || []).join(',') === (existing.flags || []).join(',')
        && (mc.flagColor || '') === (existing.flagColor || '')
        && (mc.openBeadCount || 0) === (existing.openBeadCount || 0)
        && (mc.openWorkItemCount || 0) === (existing.openWorkItemCount || 0);
}
export interface CrossTabMergeCtx {
    /**
     * True when THIS tab is actively streaming into the conversation.
     * During streaming this tab's React state is the ONLY place the
     * in-flight turn exists — queueSave is debounced, so IndexedDB lags
     * behind by design.
     */
    isStreaming: boolean;
}

export type CrossTabMergeDecision =
    /** Local copy wins outright; leave the merged entry untouched. */
    | { action: 'keep-local' }
    /** Remote is newer and safe to adopt wholesale. */
    | { action: 'adopt-remote' }
    /**
     * Adopt remote's metadata but KEEP local's messages.  Used when the
     * remote record is genuinely newer (someone changed a flag, title,
     * folder…) but is message-stale relative to a stream in flight here.
     */
    | { action: 'adopt-metadata-only' };

/**
 * Decide how to merge one remote conversation into the local copy during a
 * CROSS-TAB broadcast (`conversations-changed`).
 *
 * The periodic server sync refuses to run at all while any conversation is
 * streaming (see ChatContext: "the server poll would race with
 * addMessageToConversation / queueSave and clobber in-progress conversation
 * data").  The broadcast receiver had no equivalent protection, so a
 * metadata mutation in another tab — a conversation flag toggle, which
 * calls mutateConversationMeta and stamps `_version: Date.now()` — would
 * re-enter the merge mid-stream and do exactly what that guard exists to
 * prevent.
 *
 * The escape hatch that made it destructive is `localMsgCount <= 2`.  It
 * exists so an authoritative remote record can replace a local stub, and is
 * correct at rest.  But a conversation early in its life is *legitimately*
 * at 1–2 messages while streaming, so a fresher-but-message-stale IDB
 * record satisfied both halves of the condition and replaced live state,
 * dropping the human turn the response was answering.
 *
 * Rather than skip the merge outright (which would make cross-tab flags
 * invisible until the stream ended — the feature the broadcast is for), a
 * streaming conversation adopts remote METADATA and keeps local messages.
 */
export function decideCrossTabMerge(
    local: any | undefined,
    remote: any,
    ctx: CrossTabMergeCtx
): CrossTabMergeDecision {
    if (!local) return { action: 'adopt-remote' };

    // Version gate is unchanged: an older remote is never adopted.
    if ((remote._version || 0) <= (local._version || 0)) {
        return { action: 'keep-local' };
    }

    const localMsgCount = local.messages?.length || 0;
    const remoteMsgCount = remote.messages?.length || 0;

    // Streaming: messages are never taken from remote, at any count.  Even
    // an EQUAL count can differ in content — the committed human turn may
    // be newer than the one IndexedDB holds.
    if (ctx.isStreaming) return { action: 'adopt-metadata-only' };

    if (remoteMsgCount >= localMsgCount || localMsgCount <= 2) {
        return { action: 'adopt-remote' };
    }
    return { action: 'keep-local' };
}

/**
 * Apply a decision of `adopt-metadata-only`: every remote field EXCEPT the
 * message list.  Written as an exclusion rather than a field allowlist so a
 * newly-added metadata field (the next `flags`) propagates cross-tab
 * without needing to be remembered here.
 */
export function adoptMetadataOnly(local: any, remote: any): any {
    return { ...remote, messages: local.messages || [], isActive: local.isActive ?? true };
}