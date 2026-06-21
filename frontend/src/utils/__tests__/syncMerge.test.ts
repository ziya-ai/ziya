/**
 * Regression tests for the pure sync-merge decision core.
 *
 * Each describe block targets a decision branch; several cases encode
 * specific past production bugs (noted inline).  These functions were
 * extracted from ChatContext's periodic-sync pull merge precisely so this
 * file could exist — keep it in lockstep with utils/syncMerge.ts.
 */
import {
    shouldFetchFull,
    mergeServerChat,
    canReusePrevConversation,
    ServerChatSummary,
    LocalShell,
    MergeDecisionCtx,
} from '../syncMerge';

const NOW = 1_750_000_000_000;
const STALE_AGE = 10 * 60 * 1000; // mirrors STALE_SHELL_AGE_MS semantics

const fetchCtx = (over: Partial<{ isActiveConv: boolean; alreadyFetchedThisSession: boolean }> = {}) => ({
    isActiveConv: false,
    alreadyFetchedThisSession: false,
    ...over,
});

const mergeCtx = (over: Partial<MergeDecisionCtx> = {}): MergeDecisionCtx => ({
    projectId: 'proj-1',
    isActiveConv: false,
    now: NOW,
    staleShellAgeMs: STALE_AGE,
    ...over,
});

const sc = (over: Partial<ServerChatSummary> = {}): ServerChatSummary => ({
    id: 'conv-1',
    title: 'Hello',
    projectId: 'proj-1',
    messageCount: 4,
    lastActiveAt: NOW - 60_000,
    _version: NOW - 60_000,
    ...over,
});

const local = (over: Partial<LocalShell> = {}): LocalShell => ({
    id: 'conv-1',
    title: 'Hello',
    messages: [{}, {}, {}, {}],
    lastAccessedAt: NOW - 30_000,
    _version: NOW - 30_000,
    ...over,
});

describe('shouldFetchFull', () => {
    it('fetches server-only conversations', () => {
        expect(shouldFetchFull(sc(), undefined, fetchCtx())).toBe(true);
    });

    it('skips server-only conversations already fetched this session (startTransition race)', () => {
        expect(shouldFetchFull(sc(), undefined, fetchCtx({ alreadyFetchedThisSession: true }))).toBe(false);
    });

    it('never fetches the active conversation during polling', () => {
        // Active conv is authoritative in React state; server _version newer.
        const server = sc({ _version: NOW, messageCount: 99 });
        expect(shouldFetchFull(server, local(), fetchCtx({ isActiveConv: true }))).toBe(false);
    });

    it('fetches when server has delegateMeta that local lacks', () => {
        expect(shouldFetchFull(sc({ delegateMeta: { role: 'x' } }), local(), fetchCtx())).toBe(true);
    });

    it('fetches when server has folder assignment that local lacks', () => {
        expect(shouldFetchFull(sc({ groupId: 'g1' }), local({ folderId: null }), fetchCtx())).toBe(true);
    });

    it('does not fetch when versions and counts agree', () => {
        const v = NOW - 30_000;
        expect(shouldFetchFull(sc({ _version: v, messageCount: 4 }), local({ _version: v }), fetchCtx())).toBe(false);
    });

    it('fetches when server _version is newer', () => {
        expect(shouldFetchFull(sc({ _version: NOW }), local({ _version: NOW - 30_000 }), fetchCtx())).toBe(true);
    });

    it('skips version-divergence fetch when already fetched this session', () => {
        expect(shouldFetchFull(
            sc({ _version: NOW, messageCount: 4 }),
            local({ _version: NOW - 30_000 }),
            fetchCtx({ alreadyFetchedThisSession: true })
        )).toBe(false);
    });

    it('count divergence forces fetch even if already fetched this session', () => {
        // Local fell behind with coincident _version — permanent-lag bug.
        const v = NOW - 30_000;
        expect(shouldFetchFull(
            sc({ _version: v, messageCount: 10 }),
            local({ _version: v, messages: [{}, {}] }),
            fetchCtx({ alreadyFetchedThisSession: true })
        )).toBe(true);
    });

    it('treats shells as current (no repeated fetch before lazy-load)', () => {
        // Shell _version: undefined used to look stale every cycle.
        expect(shouldFetchFull(
            sc({ _version: NOW, messageCount: 4 }),
            local({ _isShell: true, _version: undefined, _fullMessageCount: 4, messages: [] }),
            fetchCtx()
        )).toBe(false);
    });

    it('empty-local/populated-server shell trap forces a pull', () => {
        // _fullMessageCount === 0 but server has messages: the IDB record
        // was wiped.  localVer=Infinity would block the pull forever.
        expect(shouldFetchFull(
            sc({ _version: NOW - 60_000, messageCount: 7 }),
            local({ _isShell: true, _version: undefined, _fullMessageCount: 0, messages: [] }),
            fetchCtx()
        )).toBe(true);
    });

    it('shells are excluded from count-divergence (reduced count is intentional)', () => {
        expect(shouldFetchFull(
            sc({ _version: NOW - 60_000, messageCount: 50 }),
            local({ _isShell: true, _version: undefined, _fullMessageCount: 50, messages: [{}, {}] }),
            fetchCtx()
        )).toBe(false);
    });
});

