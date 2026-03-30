/**
 * Tests for memory leak fixes in ChatContext.
 *
 * Guards against regressions of:
 *   1. GC effect re-creating its interval on every stream chunk because
 *      streamingConversations/currentConversationId were in its dep array.
 *      Fix: use streamingConversationsRef.current and currentConversationRef.current.
 *
 *   2. reasoningContentMap entries never cleaned up when streaming ends.
 *      Fix: removeStreamingConversation now calls setReasoningContentMap with delete.
 *
 *   3. processingStates Map growing unbounded (entries set to 'idle' but
 *      never removed). Fix: entries are deleted, not idled.
 *
 *   4. currentMessages in useEffect dep array causing a render loop.
 *      Fix: comparison moved into functional setCurrentMessages updater.
 */
import * as fs from 'fs';
import * as path from 'path';

const SRC = path.resolve(__dirname, '../../frontend/src/context/ChatContext.tsx');

describe('ChatContext memory leak fixes', () => {
    let src: string;

    beforeAll(() => {
        src = fs.readFileSync(SRC, 'utf-8');
    });

    // -------------------------------------------------------------------------
    // Leak 1: GC effect dep array
    // -------------------------------------------------------------------------
    describe('GC effect does not depend on streaming state values', () => {
        it('uses streamingConversationsRef.current inside runGc, not streamingConversations directly', () => {
            // Find the GC useEffect block
            const gcStart = src.indexOf('GC_INTERVAL_MS');
            expect(gcStart).toBeGreaterThan(-1);
            const gcBlock = src.slice(gcStart, gcStart + 1500);

            expect(gcBlock).toContain('streamingConversationsRef.current');
            expect(gcBlock).not.toMatch(/new Set<string>\(streamingConversations\)/);
        });

        it('uses currentConversationRef.current inside runGc, not currentConversationId directly', () => {
            const gcStart = src.indexOf('GC_INTERVAL_MS');
            const gcBlock = src.slice(gcStart, gcStart + 1500);

            expect(gcBlock).toContain('currentConversationRef.current');
        });

        it('GC effect dep array does not include streamingConversations or currentConversationId', () => {
            // Find the closing dep array of the GC effect (the one with GC_INTERVAL_MS)
            const gcStart = src.indexOf('GC_INTERVAL_MS');
            // The dep array comes after the effect body; search forward for ], [
            const gcBlock = src.slice(gcStart, gcStart + 2000);
            // The closing dep array line should not list streaming/currentConversation
            const depArrayMatch = gcBlock.match(/\},\s*\[([^\]]*)\]/);
            expect(depArrayMatch).not.toBeNull();
            const depArray = depArrayMatch![1];
            expect(depArray).not.toContain('streamingConversations,');
            expect(depArray).not.toContain('currentConversationId,');
            expect(depArray).not.toContain('streamingConversations]');
            expect(depArray).not.toContain('currentConversationId]');
        });
    });

    // -------------------------------------------------------------------------
    // Leak 2: reasoningContentMap cleanup
    // -------------------------------------------------------------------------
    describe('removeStreamingConversation cleans up reasoningContentMap', () => {
        it('calls setReasoningContentMap with a delete inside removeStreamingConversation', () => {
            const removeStart = src.indexOf('const removeStreamingConversation = useCallback');
            expect(removeStart).toBeGreaterThan(-1);
            // useCallback ends at its closing ], [...]); — grab enough chars
            const removeBlock = src.slice(removeStart, removeStart + 2000);

            expect(removeBlock).toContain('setReasoningContentMap');
            // Confirm it deletes, not just sets
            const reasoningIdx = removeBlock.indexOf('setReasoningContentMap');
            const reasoningSnippet = removeBlock.slice(reasoningIdx, reasoningIdx + 200);
            expect(reasoningSnippet).toContain('next.delete(id)');
        });
    });

    // -------------------------------------------------------------------------
    // Leak 3: processingStates cleanup
    // -------------------------------------------------------------------------
    describe('removeStreamingConversation deletes from processingStates, not idles', () => {
        it('does not set processingState to idle on stream end', () => {
            const removeStart = src.indexOf('const removeStreamingConversation = useCallback');
            const removeBlock = src.slice(removeStart, removeStart + 2000);

            // Should not set state to 'idle' — that was the leaking pattern
            expect(removeBlock).not.toMatch(/next\.set\(id,\s*\{[^}]*state:\s*['"]idle['"]/);
        });

        it('deletes the processingState entry when streaming ends', () => {
            const removeStart = src.indexOf('const removeStreamingConversation = useCallback');
            const removeBlock = src.slice(removeStart, removeStart + 2000);

            const psIdx = removeBlock.indexOf('setProcessingStates');
            expect(psIdx).toBeGreaterThan(-1);
            const psSnippet = removeBlock.slice(psIdx, psIdx + 200);
            expect(psSnippet).toContain('next.delete(id)');
        });
    });

    // -------------------------------------------------------------------------
    // Leak 4: currentMessages render loop
    // -------------------------------------------------------------------------
    describe('currentMessages useEffect does not create a render loop', () => {
        it('messages useEffect dep array does not include currentMessages', () => {
            // Find the useEffect that syncs currentMessages
            const effectMarker = 'conversations.find(c => c.id === currentConversationId)';
            const effectStart = src.indexOf(effectMarker);
            expect(effectStart).toBeGreaterThan(-1);

            // Get the dep array of this effect (search forward for ], [...])
            const effectBlock = src.slice(effectStart, effectStart + 800);
            const depMatch = effectBlock.match(/\},\s*\[([^\]]*)\]/);
            expect(depMatch).not.toBeNull();
            const deps = depMatch![1];
            expect(deps).not.toContain('currentMessages');
        });

        it('messages comparison is performed inside a functional setCurrentMessages updater', () => {
            const effectMarker = 'conversations.find(c => c.id === currentConversationId)';
            const effectStart = src.indexOf(effectMarker);
            const effectBlock = src.slice(Math.max(0, effectStart - 200), effectStart + 800);

            // The pattern should be setCurrentMessages(prev => { ... })
            expect(effectBlock).toMatch(/setCurrentMessages\s*\(\s*prev\s*=>/);
        });
    });
});
