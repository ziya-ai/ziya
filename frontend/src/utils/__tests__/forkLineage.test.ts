/**
 * forkLineageFields (lineage.ts) — the lineage stamps a PLAIN fork applies.
 * Step 1 of design/conversation-handoff.md: new forks get branchedFrom +
 * lineageKind='fork' so they nest under their parent like branches do,
 * and the fork does not inherit its source's seam / handoff-target fields.
 *
 * Also asserts the syncMerge seam: the two enumerating paths carry
 * lineageKind / handedOffTo from a server summary, so a fork or handoff
 * created in another browser renders correctly before a full fetch.
 */
import { forkLineageFields, buildLineageChain } from '../lineage';
import {
    mergeServerChat, MergeDecisionCtx, ServerChatSummary, LocalShell,
} from '../syncMerge';

describe('forkLineageFields', () => {
    test('fork of a trunk: branchedFrom = source, kind = fork, root = source', () => {
        const f = forkLineageFields({ id: 'trunk' });
        expect(f.branchedFrom).toBe('trunk');
        expect(f.lineageKind).toBe('fork');
        expect(f.lineageRootId).toBe('trunk');
        expect(f.handedOffTo).toBeUndefined();
    });

    test('fork of a fork: root stays flat (never chains)', () => {
        const f = forkLineageFields({ id: 'fork1', lineageRootId: 'trunk' });
        expect(f.branchedFrom).toBe('fork1');
        expect(f.lineageRootId).toBe('trunk');
    });

    test('spread over a branch source clears the branch seam fields', () => {
        // Regression: `...source` alone would carry the branch's seam label
        // onto the fork, making it look cut at that seam.
        const source: any = {
            id: 'branch', lineageRootId: 'trunk',
            branchedFrom: 'trunk', branchedAtMessageIndex: 7,
            branchedFromLabel: 'microburst drops', handedOffTo: 'later',
        };
        const forked = { ...source, id: 'new', ...forkLineageFields(source) };
        expect(forked.branchedFrom).toBe('branch');
        expect(forked.branchedAtMessageIndex).toBeUndefined();
        expect(forked.branchedFromLabel).toBeUndefined();
        expect(forked.handedOffTo).toBeUndefined();
        expect(forked.lineageKind).toBe('fork');
    });

    test('a new fork appears in the lineage chain under its parent', () => {
        // Outermost surface: the chain the LineageBar / sidebar nesting read.
        const source = { id: 'trunk', title: 'Trunk' };
        const forked = { id: 'new', title: 'Fork: Trunk', ...forkLineageFields(source) };
        const chain = buildLineageChain('new', [source, forked]);
        expect(chain.map(n => n.id)).toEqual(['trunk', 'new']);
    });
});

describe('syncMerge carries lineageKind / handedOffTo', () => {
    // Same fixture shapes as syncMerge.test.ts (MergeDecisionCtx,
    // ServerChatSummary, LocalShell).
    const NOW = 1_700_000_000_000;
    const ctx: MergeDecisionCtx = {
        projectId: 'proj-1', isActiveConv: false, now: NOW,
        staleShellAgeMs: 24 * 3600 * 1000,
    };
    const sc = (over: Partial<ServerChatSummary> = {}): ServerChatSummary => ({
        id: 'conv-1', title: 'Hello', projectId: 'proj-1', messageCount: 4,
        lastActiveAt: NOW - 60_000, _version: NOW - 60_000, ...over,
    });
    const local = (over: Partial<LocalShell> = {}): LocalShell => ({
        id: 'conv-1', title: 'Hello', messages: [{}, {}, {}, {}],
        lastAccessedAt: NOW - 30_000, _version: NOW - 30_000, ...over,
    });

    test('server-only shell keeps the handoff discriminator and forward link', () => {
        const d = mergeServerChat(
            sc({ branchedFrom: 'src', lineageKind: 'handoff', handedOffTo: 'next' }),
            undefined, undefined, ctx,
        );
        expect(d.action).toBe('set');
        if (d.action !== 'set') return;
        expect(d.record._isShell).toBe(true);
        expect(d.record.lineageKind).toBe('handoff');
        expect(d.record.handedOffTo).toBe('next');
        expect(d.record.branchedFrom).toBe('src');
    });

    test('summary overlay adopts handedOffTo / lineageKind when the server is newer', () => {
        // Mirrors the flag-overlay test: a handoff committed in another
        // browser stamps the SOURCE with handedOffTo and a newer _version.
        const d = mergeServerChat(
            sc({ _version: NOW, handedOffTo: 'next', lineageKind: 'fork' }),
            local({ _version: NOW - 5_000 }),
            undefined, ctx,
        );
        expect(d.action).toBe('set');
        if (d.action !== 'set') return;
        expect(d.record.handedOffTo).toBe('next');
        expect(d.record.lineageKind).toBe('fork');
    });
});
