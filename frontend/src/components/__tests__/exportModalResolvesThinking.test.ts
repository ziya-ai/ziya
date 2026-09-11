/**
 * Seam guard: every export route in ExportConversationModal must send
 * messages whose thinking markers have been resolved in the browser.
 *
 * The reasoning text behind a ⟨THINKING:turn:idx⟩ marker lives only in
 * the session-scoped reasoningContentMap; the server exporter cannot
 * resolve it and emits the marker literally.  Two correct halves --
 * a resolver helper that exists, and fetch bodies that send messages --
 * are useless unless the bodies send the RESOLVED list, so this asserts
 * the connection in the source rather than trusting either half alone.
 */
import * as fs from 'fs';
import * as path from 'path';

const src = fs.readFileSync(
    path.join(__dirname, '..', 'ExportConversationModal.tsx'), 'utf8');

describe('ExportConversationModal thinking-marker resolution', () => {
    it('imports the resolver and applies it to the current messages', () => {
        expect(src).toMatch(/import\s*\{[^}]*resolveThinkingMarkersInMessages[^}]*\}\s*from\s*'\.\.\/utils\/thinkingBlocks'/);
        expect(src).toMatch(/resolveThinkingMarkersInMessages\(currentMessages,\s*reasoningContentMap\)/);
    });

    it('sends no fetch body the unresolved message list', () => {
        // Positive: the resolved list is what leaves the browser.
        const resolvedSends = src.match(/messages:\s*exportMessages\b/g) ?? [];
        expect(resolvedSends.length).toBeGreaterThanOrEqual(2); // PDF + HTML routes
        // The markdown route sends filteredMessages, which must derive from
        // exportMessages, not currentMessages.
        expect(src).toMatch(/let msgs = \[\.\.\.exportMessages\]/);
        // Negative: nothing still ships the raw list.
        expect(src).not.toMatch(/messages:\s*currentMessages\b/);
        expect(src).not.toMatch(/let msgs = \[\.\.\.currentMessages\]/);
    });
});
