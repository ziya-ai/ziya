/**
 * mergeServerChat carries the server summary's createdAt onto the local
 * record on every branch.
 *
 * The sidebar's custom recency range can test a conversation's START
 * ("first activity", "lifetime").  The browser never stamped createdAt on a
 * conversation; only the server summary has it.  The merge enumerates or
 * spreads fields per branch, so a field missing from any one branch is
 * dropped there: the server-only shell lists its fields, and the keep-local
 * branch discards everything the server said unless a fill is written for
 * it (the lastActiveAt bug, syncMergeLastActiveAt.test.ts).
 *
 * The earliest known value wins.  conversationToServerChat pushes
 * lastAccessedAt in place of a missing createdAt, so a later value is the
 * suspect one.
 *
 * DIRECTION: every "adopts" test fails against the merge without createdAt.
 */
import { createdAtPatch, mergeServerChat } from '../syncMerge';

const ctx = { projectId: 'p', isActiveConv: false, now: 1_000_000, staleShellAgeMs: 3_600_000 };

const local = (extra: any = {}) => ({
    id: 'c1', title: 'A chat', projectId: 'p', _version: 500,
    lastAccessedAt: 500, lastActiveAt: 10, openBeadCount: 0, openWorkItemCount: 0, ...extra,
});
const server = (extra: any = {}) => ({
    id: 'c1', title: 'A chat', projectId: 'p', _version: 500,
    lastActiveAt: 10, messageCount: 3, openBeadCount: 0, openWorkItemCount: 0, ...extra,
});

describe('createdAtPatch', () => {
    it('takes the earliest known value', () => {
        expect(createdAtPatch(100, 300)).toEqual({ createdAt: 100 });
        expect(createdAtPatch(300, 100)).toEqual({ createdAt: 100 });
    });

    it('takes whichever side has one', () => {
        expect(createdAtPatch(100, undefined)).toEqual({ createdAt: 100 });
        expect(createdAtPatch(undefined, 100)).toEqual({ createdAt: 100 });
    });

    it('is empty when neither side has one; zero is "unknown", not a time', () => {
        expect(createdAtPatch(undefined, undefined)).toEqual({});
        expect(createdAtPatch(0, 0)).toEqual({});
        expect(createdAtPatch(0, 100)).toEqual({ createdAt: 100 });
    });
});

describe('mergeServerChat: createdAt', () => {
    it('server-only conversation: the shell record carries it', () => {
        const d = mergeServerChat(server({ createdAt: 100 }) as any, undefined, ctx) as any;
        expect(d.action).toBe('set');
        expect(d.record._isShell).toBe(true);
        expect(d.record.createdAt).toBe(100);
    });

    it('server-only conversation without one: no createdAt key is invented', () => {
        const d = mergeServerChat(server() as any, undefined, ctx) as any;
        expect(d.action).toBe('set');
        expect('createdAt' in d.record).toBe(false);
    });

    it('server newer: adopts the earlier of the two', () => {
        const later = mergeServerChat(server({ _version: 600, createdAt: 100 }) as any, local({ createdAt: 300 }) as any, ctx) as any;
        expect(later.action).toBe('set');
        expect(later.record.createdAt).toBe(100);
        const earlier = mergeServerChat(server({ _version: 600, createdAt: 100 }) as any, local({ createdAt: 50 }) as any, ctx) as any;
        expect(earlier.action).toBe('set');
        expect(earlier.record.createdAt).toBe(50);
    });

    it('versions tie, local has none: adopts the server value and keeps local content', () => {
        const d = mergeServerChat(server({ createdAt: 100 }) as any, local() as any, ctx) as any;
        expect(d.action).toBe('set');
        expect(d.record.createdAt).toBe(100);
        expect(d.record._version).toBe(500);
        expect(d.record.title).toBe('A chat');
    });

    it('local newer and holding a later value: adopts the earlier server value', () => {
        const d = mergeServerChat(server({ _version: 400, createdAt: 100 }) as any, local({ createdAt: 300 }) as any, ctx) as any;
        expect(d.action).toBe('set');
        expect(d.record.createdAt).toBe(100);
        expect(d.record._version).toBe(500);
    });

    it('never moves createdAt later, and does not churn when it already matches', () => {
        expect(mergeServerChat(server({ createdAt: 100 }) as any, local({ createdAt: 50 }) as any, ctx).action).toBe('keep-local');
        expect(mergeServerChat(server({ createdAt: 100 }) as any, local({ createdAt: 100 }) as any, ctx).action).toBe('keep-local');
        expect(mergeServerChat(server() as any, local({ createdAt: 100 }) as any, ctx).action).toBe('keep-local');
    });
});