describe('mergeServerChat — server-only (no local copy)', () => {
    it('adopts the full-fetched record with normalized fields', () => {
        const full = {
            id: 'conv-1', title: 'Hello', messages: [{}, {}],
            groupId: 'g1', lastActiveAt: NOW - 5_000, _version: NOW - 5_000,
        };
        const d = mergeServerChat(sc(), undefined, full, mergeCtx());
        expect(d.action).toBe('set');
        if (d.action !== 'set') return;
        expect(d.record._isShell).toBe(false);
        expect(d.record.projectId).toBe('proj-1');
        expect(d.record.folderId).toBe('g1');
        expect(d.record.lastAccessedAt).toBe(NOW - 5_000);
        expect(d.record._version).toBe(NOW - 5_000);
        expect(d.record.isActive).toBe(true);
    });

    it('falls back to sc folder fields when full record has none', () => {
        const full = { id: 'conv-1', title: 'Hello', messages: [{}] };
        const d = mergeServerChat(sc({ folderId: 'f9' }), undefined, full, mergeCtx());
        expect(d.action).toBe('set');
        if (d.action !== 'set') return;
        expect(d.record.folderId).toBe('f9');
        expect(d.record._version).toBe(NOW); // ctx.now fallback
    });

    it('creates a shell placeholder when full fetch was deferred', () => {
        const server = sc({ messageCount: 12, _version: NOW - 1_000 });
        const d = mergeServerChat(server, undefined, undefined, mergeCtx());
        expect(d.action).toBe('set');
        if (d.action !== 'set') return;
        expect(d.record._isShell).toBe(true);
        expect(d.record._fullMessageCount).toBe(12);
        expect(d.record.messages).toEqual([]);
        expect(d.record._version).toBe(NOW - 1_000);
        expect(d.record.title).toBe('Hello');
    });

    it('drops empty "New Conversation" shells (GC re-import guard)', () => {
        const server = sc({ title: 'New Conversation', lastActiveAt: NOW - STALE_AGE - 1 });
        const d = mergeServerChat(server, undefined, undefined, mergeCtx());
        expect(d.action).toBe('skip-empty-shell');
        if (d.action !== 'skip-empty-shell') return;
        expect(d.staleDeleteEligible).toBe(true);
    });

    it('empty shell not stale enough → skipped but not delete-eligible', () => {
        const server = sc({ title: 'New Conversation', lastActiveAt: NOW - 1_000 });
        const d = mergeServerChat(server, undefined, undefined, mergeCtx());
        expect(d.action).toBe('skip-empty-shell');
        if (d.action !== 'skip-empty-shell') return;
        expect(d.staleDeleteEligible).toBe(false);
    });

    it('cross-project empty shell → skipped but never delete-eligible', () => {
        const server = sc({
            title: 'New Conversation',
            projectId: 'proj-OTHER',
            lastActiveAt: NOW - STALE_AGE - 1,
        });
        const d = mergeServerChat(server, undefined, undefined, mergeCtx());
        expect(d.action).toBe('skip-empty-shell');
        if (d.action !== 'skip-empty-shell') return;
        expect(d.staleDeleteEligible).toBe(false);
    });

    it('does NOT drop an empty shell that is the active conversation', () => {
        // Dropping it would strand currentConversationId → orphan routing.
        const server = sc({ title: 'New Conversation' });
        const d = mergeServerChat(server, undefined, undefined, mergeCtx({ isActiveConv: true }));
        expect(d.action).toBe('keep-local');
    });

    it('"New Conversation" WITH messages is not treated as an empty shell', () => {
        const full = { id: 'conv-1', title: 'New Conversation', messages: [{}, {}] };
        const d = mergeServerChat(sc({ title: 'New Conversation' }), undefined, full, mergeCtx());
        expect(d.action).toBe('set');
    });
});

