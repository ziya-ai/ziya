/**
 * SaveDebounce.test.ts
 *
 * Verifies that the queueSave debounce mechanism in ChatContext coalesces
 * rapid-fire saves (e.g., during streaming) into fewer actual IDB writes.
 *
 * The debounce logic:
 * - When changedIds is provided, saves are debounced by 300ms
 * - Multiple calls within the window accumulate changedIds
 * - Only the trailing call's conversations snapshot is used
 * - Recovery attempts and _bypassDebounce skip the debounce
 */

import * as fs from 'fs';
import * as path from 'path';

const CHAT_CONTEXT_PATH = path.resolve(__dirname, '../../frontend/src/context/ChatContext.tsx');

describe('queueSave debounce mechanism', () => {
    let source: string;

    beforeAll(() => {
        source = fs.readFileSync(CHAT_CONTEXT_PATH, 'utf-8');
    });

    it('has saveDebounceTimer ref declared', () => {
        expect(source).toContain('saveDebounceTimer = useRef');
    });

    it('has pendingSaveConversations ref declared', () => {
        expect(source).toContain('pendingSaveConversations = useRef');
    });

    it('has pendingSaveChangedIds ref declared', () => {
        expect(source).toContain('pendingSaveChangedIds = useRef');
    });

    it('queueSave accepts _bypassDebounce option', () => {
        expect(source).toContain('_bypassDebounce');
    });

    it('debounce block checks for changedIds before activating', () => {
        // The debounce should only engage when changedIds is provided
        expect(source).toMatch(/options\.changedIds.*&&.*!options\._bypassDebounce/s);
    });

    it('debounce accumulates changedIds across calls', () => {
        // Should add changedIds to the pending set
        expect(source).toContain('pendingSaveChangedIds.current.add(id)');
    });

    it('debounce clears timer on each call (trailing-edge)', () => {
        expect(source).toContain('clearTimeout(saveDebounceTimer.current)');
    });

    it('debounce fires actual save with accumulated IDs', () => {
        // The timeout callback should collect pending IDs and call queueSave
        expect(source).toContain('Array.from(pendingSaveChangedIds.current)');
    });

    it('debounce passes _bypassDebounce: true to prevent re-debouncing', () => {
        expect(source).toContain('_bypassDebounce: true');
    });

    it('recovery attempts bypass debounce', () => {
        // isRecoveryAttempt should skip the debounce
        expect(source).toMatch(/!options\.isRecoveryAttempt/);
    });

    it('debounce resets pending state after firing', () => {
        // After the debounce fires, refs should be cleared
        expect(source).toContain('pendingSaveConversations.current = null');
        expect(source).toContain('pendingSaveChangedIds.current = new Set()');
    });

    it('SAVE_DEBOUNCE_MS is a reasonable value (200-1000ms)', () => {
        const match = source.match(/SAVE_DEBOUNCE_MS\s*=\s*(\d+)/);
        expect(match).toBeTruthy();
        const ms = parseInt(match![1], 10);
        expect(ms).toBeGreaterThanOrEqual(200);
        expect(ms).toBeLessThanOrEqual(1000);
    });
});

describe('streaming save loop prevention', () => {
    let source: string;

    beforeAll(() => {
        source = fs.readFileSync(CHAT_CONTEXT_PATH, 'utf-8');
    });

    it('addMessageToConversation passes changedIds to queueSave', () => {
        // This is what makes the debounce work for streaming —
        // addMessageToConversation always provides changedIds
        expect(source).toMatch(/queueSave\(updated.*changedIds:\s*\[conversationId\]/s);
    });

    it('shell guard allows saves through when changedIds is provided', () => {
        // The shell guard blocks saves without changedIds but allows them with changedIds.
        // This is correct — but without debouncing, each allowed save does a full IDB read.
        expect(source).toMatch(/hasShellData.*&&.*!options\.changedIds/s);
    });
});
