/**
 * Local-newer merge fills lineage the local record never had.
 *
 * Observed 2026-10-20 (continuation d88d9994): a window minted a fresh local
 * record for a handoff continuation on its first send — no branchedFrom, no
 * lineageKind — and because that record was newer it won the merge on every
 * sync cycle.  The sidebar could neither nest the segment under its source
 * nor mark it ⛓, even though the server summary carried both fields.
 * Only ABSENT local fields are filled; a deliberate local value is kept.
 */
import {
    mergeServerChat, MergeDecisionCtx, ServerChatSummary, LocalShell,
} from '../syncMerge';

const NOW = 1_700_000_000_000;
const ctx: MergeDecisionCtx = {
    projectId: 'proj-1', isActiveConv: false, now: NOW,
    staleShellAgeMs: 24 * 3600 * 1000,
};
const sc = (over: Partial<ServerChatSummary> = {}): ServerChatSummary => ({
    id: 'cont-1', title: 'Cont', projectId: 'proj-1', messageCount: 1,
    lastActiveAt: NOW - 60_000, _version: NOW - 60_000, ...over,
});
const local = (over: Partial<LocalShell> = {}): LocalShell => ({
    id: 'cont-1', title: 'Cont', messages: [{}],
    lastAccessedAt: NOW - 30_000, _version: NOW - 30_000, ...over,
});

describe('mergeServerChat: lineage fill on the local-newer branch', () => {
    test('absent local lineage is adopted from the server summary', () => {
        const d = mergeServerChat(
            sc({ branchedFrom: 'src-1', lineageKind: 'handoff' }),
            local(), ctx,
        );
        expect(d.action).toBe('set');
        if (d.action !== 'set') return;
        expect(d.record.branchedFrom).toBe('src-1');
        expect(d.record.lineageKind).toBe('handoff');
        // Content is still the local (newer) record's.
        expect(d.record._version).toBe(NOW - 30_000);
    });

    test('a present local value is never overridden', () => {
        const d = mergeServerChat(
            sc({ branchedFrom: 'src-1', lineageKind: 'handoff' }),
            local({ branchedFrom: 'other', lineageKind: 'fork' }), ctx,
        );
        // Nothing to fill and counts match → untouched local.
        expect(d.action).toBe('keep-local');
    });

    test('no lineage on either side stays keep-local (no per-cycle churn)', () => {
        expect(mergeServerChat(sc(), local(), ctx).action).toBe('keep-local');
    });
});