describe('mergeServerChat — local copy exists', () => {
    it('keeps local when local _version is newer or equal', () => {
        const d = mergeServerChat(
            sc({ _version: NOW - 60_000, title: 'OLD TITLE' }),
            local({ _version: NOW - 30_000, title: 'Renamed' }),
            undefined,
            mergeCtx()
        );
        expect(d.action).toBe('keep-local');
    });

    it('summary-overlay when server newer and no full body', () => {
        const d = mergeServerChat(
            sc({ _version: NOW, title: 'Server Title', isGlobal: true }),
            local({ _version: NOW - 30_000, title: 'Local Title' }),
            undefined,
            mergeCtx()
        );
        expect(d.action).toBe('set');
        if (d.action !== 'set') return;
        expect(d.record.title).toBe('Server Title');
        expect(d.record.isGlobal).toBe(true);
        expect(d.record._version).toBe(NOW);
        // Local messages untouched by summary overlay
        expect(d.record.messages).toHaveLength(4);
    });

    it('summary-overlay preserves _isShell (FAST_PATH_TOMBSTONE guard)', () => {
        const d = mergeServerChat(
            sc({ _version: NOW }),
            local({ _version: NOW - 30_000, _isShell: true, messages: [] }),
            undefined,
            mergeCtx()
        );
        expect(d.action).toBe('set');
        if (d.action !== 'set') return;
        expect(d.record._isShell).toBe(true);
    });

    it('summary-overlay falls back to local title when server title empty', () => {
        const d = mergeServerChat(
            sc({ _version: NOW, title: '' }),
            local({ _version: NOW - 30_000, title: 'Keep Me' }),
            undefined,
            mergeCtx()
        );
        expect(d.action).toBe('set');
        if (d.action !== 'set') return;
        expect(d.record.title).toBe('Keep Me');
    });

    it('adopts full record when server newer and counts agree', () => {
        const full = {
            id: 'conv-1', title: 'Server Title',
            messages: [{}, {}, {}, {}, {}], _version: NOW,
        };
        const d = mergeServerChat(
            sc({ _version: NOW }),
            local({ _version: NOW - 30_000 }),
            full,
            mergeCtx()
        );
        expect(d.action).toBe('set');
        if (d.action !== 'set') return;
        expect(d.record.messages).toHaveLength(5);
        expect(d.record._isShell).toBe(false);
    });

    it('count guard: keeps local messages when server body is shorter (partial-sync protection)', () => {
        const localMsgs = [{ id: 1 }, { id: 2 }, { id: 3 }, { id: 4 }];
        const full = { id: 'conv-1', title: 'Server Title', messages: [{}], _version: NOW };
        const d = mergeServerChat(
            sc({ _version: NOW }),
            local({ _version: NOW - 30_000, messages: localMsgs }),
            full,
            mergeCtx()
        );
        expect(d.action).toBe('set');
        if (d.action !== 'set') return;
        expect(d.record.messages).toBe(localMsgs);
        // Metadata still comes from server
        expect(d.record.title).toBe('Server Title');
    });

    it('count guard does not fire for tiny conversations (localCount <= 2)', () => {
        const full = { id: 'conv-1', messages: [{}], _version: NOW };
        const d = mergeServerChat(
            sc({ _version: NOW }),
            local({ _version: NOW - 30_000, messages: [{}, {}] }),
            full,
            mergeCtx()
        );
        expect(d.action).toBe('set');
        if (d.action !== 'set') return;
        expect(d.record.messages).toHaveLength(1); // server body adopted
    });

    it('count guard does not mutate the shared full-fetch object', () => {
        // The inline implementation mutated full.messages in place; the
        // extraction copies first.  Lock that in.
        const localMsgs = [{}, {}, {}, {}];
        const serverMsgs = [{}];
        const full = { id: 'conv-1', messages: serverMsgs, _version: NOW };
        mergeServerChat(
            sc({ _version: NOW }),
            local({ _version: NOW - 30_000, messages: localMsgs }),
            full,
            mergeCtx()
        );
        expect(full.messages).toBe(serverMsgs);
    });

    it('rename-revert regression: pushed rename survives an equal-version sync', () => {
        // The original bug: rename only bumped local _version; a server-side
        // bead write then made serverVersion > localVersion and the summary
        // overlay reverted the title.  With the rename pushed, the server
        // copy carries the new title — and when versions tie, local wins.
        const renameTime = NOW - 10_000;
        const d = mergeServerChat(
            sc({ _version: renameTime, title: 'My Fork Name' }),
            local({ _version: renameTime, title: 'My Fork Name' }),
            undefined,
            mergeCtx()
        );
        expect(d.action).toBe('keep-local');
    });
});

