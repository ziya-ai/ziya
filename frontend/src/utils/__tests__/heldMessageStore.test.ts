/**
 * heldMessageStore keeps messages SHELL_GUARD holds back across a reload.
 *
 * A held streamed assistant turn used to live only in an in-memory ref
 * (pendingShellMessages).  On 2026-10-02 a 38-iteration, 13-minute turn in a
 * handoff continuation was held that way and lost.
 */
import {
    clearHeldMessages, heldMessagesKey, listHeldConversations,
    loadHeldMessages, saveHeldMessages,
} from '../heldMessageStore';

describe('heldMessageStore', () => {
    beforeEach(() => window.localStorage.clear());

    it('round-trips held messages per conversation', () => {
        const held = [{ id: 'a1', role: 'assistant', content: 'long turn' }];
        expect(saveHeldMessages('conv-1', held)).toBe(true);
        expect(loadHeldMessages('conv-1')).toEqual(held);
        expect(loadHeldMessages('conv-2')).toEqual([]);
        expect(window.localStorage.getItem(heldMessagesKey('conv-1'))).not.toBeNull();
        expect(listHeldConversations()).toEqual(['conv-1']);
    });

    it('saving an empty list clears, and clear removes', () => {
        saveHeldMessages('conv-1', [{ id: 'x' }]);
        saveHeldMessages('conv-1', []);
        expect(loadHeldMessages('conv-1')).toEqual([]);
        saveHeldMessages('conv-1', [{ id: 'x' }]);
        clearHeldMessages('conv-1');
        expect(loadHeldMessages('conv-1')).toEqual([]);
        expect(listHeldConversations()).toEqual([]);
    });

    it('a corrupt entry reads as empty instead of throwing', () => {
        window.localStorage.setItem(heldMessagesKey('conv-1'), '{not json');
        expect(loadHeldMessages('conv-1')).toEqual([]);
    });

    it('refuses an oversized payload rather than evicting other storage', () => {
        const err = jest.spyOn(console, 'error').mockImplementation(() => {});
        const huge = [{ content: 'x'.repeat(2_100_000) }];
        expect(saveHeldMessages('conv-1', huge)).toBe(false);
        expect(loadHeldMessages('conv-1')).toEqual([]);
        err.mockRestore();
    });
});
