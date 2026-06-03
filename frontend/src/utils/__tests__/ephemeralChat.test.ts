/**
 * Regression tests for the ephemeral-chat lifecycle in ChatContext.
 *
 * Ephemeral conversations live in React state for the current UX session
 * only. They must never reach IndexedDB or the server's bulkSync endpoint,
 * and they must remain invisible to other browser tabs / clients.
 *
 * Enforcement happens in two places:
 *   1. queueSave's EPHEMERAL GUARD strips ephemerals from the input
 *      conversations array and from the changedIds set before any
 *      persistence path runs (debounced slow path, FAST_PATH dual-write,
 *      project-switch syncs).
 *   2. promoteEphemeralToRetained clears the isEphemeral flag and bumps
 *      _version so the conversation re-enters the normal save pipeline.
 *
 * These tests extract both transformations as pure functions so we can
 * lock the contract without standing up a live React tree (or mocking
 * IndexedDB / BroadcastChannel / syncApi.bulkSync).
 */

interface MinimalConversation {
    id: string;
    title: string;
    isEphemeral?: boolean;
    isActive?: boolean;
    _version?: number;
    lastAccessedAt?: number;
    messages: { id: string; content: string }[];
}

/**
 * Pure extraction of the EPHEMERAL GUARD inside ChatContext.queueSave.
 *
 * Mirrors the block at the top of queueSave: builds the set of ephemeral
 * ids, removes them from the conversations array and from changedIds,
 * and signals a short-circuit when changedIds was non-empty before but
 * is empty after (the original call was about ephemerals only, so the
 * whole save can be skipped).
 */
function applyEphemeralGuard(
    conversations: MinimalConversation[],
    changedIds: string[] | undefined,
): {
    filteredConversations: MinimalConversation[];
    filteredChangedIds: string[] | undefined;
    shortCircuit: boolean;
} {
    const ephemeralIds = new Set(
        conversations.filter(c => c.isEphemeral).map(c => c.id),
    );
    if (ephemeralIds.size === 0) {
        return { filteredConversations: conversations, filteredChangedIds: changedIds, shortCircuit: false };
    }
    const filteredConversations = conversations.filter(c => !ephemeralIds.has(c.id));
    if (changedIds === undefined) {
        // Full-save call (no specific changedIds) — no short-circuit, just
        // strip ephemerals from the input and let the rest proceed.
        return { filteredConversations, filteredChangedIds: undefined, shortCircuit: false };
    }
    const hadIds = changedIds.length > 0;
    const filteredChangedIds = changedIds.filter(id => !ephemeralIds.has(id));
    const shortCircuit = hadIds && filteredChangedIds.length === 0;
    return { filteredConversations, filteredChangedIds, shortCircuit };
}

/**
 * Pure extraction of promoteEphemeralToRetained's state mutation.
 *
 * Returns the next conversations array plus a `promoted` flag matching
 * the early-return guard in ChatContext (no save scheduled when the
 * caller targets a non-ephemeral or unknown id).
 */
function applyPromote(
    conversations: MinimalConversation[],
    targetId: string,
    now: number,
): { next: MinimalConversation[]; promoted: boolean } {
    let promoted = false;
    const next = conversations.map(conv => {
        if (conv.id !== targetId || !conv.isEphemeral) return conv;
        promoted = true;
        const { isEphemeral, ...rest } = conv;
        return { ...rest, _version: now, lastAccessedAt: now };
    });
    return { next, promoted };
}