// ── Open-work counts carried through every set branch ──────────────
// The sidebar's bead / work-item indicators read openBeadCount /
// openWorkItemCount off the merged record.  These are SUMMARY-only
// synthetic fields: a full-fetched Chat (getChat) does NOT carry them, so
// every `set` branch must source them from `sc` (the summary), never rely
// on the `...full` spread.  Branch 4 (summary-overlay) was the gap found
// in review — it spread `...local` and dropped the counts; a bead write
// bumps _version onto exactly that branch, so a stale count would show.
describe('mergeServerChat — open-work count propagation', () => {
    it('branch 1 (server-only + full): counts come from summary, not full', () => {
        // full has NO openBeadCount (Chat model lacks it); sc carries it.
        const full = { id: 'conv-1', messages: [{}, {}], _version: NOW };
        const d = mergeServerChat(
            sc({ openBeadCount: 3, openWorkItemCount: 0 }),
            undefined,
            full,
            mergeCtx()
        );
        expect(d.action).toBe('set');
        expect((d as any).record.openBeadCount).toBe(3);
        expect((d as any).record.openWorkItemCount).toBe(0);
    });

    it('branch 2 (server-only shell, no full): counts come from summary', () => {
        const d = mergeServerChat(
            sc({ title: 'Real chat', messageCount: 5, openBeadCount: 2, openWorkItemCount: 1 }),
            undefined,
            undefined,
            mergeCtx()
        );
        expect(d.action).toBe('set');
        expect((d as any).record._isShell).toBe(true);
        expect((d as any).record.openBeadCount).toBe(2);
        expect((d as any).record.openWorkItemCount).toBe(1);
    });

    it('branch 3 (server-newer + full): counts come from summary', () => {
        const full = { id: 'conv-1', messages: [{}, {}, {}, {}, {}], _version: NOW };
        const d = mergeServerChat(
            sc({ _version: NOW, openBeadCount: 4 }),
            local({ _version: NOW - 30_000 }),
            full,
            mergeCtx()
        );
        expect(d.action).toBe('set');
        expect((d as any).record.openBeadCount).toBe(4);
    });

    it('branch 4 (summary-overlay, server-newer no full): counts overlaid from summary', () => {
        // THE GAP: a bead write bumps server _version with no full fetch.
        // The overlay spreads ...local; the count must still update from sc.
        const d = mergeServerChat(
            sc({ _version: NOW, openBeadCount: 5 }),
            local({ _version: NOW - 30_000, _isShell: true, messages: [], openBeadCount: 1 }),
            undefined,
            mergeCtx()
        );
        expect(d.action).toBe('set');
        expect((d as any).record.openBeadCount).toBe(5);   // updated, not stale 1
    });

    it('branch 4 falls back to local count when summary omits it', () => {
        // Defensive: an older server without the field shouldn't zero a
        // count local already knows.
        const d = mergeServerChat(
            sc({ _version: NOW, openBeadCount: undefined }),
            local({ _version: NOW - 30_000, _isShell: true, messages: [], openBeadCount: 2 }),
            undefined,
            mergeCtx()
        );
        expect(d.action).toBe('set');
        expect((d as any).record.openBeadCount).toBe(2);   // preserved
    });

    it('counts default to 0 when neither summary nor local provide them', () => {
        const d = mergeServerChat(
            sc({ messageCount: 5, openBeadCount: undefined, openWorkItemCount: undefined }),
            undefined,
            undefined,
            mergeCtx()
        );
        expect((d as any).record.openBeadCount).toBe(0);
        expect((d as any).record.openWorkItemCount).toBe(0);
    });

    // ── keep-local count overlay (THE blocker: unversioned counts) ──────
    // Parking a bead writes the fallback store WITHOUT bumping the chat
    // record's _version, so serverVersion never exceeds localVersion and the
    // version-newer branches never fire — the merge falls to keep-local.
    // The counts are server-derived and unversioned, so they must be
    // overlaid even when local wins on content, or the indicator never shows.
    it('keep-local overlays a diverged server bead count (version tie)', () => {
        const d = mergeServerChat(
            sc({ _version: NOW - 30_000, openBeadCount: 2 }),
            local({ _version: NOW - 30_000, openBeadCount: 0 }),  // tie → keep-local path
            undefined,
            mergeCtx()
        );
        expect(d.action).toBe('set');
        expect((d as any).record.openBeadCount).toBe(2);   // overlaid, not discarded
    });

    it('keep-local overlays when local _version is NEWER (bead unversioned)', () => {
        const d = mergeServerChat(
            sc({ _version: NOW - 60_000, openBeadCount: 3, openWorkItemCount: 0 }),
            local({ _version: NOW, openBeadCount: 0 }),  // local newer → keep-local path
            undefined,
            mergeCtx()
        );
        expect(d.action).toBe('set');
        expect((d as any).record.openBeadCount).toBe(3);
    });

    it('keep-local preserves local content fields when overlaying counts', () => {
        const localRec = local({
            _version: NOW, title: 'Local Title', _isShell: true,
            messages: [], openBeadCount: 0,
        });
        const d = mergeServerChat(
            sc({ _version: NOW - 30_000, title: 'Stale Server', openBeadCount: 2 }),
            localRec,
            undefined,
            mergeCtx()
        );
        expect(d.action).toBe('set');
        // Content stays local (local won); only the count changed.
        expect((d as any).record.title).toBe('Local Title');
        expect((d as any).record._isShell).toBe(true);
        expect((d as any).record.openBeadCount).toBe(2);
    });

    it('keep-local stays keep-local when counts already agree (no churn)', () => {
        const d = mergeServerChat(
            sc({ _version: NOW - 30_000, openBeadCount: 2, openWorkItemCount: 0 }),
            local({ _version: NOW - 30_000, openBeadCount: 2, openWorkItemCount: 0 }),
            undefined,
            mergeCtx()
        );
        expect(d.action).toBe('keep-local');
    });
});

