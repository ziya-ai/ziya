/**
 * An aborted stream is not a read error.
 *
 * The only abort() on the stream's AbortController is the abort-event
 * listener in sendPayload, so an AbortError in the read loop is always a
 * deliberate stop.  The inner catch used to treat it like a network failure:
 * five console.error lines ("❌ Error reading stream: AbortError: Aborted",
 * type, message, stack, last chunk), and, when nothing had streamed yet or
 * isAborted was set, the "Stream reading error" toast.
 *
 * Source-level, like the other chatApi tests: sendPayload cannot be driven
 * without the whole streaming stack.
 *
 * DIRECTION: fails against the unpatched catch, whose first statement is the
 * console.error.
 */
import * as fs from 'fs';
import * as path from 'path';

const SRC = fs.readFileSync(path.resolve(__dirname, '..', 'chatApi.ts'), 'utf8');

function innerCatch(): string {
    const logAt = SRC.indexOf("console.error('❌ Error reading stream:'");
    expect(logAt).toBeGreaterThan(-1);
    const catchAt = SRC.lastIndexOf('} catch (error) {', logAt);
    expect(catchAt).toBeGreaterThan(-1);
    return SRC.slice(catchAt, logAt);
}

describe('readStream inner catch', () => {
    it('returns on an abort before any error logging', () => {
        const head = innerCatch();
        expect(head).toMatch(/isAborted\s*\|\|\s*\(error as any\)\?\.name === 'AbortError'/);
        expect(head).toContain('removeStreamingConversation(conversationId);');
        expect(head).toMatch(/return isAborted \? 'Response generation stopped by user\.' : ''/);
    });

    it('a genuine read error still logs and toasts', () => {
        const logAt = SRC.indexOf("console.error('❌ Error reading stream:'");
        const after = SRC.slice(logAt, logAt + 3000);
        expect(after).toContain("message.error('Stream reading error. Check JS console for details.')");
    });
});
