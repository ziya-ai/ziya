/**
 * Tests for zombie record detection in the lazy-load trigger.
 *
 * A "zombie record" is a conversation where a shell (first+last messages
 * only) was persisted to IDB with _isShell: false, making it look like a
 * complete 2-message conversation.  The lazy-load trigger must detect
 * these and attempt a server fetch to recover the full history.
 *
 * Related fixes in ChatContext.tsx:
 * 1. `needsLazyLoad` detects conversations with ≤ 2 messages + real title
 * 2. Zombie records try IDB first (same as shells) before server fetch
 * 3. Server fetch requires > 2 messages to accept (prevents 2-msg summary
 *    from overwriting local state)
 */

// ---------- helpers --------------------------------------------------------

interface ConvEntry {
    id: string;
    title: string;
    messages: any[];
    _isShell?: boolean;
    _fullMessageCount?: number;
}

/**
 * Pure-logic extraction of the needsLazyLoad condition from ChatContext.tsx.
 * Mirrors the exact logic so we can unit-test without React.
 */
function computeNeedsLazyLoad(convEntry: ConvEntry | null): boolean {
    if (!convEntry) return false;

    const isZombieRecord = (convEntry.messages?.length ?? 0) <= 2
        && !convEntry._isShell
        && convEntry.title !== 'New Conversation'
        && convEntry.title !== '';

    return (
        (!convEntry.messages || convEntry.messages.length === 0) ||
        !!convEntry._isShell ||
        isZombieRecord
    );
}

/**
 * Pure-logic extraction of the IDB-first branch guard.
 * In ChatContext.tsx: `if (convEntry._isShell || isZombieRecord)`
 * Zombies must also try IDB before falling back to server.
 */
function shouldTryIdbFirst(convEntry: ConvEntry | null): boolean {
    if (!convEntry) return false;

    const isZombieRecord = (convEntry.messages?.length ?? 0) <= 2
        && !convEntry._isShell
        && convEntry.title !== 'New Conversation'
        && convEntry.title !== '';

    return !!convEntry._isShell || isZombieRecord;
}

/**
 * Pure-logic extraction of the server response acceptance guard.
 * In ChatContext.tsx the server response is only accepted when
 * messages.length > 2 (prevents 2-message summary from "succeeding").
 */
function shouldAcceptServerResponse(
    serverMessages: any[] | undefined,
    localMessageCount: number
): boolean {
    if (!serverMessages) return false;
    return serverMessages.length > 2 &&
        serverMessages.length >= localMessageCount;
}

// ---------- tests ----------------------------------------------------------

describe('Zombie record detection (needsLazyLoad)', () => {

    // ---- True positives: SHOULD trigger lazy-load ----

    describe('should trigger lazy-load', () => {

        test('zombie: 2 messages, _isShell false, real title', () => {
            expect(computeNeedsLazyLoad({
                id: 'abc',
                title: 'Read Docs/REFACTORING_HANDOFF.md and Doc',
                messages: [{ role: 'system' }, { role: 'user' }],
                _isShell: false,
            })).toBe(true);
        });

        test('zombie: 1 message, _isShell false, real title', () => {
            expect(computeNeedsLazyLoad({
                id: 'abc',
                title: 'Some conversation about debugging',
                messages: [{ role: 'user' }],
                _isShell: false,
            })).toBe(true);
        });

        test('zombie: 0 messages (always triggers)', () => {
            expect(computeNeedsLazyLoad({
                id: 'abc',
                title: 'Anything',
                messages: [],
            })).toBe(true);
        });

        test('shell: _isShell true (original trigger)', () => {
            expect(computeNeedsLazyLoad({
                id: 'abc',
                title: 'Some conversation',
                messages: [{ role: 'system' }, { role: 'user' }],
                _isShell: true,
                _fullMessageCount: 50,
            })).toBe(true);
        });

        test('null messages array', () => {
            expect(computeNeedsLazyLoad({
                id: 'abc',
                title: 'Has title',
                messages: null as any,
            })).toBe(true);
        });

        test('zombie: 2 messages with undefined _isShell (not set)', () => {
            // _isShell not present at all — common for server-synced records
            expect(computeNeedsLazyLoad({
                id: 'abc',
                title: 'Fork: debugging session',
                messages: [{ role: 'system' }, { role: 'user' }],
            })).toBe(true);
        });

    });

    // ---- True negatives: should NOT trigger lazy-load ----

    describe('should NOT trigger lazy-load', () => {

        test('healthy conversation with many messages', () => {
            const messages = Array.from({ length: 20 }, (_, i) => ({ role: i % 2 === 0 ? 'user' : 'assistant' }));
            expect(computeNeedsLazyLoad({
                id: 'abc',
                title: 'Normal conversation',
                messages,
                _isShell: false,
            })).toBe(false);
        });

        test('conversation with 3 messages (above zombie threshold)', () => {
            expect(computeNeedsLazyLoad({
                id: 'abc',
                title: 'Short but real conversation',
                messages: [{ role: 'system' }, { role: 'user' }, { role: 'assistant' }],
                _isShell: false,
            })).toBe(false);
        });

        test('genuinely new conversation: title "New Conversation" with 2 messages', () => {
            expect(computeNeedsLazyLoad({
                id: 'abc',
                title: 'New Conversation',
                messages: [{ role: 'system' }, { role: 'user' }],
                _isShell: false,
            })).toBe(false);
        });

        test('genuinely new conversation: empty title with 1 message', () => {
            expect(computeNeedsLazyLoad({
                id: 'abc',
                title: '',
                messages: [{ role: 'user' }],
                _isShell: false,
            })).toBe(false);
        });

        test('null convEntry', () => {
            expect(computeNeedsLazyLoad(null)).toBe(false);
        });

    });

    // ---- Edge cases ----

    describe('edge cases', () => {

        test('exactly 2 messages with emoji title', () => {
            expect(computeNeedsLazyLoad({
                id: 'abc',
                title: '🎯Orchestrator — Slack History Memory Mining',
                messages: [{ role: 'system' }, { role: 'user' }],
                _isShell: false,
            })).toBe(true);
        });

        test('exactly 3 messages is not a zombie', () => {
            expect(computeNeedsLazyLoad({
                id: 'abc',
                title: 'Some real conversation',
                messages: [{ role: 'system' }, { role: 'user' }, { role: 'assistant' }],
                _isShell: false,
            })).toBe(false);
        });

        test('zombie with _fullMessageCount still set (partial fix scenario)', () => {
            // If somehow _fullMessageCount survived but _isShell was stripped
            expect(computeNeedsLazyLoad({
                id: 'abc',
                title: 'Corrupted record',
                messages: [{ role: 'system' }, { role: 'user' }],
                _isShell: false,
                _fullMessageCount: 100,
            })).toBe(true);
        });

    });
});

