/**
 * Regression tests for the SHELL_GUARD recovery decision core.
 *
 * Context: ChatContext's addMessageToConversation refuses to append to a
 * conversation whose in-state messages may be truncated (a "shell"), queues the
 * message, and recovers the full body first.  Two defects lived in that block:
 *
 *   1. The arming condition (`_fullMessageCount || 0 > messages.length`) read
 *      BOTH "count unknown" and "count 0" as proof of completeness, so a
 *      truncated shell fell through to a blind append and kept its `_isShell`
 *      marker.  A marker-bearing record is filtered out of every IDB write, so
 *      it never persisted -- which is the enabling condition for the
 *      cross-browser sync wedge (no local record => shouldFetchFull's `!local`
 *      branch => gated only on recentlyFetchedFullIds => never pulls again).
 *
 *   2. The recovery deleted the pending queue unconditionally but re-applied it
 *      only when IDB returned a fuller body.  When IDB had no record at all
 *      (exactly the wedged case), the queued user/assistant messages were
 *      silently discarded.
 *
 * These functions were extracted so both are testable without React or a live
 * IndexedDB.  Keep in lockstep with utils/shellRecovery.ts.
 */
import {
    isKnownCompleteShell,
    recoverShellMessages,
    ShellRecoveryDeps,
} from '../shellRecovery';
import type { Message } from '../types';

const msg = (content: string, role: 'human' | 'assistant' = 'human'): Message =>
    ({ role, content } as Message);

const body = (n: number): Message[] =>
    Array.from({ length: n }, (_, i) => msg(`m${i}`));

/** Deps whose tiers are individually configurable; records the call order. */
function makeDeps(opts: {
    idb?: any | Error;
    server?: any | Error;
} = {}): ShellRecoveryDeps & { calls: string[] } {
    const calls: string[] = [];
    return {
        calls,
        getIdbRecord: async (id: string) => {
            calls.push(`idb:${id}`);
            if (opts.idb instanceof Error) throw opts.idb;
            return opts.idb ?? null;
        },
        getServerChat: async (projectId: string, id: string) => {
            calls.push(`server:${projectId}:${id}`);
            if (opts.server instanceof Error) throw opts.server;
            return opts.server ?? null;
        },
    };
}

describe('isKnownCompleteShell', () => {
    it('is true when a known count is exactly met', () => {
        expect(isKnownCompleteShell({ messages: body(5), _fullMessageCount: 5 })).toBe(true);
    });

    it('is true when the array exceeds the known count', () => {
        expect(isKnownCompleteShell({ messages: body(7), _fullMessageCount: 5 })).toBe(true);
    });

    it('is false when the known count exceeds the array (genuinely truncated)', () => {
        expect(isKnownCompleteShell({ messages: body(2), _fullMessageCount: 37 })).toBe(false);
    });

    // Defect 1.  The old `_fullMessageCount || 0` coerced absent to 0, and
    // `0 > messages.length` is false, so an unknown count read as complete and
    // the guard never armed -- a blind append onto a possibly-truncated array.
    it('is false when the count is UNKNOWN (not treated as complete)', () => {
        expect(isKnownCompleteShell({ messages: body(2) })).toBe(false);
        expect(isKnownCompleteShell({ messages: body(2), _fullMessageCount: undefined })).toBe(false);
    });

    // Same hole from the other direction: a server summary that omits
    // messageCount lands `_fullMessageCount: 0` on the shell placeholder.
    it('is false when the count is 0 (summary omitted messageCount)', () => {
        expect(isKnownCompleteShell({ messages: [], _fullMessageCount: 0 })).toBe(false);
        expect(isKnownCompleteShell({ messages: body(2), _fullMessageCount: 0 })).toBe(false);
    });

    it('is false for a non-finite count rather than throwing', () => {
        expect(isKnownCompleteShell({ messages: body(2), _fullMessageCount: NaN })).toBe(false);
        expect(isKnownCompleteShell({ messages: body(2), _fullMessageCount: Infinity })).toBe(false);
    });

    it('is false for a missing record', () => {
        expect(isKnownCompleteShell(null)).toBe(false);
        expect(isKnownCompleteShell(undefined)).toBe(false);
    });

    it('tolerates a record with no messages array', () => {
        expect(isKnownCompleteShell({ _fullMessageCount: 3 } as any)).toBe(false);
    });
});

