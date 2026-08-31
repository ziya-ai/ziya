/**
 * Regression tests for the two defects that let a conversation the user was
 * LOOKING AT go permanently stale while the server held newer messages.
 *
 * Observed in production (conv c14288e8, project f66402b0): server reported 37
 * messages, the Chrome window rendered 31, the 30s poll was demonstrably alive
 * (`listChats(101) took 73ms`, 4 shells hydrated per cycle), and the record was
 * absent from BOTH ZiyaDB.conversations and the localStorage shell cache.
 *
 * With no local record, shouldFetchFull's `!local` branch is gated solely on
 * `alreadyFetchedThisSession` — and the eager-hydration cap had marked this id
 * as fetched on the rationale that deferred shells "hydrate on open".  The chat
 * was already open, so it never re-opened and never hydrated.  Nothing else in
 * the pull could correct it: the shell marker excludes the record from the IDB
 * write, so `local` stays absent, so the gate stays closed.
 *
 * Compounding it, the bodyless placeholder built for a server-only chat stamped
 * itself with the server's _version, marking empty content as current.
 */
import {
    selectHydrationTargets,
    mergeServerChat,
    canReusePrevConversation,
    ServerChatSummary,
    MergeDecisionCtx,
} from '../syncMerge';

const NOW = 1_750_000_000_000;
const ACTIVE = 'c14288e8-110a-4363-bc54-1383f9ea605a';

describe('selectHydrationTargets', () => {
    // Recency is the production ordering key (mergedMap lastAccessedAt).
    // Rank ids by trailing number: id-000 newest, id-100 oldest.
    const recency = (id: string) => -Number(id.split('-')[1]);
    const ids = (n: number, from = 0) =>
        Array.from({ length: n }, (_, i) => `id-${String(i + from).padStart(3, '0')}`);

    it('hydrates everything when eligible is within the cap', () => {
        const eligible = ids(10);
        const sel = selectHydrationTargets(eligible, 25, ACTIVE, recency);
        expect(sel.targets).toEqual(eligible);
        expect(sel.deferred).toEqual([]);
    });

    it('caps to the most recent ids and defers the remainder', () => {
        const eligible = ids(56);
        const sel = selectHydrationTargets(eligible, 25, null, recency);
        expect(sel.targets).toHaveLength(25);
        expect(sel.deferred).toHaveLength(31);
        // Newest kept, oldest deferred.
        expect(sel.targets).toContain('id-000');
        expect(sel.deferred).toContain('id-055');
        // Partition: no overlap, nothing lost.
        expect(new Set([...sel.targets, ...sel.deferred]).size).toBe(56);
    });

    // The production defect. 101 server chats vs 45 local shells -> ~56
    // eligible, cap 25, and the active conversation ranked outside the top 25.
    it('force-includes the ACTIVE conversation when it ranks below the cap', () => {
        const eligible = [...ids(55), ACTIVE];
        const recencyWithStaleActive = (id: string) =>
            id === ACTIVE ? -999 : recency(id); // oldest of all -> would be deferred
        const sel = selectHydrationTargets(eligible, 25, ACTIVE, recencyWithStaleActive);
        expect(sel.targets).toContain(ACTIVE);
        expect(sel.deferred).not.toContain(ACTIVE);
    });

    it('never defers the active conversation even when every other id is newer', () => {
        const eligible = [ACTIVE, ...ids(200)];
        const sel = selectHydrationTargets(eligible, 5, ACTIVE, (id) =>
            id === ACTIVE ? 0 : 1_000_000);
        expect(sel.targets).toContain(ACTIVE);
        expect(sel.deferred).not.toContain(ACTIVE);
        // The exemption costs exactly one extra fetch, not an uncapped batch.
        expect(sel.targets).toHaveLength(6);
    });

    it('does not duplicate the active conversation when it already ranks inside the cap', () => {
        const eligible = [ACTIVE, ...ids(55)];
        const sel = selectHydrationTargets(eligible, 25, ACTIVE, (id) =>
            id === ACTIVE ? 1_000_000 : recency(id));
        expect(sel.targets.filter(id => id === ACTIVE)).toHaveLength(1);
        expect(sel.targets).toHaveLength(25);
    });

    it('does not inject an active id that is not eligible', () => {
        const eligible = ids(56);
        const sel = selectHydrationTargets(eligible, 25, 'not-in-this-project', recency);
        expect(sel.targets).not.toContain('not-in-this-project');
        expect(sel.targets).toHaveLength(25);
    });

    it('tolerates a null active id (no conversation open)', () => {
        const sel = selectHydrationTargets(ids(30), 25, null, recency);
        expect(sel.targets).toHaveLength(25);
        expect(sel.deferred).toHaveLength(5);
    });
});

describe('mergeServerChat: bodyless placeholder version stamping', () => {
    const ctx = (): MergeDecisionCtx => ({
        projectId: 'f66402b0',
        isActiveConv: true,
        now: NOW,
        staleShellAgeMs: 60 * 60 * 1000,
    });

    const summary = (over: Partial<ServerChatSummary> = {}): ServerChatSummary => ({
        id: ACTIVE,
        title: 'pick up from HANDOFF.md',
        messageCount: 37,
        lastActiveAt: NOW - 1000,
        _version: 1787697560970,
        ...over,
    });

    it('does NOT claim the server version when it carries no body the server says exists', () => {
        // local absent, full fetch deferred -> shell placeholder with messages: []
        const d = mergeServerChat(summary(), undefined, undefined, ctx()) as any;
        expect(d.action).toBe('set');
        expect(d.record.messages).toEqual([]);
        expect(d.record._isShell).toBe(true);
        expect(d.record._fullMessageCount).toBe(37);
        expect(d.record._version).toBe(0);
    });

    it('still stamps the server version when the server reports no messages', () => {
        // Nothing is being misrepresented here: empty record, empty server.
        const d = mergeServerChat(
            summary({ messageCount: 0, title: 'Sidebar Only' }), undefined, undefined, ctx()
        ) as any;
        expect(d.action).toBe('set');
        expect(d.record._version).toBe(1787697560970);
    });

    it('stamps the server version when messageCount is absent (older server summary)', () => {
        const s = summary({ title: 'No Count' });
        delete (s as any).messageCount;
        const d = mergeServerChat(s, undefined, undefined, ctx()) as any;
        expect(d.record._version).toBe(1787697560970);
    });

    // The seam: placeholder -> preservation/reuse. A bodyless placeholder must
    // never displace a fuller in-memory copy, which is what a serverVersion
    // stamp enabled (canReusePrevConversation compares _version first).
    it('a bodyless placeholder never displaces the rendered in-memory record', () => {
        const d = mergeServerChat(summary(), undefined, undefined, ctx()) as any;
        const inMemory = {
            ...d.record,
            messages: Array.from({ length: 31 }, (_, i) => ({ role: 'human', content: `m${i}` })),
            _isShell: false,
            _fullMessageCount: undefined,
            _version: 1787697560970,
        };
        expect(canReusePrevConversation(d.record, inMemory)).toBe(true);
    });
});