describe('IDB-first branch for zombies', () => {

    test('zombie records try IDB first', () => {
        expect(shouldTryIdbFirst({
            id: 'abc',
            title: 'Read Docs/REFACTORING_HANDOFF.md',
            messages: [{ role: 'system' }, { role: 'user' }],
            _isShell: false,
        })).toBe(true);
    });

    test('shell records try IDB first (existing behavior)', () => {
        expect(shouldTryIdbFirst({
            id: 'abc',
            title: 'Some conversation',
            messages: [{ role: 'system' }, { role: 'user' }],
            _isShell: true,
            _fullMessageCount: 50,
        })).toBe(true);
    });

    test('healthy conversation does NOT try IDB-first lazy-load', () => {
        expect(shouldTryIdbFirst({
            id: 'abc',
            title: 'Normal conversation',
            messages: Array.from({ length: 20 }, () => ({ role: 'user' })),
            _isShell: false,
        })).toBe(false);
    });

    test('genuinely new conversation does NOT try IDB first', () => {
        expect(shouldTryIdbFirst({
            id: 'abc',
            title: 'New Conversation',
            messages: [{ role: 'user' }],
            _isShell: false,
        })).toBe(false);
    });

    test('null convEntry returns false', () => {
        expect(shouldTryIdbFirst(null)).toBe(false);
    });
});

describe('Server response acceptance threshold', () => {

    test('rejects 2-message server response (summary, not real data)', () => {
        expect(shouldAcceptServerResponse(
            [{ role: 'system' }, { role: 'user' }],
            2
        )).toBe(false);
    });

    test('rejects 1-message server response', () => {
        expect(shouldAcceptServerResponse(
            [{ role: 'user' }],
            1
        )).toBe(false);
    });

    test('accepts 3+ message server response', () => {
        expect(shouldAcceptServerResponse(
            [{ role: 'system' }, { role: 'user' }, { role: 'assistant' }],
            2
        )).toBe(true);
    });

    test('accepts large server response', () => {
        const msgs = Array.from({ length: 24 }, () => ({ role: 'user' }));
        expect(shouldAcceptServerResponse(msgs, 2)).toBe(true);
    });

    test('rejects server response with fewer messages than local', () => {
        expect(shouldAcceptServerResponse(
            [{ role: 'system' }, { role: 'user' }, { role: 'assistant' }],
            10
        )).toBe(false);
    });

    test('rejects undefined server messages', () => {
        expect(shouldAcceptServerResponse(undefined, 2)).toBe(false);
    });

    test('rejects empty server messages', () => {
        expect(shouldAcceptServerResponse([], 0)).toBe(false);
    });
});
