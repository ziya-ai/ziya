/**
 * Regression tests for ephemeral-chat survival across the periodic server sync.
 *
 * An ephemeral conversation is, by construction, present in NEITHER
 * IndexedDB nor the server — that is the whole point of it.  The 30s sync
 * rebuilds React state from exactly those two sources, so an ephemeral can
 * only survive via the sync's two "prev-only preservation" passes.
 *
 * Both passes originally capped survival at 5 minutes since last touch.
 * That cap is correct for the case it was written for (a just-forked
 * conversation whose background IDB write hasn't landed — prev-only is a
 * transient race there), and exactly wrong for an ephemeral, where
 * prev-only is the permanent steady state.  The observable symptom was an
 * idle ephemeral vanishing on the first sync ~5 minutes after its last
 * touch, while the FOCUSED one survived through the separate
 * active-conversation rescue — making the loss look intermittent.
 *
 * Both predicates are extracted as pure functions here, matching the
 * convention in ephemeralChat.test.ts, so the contract is locked without
 * standing up a live React tree (or mocking IndexedDB / syncApi).
 */

// Force module scope — see identical comment in ephemeralChat.test.ts.
export {};

const PRESERVATION_MAX_AGE_MS = 5 * 60 * 1000;

interface PrevConversation {
    id: string;
    isEphemeral?: boolean;
    isActive?: boolean;
    projectId?: string;
    isGlobal?: boolean;
    lastAccessedAt?: number;
    _version?: number;
}

/**
 * Pure extraction of the main prev-only preservation loop in
 * ChatContext.syncWithServer (the one that builds \`safeConvs\`).
 */
function preserveInMainLoop(
    prev: PrevConversation[],
    opts: {
        mergedIds: Set<string>;
        knownServerIds: Set<string>;
        projectId: string;
        nowTs: number;
    },
): PrevConversation[] {
    const preserved: PrevConversation[] = [];
    for (const p of prev) {
        if (opts.mergedIds.has(p.id)) continue;
        if (p.isActive === false) continue;
        if (opts.knownServerIds.has(p.id)) continue;
        if (p.projectId && p.projectId !== opts.projectId && !p.isGlobal) continue;
        if (p.isEphemeral) {
            preserved.push(p);
            continue;
        }
        const lastActivity = p.lastAccessedAt || p._version || 0;
        if (lastActivity === 0 || opts.nowTs - lastActivity > PRESERVATION_MAX_AGE_MS) continue;
        preserved.push(p);
    }
    return preserved;
}

/**
 * Pure extraction of the late-preservation filter that runs inside the
 * setConversations updater, against a fresher \`prev\` snapshot.
 */
function preserveInLateFilter(
    prev: PrevConversation[],
    opts: {
        mergedIdSet: Set<string>;
        knownServerIds: Set<string>;
        projectId: string;
        nowTs: number;
    },
): PrevConversation[] {
    return prev.filter(p =>
        !opts.mergedIdSet.has(p.id)
        && p.isActive !== false
        && (!p.projectId || p.projectId === opts.projectId)
        && !opts.knownServerIds.has(p.id)
        && (p.isEphemeral
            || (opts.nowTs - (p.lastAccessedAt || p._version || 0)) < PRESERVATION_MAX_AGE_MS)
    );
}

const NOW = 1_000_000_000;
const LONG_IDLE = NOW - (30 * 60 * 1000);   // 30 minutes: well past the cap
const RECENT = NOW - 5_000;

const ephemeral = (over: Partial<PrevConversation> = {}): PrevConversation => ({
    id: 'eph-1',
    isEphemeral: true,
    isActive: true,
    projectId: 'proj-a',
    lastAccessedAt: RECENT,
    ...over,
});

const baseOpts = {
    mergedIds: new Set<string>(),
    mergedIdSet: new Set<string>(),
    knownServerIds: new Set<string>(),
    projectId: 'proj-a',
    nowTs: NOW,
};

