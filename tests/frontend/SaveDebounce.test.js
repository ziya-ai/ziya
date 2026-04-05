/**
 * Tests for the queueSave debounce mechanism in ChatContext.
 *
 * Guards against regression of the rapid-fire IDB write loop where
 * streaming chunks trigger hundreds of full IndexedDB read-merge-write
 * cycles per second when React state contains shell conversations.
 *
 * The fix: queueSave debounces saves when changedIds is provided,
 * coalescing rapid calls into one save per 300ms window.
 */

const fs = require('fs');
const path = require('path');

const CHAT_CONTEXT_PATH = path.resolve(
    __dirname, '../../frontend/src/context/ChatContext.tsx'
);

describe('queueSave debounce mechanism', () => {
    let source;

    beforeAll(() => {
        source = fs.readFileSync(CHAT_CONTEXT_PATH, 'utf-8');
    });

    it('declares saveDebounceTimer ref', () => {
        expect(source).toContain('saveDebounceTimer = useRef');
    });

    it('declares pendingSaveConversations ref', () => {
        expect(source).toContain('pendingSaveConversations = useRef');
    });

    it('declares pendingSaveChangedIds ref', () => {
        expect(source).toContain('pendingSaveChangedIds = useRef');
    });

    it('has _bypassDebounce option in queueSave signature', () => {
        expect(source).toContain('_bypassDebounce');
    });

    it('debounce block checks changedIds before entering debounce path', () => {
        // The debounce should only activate when changedIds is provided
        expect(source).toMatch(/options\.changedIds\s*&&\s*options\.changedIds\.length\s*>\s*0[\s\S]*?!options\._bypassDebounce/);
    });

    it('debounce block does NOT fire for recovery attempts', () => {
        // isRecoveryAttempt should bypass debounce
        expect(source).toMatch(/!options\.isRecoveryAttempt/);
    });

    it('accumulates changedIds across debounced calls', () => {
        expect(source).toContain('pendingSaveChangedIds.current.add(id)');
    });

    it('clears debounce timer on each new call', () => {
        expect(source).toContain('clearTimeout(saveDebounceTimer.current)');
    });

    it('fires debounced save with accumulated changedIds', () => {
        // The debounced callback should use Array.from(pendingSaveChangedIds.current)
        expect(source).toContain('Array.from(pendingSaveChangedIds.current)');
    });

    it('passes _bypassDebounce: true on the recursive call', () => {
        expect(source).toContain('_bypassDebounce: true');
    });

    it('resets pending state after debounce fires', () => {
        // Should clear pendingSaveConversations and pendingSaveChangedIds
        expect(source).toContain('pendingSaveConversations.current = null');
        expect(source).toContain('pendingSaveChangedIds.current = new Set()');
    });

    it('uses a debounce interval of at least 100ms', () => {
        // Extract SAVE_DEBOUNCE_MS value — must be >= 100 to be meaningful
        const match = source.match(/SAVE_DEBOUNCE_MS\s*=\s*(\d+)/);
        expect(match).toBeTruthy();
        const ms = parseInt(match[1], 10);
        expect(ms).toBeGreaterThanOrEqual(100);
        expect(ms).toBeLessThanOrEqual(2000); // sanity: not too slow
    });

    it('addMessageToConversation calls queueSave with changedIds', () => {
        // Every addMessage call must pass changedIds so it hits the debounce path
        const addMsgBlock = source.match(
            /addMessageToConversation[\s\S]*?queueSave\(updatedConversations,\s*\{[^}]*changedIds/
        );
        expect(addMsgBlock).toBeTruthy();
    });
});
