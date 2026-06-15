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
