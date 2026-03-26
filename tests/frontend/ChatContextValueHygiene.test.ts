/**
 * Tests for ChatContext useMemo value object hygiene.
 *
 * Guards against:
 *   - Dead grouped objects (currentConversationState, globalState) that are
 *     computed on every memo invalidation but never consumed
 *   - Dead state counters (messageUpdateCounter) that are initialized but
 *     never incremented, wasting a dependency slot in useMemo and useEffect
 *
 * See: Issue #16 — Duplicate Keys in ChatContext useMemo Value Object
 */
import * as fs from 'fs';
import * as path from 'path';

const CHAT_CONTEXT_PATH = path.resolve(__dirname, '../../frontend/src/context/ChatContext.tsx');
const ACTIVE_CHAT_PATH = path.resolve(__dirname, '../../frontend/src/context/ActiveChatContext.tsx');

describe('ChatContext useMemo value hygiene', () => {
    let chatContextSrc: string;
    let activeChatSrc: string;

    beforeAll(() => {
        chatContextSrc = fs.readFileSync(CHAT_CONTEXT_PATH, 'utf-8');
        activeChatSrc = fs.readFileSync(ACTIVE_CHAT_PATH, 'utf-8');
    });

    describe('no dead grouped objects in useMemo value', () => {
        it('does not contain currentConversationState grouped object', () => {
            // The useMemo value object should not contain grouped sub-objects
            // that duplicate flat keys. These are dead weight: computed but
            // never consumed since the ChatContext interface doesn't declare them.
            const useMemoStart = chatContextSrc.indexOf('const value = useMemo');
            expect(useMemoStart).toBeGreaterThan(-1);

            // Search only within the useMemo call (roughly next 200 lines)
            const useMemoSlice = chatContextSrc.slice(useMemoStart, useMemoStart + 8000);
            expect(useMemoSlice).not.toMatch(/currentConversationState\s*:\s*\{/);
        });

        it('does not contain globalState grouped object', () => {
            const useMemoStart = chatContextSrc.indexOf('const value = useMemo');
            expect(useMemoStart).toBeGreaterThan(-1);

            const useMemoSlice = chatContextSrc.slice(useMemoStart, useMemoStart + 8000);
            expect(useMemoSlice).not.toMatch(/globalState\s*:\s*\{/);
        });
    });

    describe('no dead messageUpdateCounter state', () => {
        it('ChatContext does not declare messageUpdateCounter state', () => {
            // messageUpdateCounter was useState(0) but setMessageUpdateCounter
            // was never called anywhere — a no-op dependency that inflated
            // useMemo and useEffect dep arrays.
            expect(chatContextSrc).not.toMatch(/\[messageUpdateCounter,\s*setMessageUpdateCounter\]/);
        });

        it('ActiveChatContext interface does not include messageUpdateCounter', () => {
            // Extract the interface body
            const ifaceStart = activeChatSrc.indexOf('export interface ActiveChatContextValue');
            const ifaceEnd = activeChatSrc.indexOf('}', ifaceStart);
            expect(ifaceStart).toBeGreaterThan(-1);

            const ifaceBody = activeChatSrc.slice(ifaceStart, ifaceEnd);
            expect(ifaceBody).not.toContain('messageUpdateCounter');
        });

        it('ActiveChatProvider does not pass messageUpdateCounter', () => {
            // The provider's useMemo body should not reference it
            const providerStart = activeChatSrc.indexOf('export const ActiveChatProvider');
            expect(providerStart).toBeGreaterThan(-1);

            const providerBody = activeChatSrc.slice(providerStart);
            expect(providerBody).not.toContain('messageUpdateCounter');
        });
    });

    describe('ChatContext interface does not declare dead keys', () => {
        it('interface ChatContext has no currentConversationState or globalState', () => {
            const ifaceStart = chatContextSrc.indexOf('interface ChatContext {');
            const ifaceEnd = chatContextSrc.indexOf('}', ifaceStart);
            expect(ifaceStart).toBeGreaterThan(-1);

            const ifaceBody = chatContextSrc.slice(ifaceStart, ifaceEnd);
            expect(ifaceBody).not.toContain('currentConversationState');
            expect(ifaceBody).not.toContain('globalState');
            expect(ifaceBody).not.toContain('messageUpdateCounter');
        });
    });
});