describe('main preservation loop', () => {
    test('keeps an idle ephemeral past the 5-minute staleness cap', () => {
        // The reported bug: an unfocused ephemeral disappearing on its own.
        const kept = preserveInMainLoop([ephemeral({ lastAccessedAt: LONG_IDLE })], baseOpts);
        expect(kept.map(c => c.id)).toEqual(['eph-1']);
    });

    test('keeps a recently-touched ephemeral', () => {
        const kept = preserveInMainLoop([ephemeral()], baseOpts);
        expect(kept).toHaveLength(1);
    });

    test('keeps an ephemeral with no timestamps at all', () => {
        // lastActivity === 0 was an independent drop condition, so an
        // ephemeral missing both fields must not fall through it either.
        const kept = preserveInMainLoop(
            [ephemeral({ lastAccessedAt: undefined, _version: undefined })], baseOpts,
        );
        expect(kept).toHaveLength(1);
    });

    test('still drops a non-ephemeral prev-only entry past the cap', () => {
        // The cap must keep working for the case it was written for, or a
        // stale prev-only entry could fight a sibling-tab delete.
        const kept = preserveInMainLoop(
            [{ id: 'fork-1', isActive: true, projectId: 'proj-a', lastAccessedAt: LONG_IDLE }],
            baseOpts,
        );
        expect(kept).toHaveLength(0);
    });

    test('still drops an explicitly deleted ephemeral', () => {
        // Exempting the AGE cap must not exempt the delete flag.
        const kept = preserveInMainLoop(
            [ephemeral({ isActive: false, lastAccessedAt: LONG_IDLE })], baseOpts,
        );
        expect(kept).toHaveLength(0);
    });

    test('still drops an ephemeral belonging to another project', () => {
        const kept = preserveInMainLoop(
            [ephemeral({ projectId: 'proj-b', lastAccessedAt: LONG_IDLE })], baseOpts,
        );
        expect(kept).toHaveLength(0);
    });

    test('preserves several idle ephemerals at once', () => {
        // Multiple background ephemerals are the normal multitasking case.
        const kept = preserveInMainLoop([
            ephemeral({ id: 'eph-1', lastAccessedAt: LONG_IDLE }),
            ephemeral({ id: 'eph-2', lastAccessedAt: LONG_IDLE }),
            ephemeral({ id: 'eph-3', lastAccessedAt: LONG_IDLE }),
        ], baseOpts);
        expect(kept.map(c => c.id)).toEqual(['eph-1', 'eph-2', 'eph-3']);
    });
});

describe('late preservation filter', () => {
    test('keeps an idle ephemeral past the staleness cap', () => {
        // This pass runs after the main loop and would otherwise re-drop it.
        const kept = preserveInLateFilter([ephemeral({ lastAccessedAt: LONG_IDLE })], baseOpts);
        expect(kept.map(c => c.id)).toEqual(['eph-1']);
    });

    test('keeps an ephemeral with no timestamps at all', () => {
        const kept = preserveInLateFilter(
            [ephemeral({ lastAccessedAt: undefined, _version: undefined })], baseOpts,
        );
        expect(kept).toHaveLength(1);
    });

    test('still drops a non-ephemeral prev-only entry past the cap', () => {
        const kept = preserveInLateFilter(
            [{ id: 'fork-1', isActive: true, projectId: 'proj-a', lastAccessedAt: LONG_IDLE }],
            baseOpts,
        );
        expect(kept).toHaveLength(0);
    });

    test('still drops an explicitly deleted ephemeral', () => {
        const kept = preserveInLateFilter(
            [ephemeral({ isActive: false, lastAccessedAt: LONG_IDLE })], baseOpts,
        );
        expect(kept).toHaveLength(0);
    });

    test('does not re-add an ephemeral already in the merged result', () => {
        // Duplicate entries would render the chat twice in the sidebar.
        const kept = preserveInLateFilter([ephemeral({ lastAccessedAt: LONG_IDLE })], {
            ...baseOpts,
            mergedIdSet: new Set(['eph-1']),
        });
        expect(kept).toHaveLength(0);
    });
});

describe('pre-fix behaviour (negative control)', () => {
    // Proves these tests measure a real behavioural difference rather than
    // agreeing with the new code by construction.
    function preFixMainLoop(prev: PrevConversation[], nowTs: number): PrevConversation[] {
        return prev.filter(p => {
            const lastActivity = p.lastAccessedAt || p._version || 0;
            return !(lastActivity === 0 || nowTs - lastActivity > PRESERVATION_MAX_AGE_MS);
        });
    }

    test('dropped an idle ephemeral, which the fix now keeps', () => {
        const idle = [ephemeral({ lastAccessedAt: LONG_IDLE })];
        expect(preFixMainLoop(idle, NOW)).toHaveLength(0);
        expect(preserveInMainLoop(idle, baseOpts)).toHaveLength(1);
    });
});