describe('queueSave ephemeral guard', () => {
    test('strips ephemeral conversations from a per-message save and short-circuits', () => {
        const convs: MinimalConversation[] = [
            { id: 'eph-1', title: 'Ephemeral', isEphemeral: true, isActive: true, messages: [{ id: 'm1', content: 'hi' }] },
        ];
        const result = applyEphemeralGuard(convs, ['eph-1']);
        expect(result.shortCircuit).toBe(true);
        expect(result.filteredConversations).toHaveLength(0);
        expect(result.filteredChangedIds).toEqual([]);
    });

    test('strips ephemerals but preserves a real conversation in a mixed batch', () => {
        const convs: MinimalConversation[] = [
            { id: 'eph-1', title: 'Ephemeral', isEphemeral: true, isActive: true, messages: [] },
            { id: 'real-1', title: 'Retained', isActive: true, messages: [{ id: 'm1', content: 'hi' }] },
        ];
        const result = applyEphemeralGuard(convs, ['eph-1', 'real-1']);
        expect(result.shortCircuit).toBe(false);
        expect(result.filteredConversations.map(c => c.id)).toEqual(['real-1']);
        expect(result.filteredChangedIds).toEqual(['real-1']);
    });

    test('passes through unchanged when no conversation is ephemeral', () => {
        const convs: MinimalConversation[] = [
            { id: 'real-1', title: 'A', isActive: true, messages: [] },
            { id: 'real-2', title: 'B', isActive: true, messages: [] },
        ];
        const result = applyEphemeralGuard(convs, ['real-1']);
        expect(result.shortCircuit).toBe(false);
        expect(result.filteredConversations).toBe(convs); // same reference
        expect(result.filteredChangedIds).toEqual(['real-1']);
    });

    test('full-save call (changedIds undefined) strips ephemerals without short-circuit', () => {
        const convs: MinimalConversation[] = [
            { id: 'eph-1', title: 'Ephemeral', isEphemeral: true, isActive: true, messages: [] },
            { id: 'real-1', title: 'Retained', isActive: true, messages: [] },
        ];
        const result = applyEphemeralGuard(convs, undefined);
        expect(result.shortCircuit).toBe(false);
        expect(result.filteredConversations.map(c => c.id)).toEqual(['real-1']);
        expect(result.filteredChangedIds).toBeUndefined();
    });

    test('does not mutate the input conversations array', () => {
        const convs: MinimalConversation[] = [
            { id: 'eph-1', title: 'Ephemeral', isEphemeral: true, isActive: true, messages: [] },
            { id: 'real-1', title: 'Retained', isActive: true, messages: [] },
        ];
        const snapshot = convs.map(c => c.id);
        applyEphemeralGuard(convs, ['eph-1', 'real-1']);
        expect(convs.map(c => c.id)).toEqual(snapshot);
        expect(convs[0].isEphemeral).toBe(true);
    });

    test('handles a save where ALL non-ephemeral conversations remain', () => {
        const convs: MinimalConversation[] = [
            { id: 'eph-1', title: 'Ephemeral', isEphemeral: true, isActive: true, messages: [] },
            { id: 'real-1', title: 'Retained', isActive: true, messages: [] },
        ];
        // changedIds includes only the real one; ephemeral is in the array
        // but not in the changedIds. Guard should still strip the ephemeral
        // from the array and leave changedIds untouched.
        const result = applyEphemeralGuard(convs, ['real-1']);
        expect(result.shortCircuit).toBe(false);
        expect(result.filteredConversations.map(c => c.id)).toEqual(['real-1']);
        expect(result.filteredChangedIds).toEqual(['real-1']);
    });
});

describe('promoteEphemeralToRetained transform', () => {
    const NOW = 1_700_000_000_000;

    test('clears isEphemeral, bumps _version, and updates lastAccessedAt', () => {
        const convs: MinimalConversation[] = [
            { id: 'eph-1', title: 'Ephemeral', isEphemeral: true, isActive: true, _version: 1, lastAccessedAt: 1, messages: [{ id: 'm1', content: 'hi' }] },
        ];
        const { next, promoted } = applyPromote(convs, 'eph-1', NOW);
        expect(promoted).toBe(true);
        expect(next[0].isEphemeral).toBeUndefined();
        expect(next[0]._version).toBe(NOW);
        expect(next[0].lastAccessedAt).toBe(NOW);
        expect(next[0].messages).toEqual([{ id: 'm1', content: 'hi' }]);
    });

    test('returns promoted=false when target id is unknown', () => {
        const convs: MinimalConversation[] = [
            { id: 'eph-1', title: 'A', isEphemeral: true, isActive: true, messages: [] },
        ];
        const { next, promoted } = applyPromote(convs, 'missing', NOW);
        expect(promoted).toBe(false);
        expect(next).toEqual(convs);
    });

    test('returns promoted=false when target is not ephemeral', () => {
        const convs: MinimalConversation[] = [
            { id: 'real-1', title: 'Already retained', isActive: true, messages: [] },
        ];
        const { next, promoted } = applyPromote(convs, 'real-1', NOW);
        expect(promoted).toBe(false);
        expect(next[0]).toBe(convs[0]); // unchanged reference
    });

    test('promotes only the named conversation, leaves siblings untouched', () => {
        const convs: MinimalConversation[] = [
            { id: 'eph-1', title: 'A', isEphemeral: true, isActive: true, messages: [] },
            { id: 'eph-2', title: 'B', isEphemeral: true, isActive: true, messages: [] },
            { id: 'real-1', title: 'C', isActive: true, messages: [] },
        ];
        const { next, promoted } = applyPromote(convs, 'eph-1', NOW);
        expect(promoted).toBe(true);
        expect(next[0].isEphemeral).toBeUndefined();
        expect(next[1].isEphemeral).toBe(true);
        expect(next[2]).toBe(convs[2]);
    });

    test('promoted conversation passes the EPHEMERAL GUARD on the next save', () => {
        const convs: MinimalConversation[] = [
            { id: 'eph-1', title: 'A', isEphemeral: true, isActive: true, messages: [] },
        ];
        const { next } = applyPromote(convs, 'eph-1', NOW);
        // Now the previously-ephemeral conversation must reach the save path.
        const result = applyEphemeralGuard(next, ['eph-1']);
        expect(result.shortCircuit).toBe(false);
        expect(result.filteredConversations.map(c => c.id)).toEqual(['eph-1']);
        expect(result.filteredChangedIds).toEqual(['eph-1']);
    });
});