describe('recoverShellMessages', () => {
    it('adopts a fuller IDB body without consulting the server', async () => {
        const deps = makeDeps({ idb: { messages: body(37) } });
        const out = await recoverShellMessages('c1', 'p1', 2, deps);
        expect(out).toEqual({ action: 'adopt', messages: expect.any(Array), source: 'idb' });
        expect((out as any).messages).toHaveLength(37);
        // Positive control on ordering AND a cost assertion: the server tier
        // must not be paid for when IDB already answered.
        expect(deps.calls).toEqual(['idb:c1']);
    });

    it('refuses an IDB record that is ITSELF a shell and falls through to the server', async () => {
        const deps = makeDeps({
            idb: { messages: body(31), _isShell: true },
            server: { messages: body(37) },
        });
        const out = await recoverShellMessages('c1', 'p1', 2, deps);
        expect(out).toEqual({ action: 'adopt', messages: expect.any(Array), source: 'server' });
        expect((out as any).messages).toHaveLength(37);
        expect(deps.calls).toEqual(['idb:c1', 'server:p1:c1']);
    });

    // The wedged production case: no IDB row exists because the record was
    // flagged _isShell and therefore excluded from every IDB write.
    it('adopts from the server when IDB has no record at all', async () => {
        const deps = makeDeps({ idb: null, server: { messages: body(37) } });
        const out = await recoverShellMessages('c1', 'p1', 31, deps);
        expect(out).toEqual({ action: 'adopt', messages: expect.any(Array), source: 'server' });
        expect((out as any).messages).toHaveLength(37);
    });

    it('adopts from the server when the IDB read throws', async () => {
        const deps = makeDeps({ idb: new Error('IDB unavailable'), server: { messages: body(9) } });
        const out = await recoverShellMessages('c1', 'p1', 2, deps);
        expect(out).toEqual({ action: 'adopt', messages: expect.any(Array), source: 'server' });
        expect(deps.calls).toEqual(['idb:c1', 'server:p1:c1']);
    });

    it('does NOT adopt an IDB body that is no fuller than local', async () => {
        const deps = makeDeps({ idb: { messages: body(2) }, server: { messages: body(2) } });
        const out = await recoverShellMessages('c1', 'p1', 2, deps);
        expect(out.action).not.toBe('adopt');
    });

    // The server is authoritative: if it holds no more than we do, the local
    // array is not truncated and appending to it cannot lose history.  Without
    // this outcome a legitimately-short conversation that happens to carry a
    // shell marker could never accept another message.
    it('returns apply-local when the server confirms local is already complete', async () => {
        const deps = makeDeps({ idb: null, server: { messages: body(4) } });
        const out = await recoverShellMessages('c1', 'p1', 4, deps);
        expect(out).toEqual({ action: 'apply-local', source: 'server-complete' });
    });

    it('returns apply-local when the server holds FEWER messages than local', async () => {
        const deps = makeDeps({ idb: null, server: { messages: body(1) } });
        const out = await recoverShellMessages('c1', 'p1', 6, deps);
        expect(out).toEqual({ action: 'apply-local', source: 'server-complete' });
    });

    it('returns apply-local for an empty conversation the server also reports empty', async () => {
        const deps = makeDeps({ idb: null, server: { messages: [] } });
        const out = await recoverShellMessages('c1', 'p1', 0, deps);
        expect(out).toEqual({ action: 'apply-local', source: 'server-complete' });
    });

    it('holds (never guesses) when there is no projectId to query', async () => {
        const deps = makeDeps({ idb: null });
        const out = await recoverShellMessages('c1', undefined, 2, deps);
        expect(out).toEqual({ action: 'hold', reason: 'no-project' });
        // The server tier must not be invented from a missing project id.
        expect(deps.calls).toEqual(['idb:c1']);
    });

    it('holds when the server read throws', async () => {
        const deps = makeDeps({ idb: null, server: new Error('network down') });
        const out = await recoverShellMessages('c1', 'p1', 2, deps);
        expect(out).toEqual({ action: 'hold', reason: 'unreachable' });
    });

    it('holds when the server returns no record', async () => {
        const deps = makeDeps({ idb: null, server: null });
        const out = await recoverShellMessages('c1', 'p1', 2, deps);
        expect(out).toEqual({ action: 'hold', reason: 'unreachable' });
    });

    it('holds when the server record carries no messages array', async () => {
        const deps = makeDeps({ idb: null, server: { title: 'x' } });
        const out = await recoverShellMessages('c1', 'p1', 2, deps);
        expect(out).toEqual({ action: 'hold', reason: 'unreachable' });
    });

    // Defect 2, stated as an invariant rather than a branch: a hold must never
    // hand the caller anything to append.  If a hold ever carried messages the
    // caller would apply them onto a possibly-truncated array, and the sync
    // push (which treats a fresher local _version as authoritative) would
    // propagate that truncation to the server.
    it('never returns messages to apply on a hold outcome', async () => {
        const cases: Array<ShellRecoveryDeps> = [
            makeDeps({ idb: null }),
            makeDeps({ idb: null, server: new Error('boom') }),
            makeDeps({ idb: null, server: null }),
            makeDeps({ idb: new Error('boom'), server: new Error('boom') }),
        ];
        const pids = [undefined, 'p1', 'p1', 'p1'];
        for (let i = 0; i < cases.length; i++) {
            const out = await recoverShellMessages('c1', pids[i], 2, cases[i]);
            expect(out.action).toBe('hold');
            expect((out as any).messages).toBeUndefined();
        }
    });

    it('holds rather than throwing when BOTH tiers fail', async () => {
        const deps = makeDeps({ idb: new Error('idb'), server: new Error('net') });
        await expect(recoverShellMessages('c1', 'p1', 2, deps)).resolves.toEqual({
            action: 'hold', reason: 'unreachable',
        });
    });
});