describe('canReusePrevConversation — post-merge reference reuse', () => {
    // The final-mile bug: mergeServerChat produced the correct openBeadCount,
    // but ChatContext's reference-preservation step reused the stale prev
    // object because its equality check omitted the count fields.  A count
    // changes WITHOUT a _version bump (parking a bead writes the fallback
    // store, not the chat record), so a version-gated comparison can't see it.
    const base = () => ({
        id: 'conv-1', _version: 100, messages: [{}, {}], title: 'T',
        folderId: null, isGlobal: false, delegateMeta: null,
        hasUnreadResponse: false, openBeadCount: 0, openWorkItemCount: 0,
    });

    it('reuses prev when nothing observable changed', () => {
        expect(canReusePrevConversation(base(), base())).toBe(true);
    });

    it('does NOT reuse when openBeadCount diverges (the headline bug)', () => {
        const existing = base();                       // count 0 in state
        const mc = { ...base(), openBeadCount: 2 };     // merge produced 2
        expect(canReusePrevConversation(mc, existing)).toBe(false);
    });

    it('does NOT reuse when openWorkItemCount diverges', () => {
        const existing = base();
        const mc = { ...base(), openWorkItemCount: 1 };
        expect(canReusePrevConversation(mc, existing)).toBe(false);
    });

    it('treats missing count fields as 0 (no spurious re-render)', () => {
        const existing = base();
        const mc = base();
        delete (mc as any).openBeadCount;
        delete (mc as any).openWorkItemCount;
        expect(canReusePrevConversation(mc, existing)).toBe(true);
    });

    it('returns false when existing is undefined (new conversation)', () => {
        expect(canReusePrevConversation(base(), undefined)).toBe(false);
    });

    it('still respects the pre-existing fields (title change blocks reuse)', () => {
        const existing = base();
        const mc = { ...base(), title: 'Renamed' };
        expect(canReusePrevConversation(mc, existing)).toBe(false);
    });
});
