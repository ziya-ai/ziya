/**
 * mergeServerChat must repair a stale local lastActiveAt even when local wins
 * on _version.
 *
 * The sidebar recency filter reads lastActiveAt first (chatTreeRecency
 * conversationActivityAt).  Locally that field was only ever adopted on the
 * server-newer branch, and local writes stamp _version, so for a
 * conversation written in this browser local always ties or wins and the
 * server's lastActiveAt was discarded on every cycle.  A fork created before
 * the fork fix carried its SOURCE's lastActiveAt this way indefinitely.
 *
 * DIRECTION: the first test fails against the unpatched merge (keep-local).
 */
import { mergeServerChat } from '../syncMerge';

const ctx = { projectId: 'p', isActiveConv: false, now: 1_000_000, staleShellAgeMs: 3_600_000 };

const local = (extra: any = {}) => ({
    id: 'c1', title: 'Fork: x', projectId: 'p', _version: 500,
    lastAccessedAt: 500, lastActiveAt: 10, openBeadCount: 0, openWorkItemCount: 0,
    branchedFrom: 'src', lineageKind: 'fork' as const, ...extra,
});
const server = (extra: any = {}) => ({
    id: 'c1', title: 'Fork: x', projectId: 'p', _version: 500,
    lastActiveAt: 480, messageCount: 3, openBeadCount: 0, openWorkItemCount: 0,
    branchedFrom: 'src', lineageKind: 'fork' as const, ...extra,
});

describe('mergeServerChat: lastActiveAt on the keep-local side', () => {
    it('adopts a newer server lastActiveAt when versions tie', () => {
        const d = mergeServerChat(server() as any, local() as any, ctx) as any;
        expect(d.action).toBe('set');
        expect(d.record.lastActiveAt).toBe(480);
        // Content and version stay local.
        expect(d.record._version).toBe(500);
        expect(d.record.title).toBe('Fork: x');
    });

    it('adopts it when local is newer too', () => {
        const d = mergeServerChat(server({ _version: 400 }) as any, local() as any, ctx) as any;
        expect(d.action).toBe('set');
        expect(d.record.lastActiveAt).toBe(480);
        expect(d.record._version).toBe(500);
    });

    it('never moves lastActiveAt backwards', () => {
        const d = mergeServerChat(server({ lastActiveAt: 5 }) as any, local() as any, ctx) as any;
        expect(d.action).toBe('keep-local');
    });

    it('still keeps local when nothing differs', () => {
        const d = mergeServerChat(server({ lastActiveAt: 10 }) as any, local() as any, ctx) as any;
        expect(d.action).toBe('keep-local');
    });
});
