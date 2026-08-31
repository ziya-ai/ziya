/**
 * Every retry/continue send in StreamedContent must bind its target
 * conversation EXPLICITLY.
 *
 * Why static wiring assertions: the defect class is an omitted argument
 * whose default is resolved from a DIFFERENT source than the handler's
 * guard.  Each retry handler guards with a closure-captured
 * currentConversationId (frozen at effect registration), while
 * useSendPayload.send() defaults a missing `conversationId` to the LIVE
 * currentConversationId read from a ref at call time.  Between a
 * conversation switch's render (ref updated) and the passive-effect
 * re-registration of the listener (closure updated), the two disagree:
 * the guard passes for the OLD conversation while send() binds the
 * retry to the NEW one — regenerating the old conversation's turn into
 * whichever conversation (any project) is on screen.
 *
 * This was the cross-project conversation leak: the
 * retryStreamInterruption dispatch is a setTimeout(800ms) after a
 * stream read error (network drop / OS sleep-wake), exactly when users
 * switch around, and the leaked content carries no error framing
 * because the interruption sentinel is an invisible span stripped
 * before the retry.
 *
 * The invariant: whatever id a handler validated/derived its messages
 * from is the id its send() must be bound to.  A unit test on the
 * handlers can pass while the argument is silently dropped, so these
 * assert the call sites directly.
 */

import * as fs from 'fs';
import * as path from 'path';

const SRC = fs.readFileSync(
    path.join(__dirname, '../StreamedContent.tsx'),
    'utf8'
);

/**
 * Slice the source between a handler's declaration and the point where
 * it is registered, so assertions scope to one handler's body.
 */
function handlerBlock(declaration: string, registration: string): string {
    const start = SRC.indexOf(declaration);
    const end = SRC.indexOf(registration, start);
    if (start === -1 || end === -1 || end <= start) {
        throw new Error(
            `Could not locate handler block between "${declaration}" and "${registration}"`
        );
    }
    return SRC.slice(start, end);
}

const RETRY_HANDLERS: Array<[name: string, declaration: string, registration: string]> = [
    [
        'handleRetryAuthError',
        'const handleRetryAuthError',
        "window.addEventListener('retryAuthError'",
    ],
    [
        'handleRetryContextError',
        'const handleRetryContextError',
        "window.addEventListener('retryContextError'",
    ],
    [
        'handleRetryStreamInterruption',
        'const handleRetryStreamInterruption',
        "window.addEventListener('retryStreamInterruption'",
    ],
];

describe('retry sends are explicitly bound to the retried conversation', () => {
    it.each(RETRY_HANDLERS)(
        '%s passes conversationId: retryConversationId to send()',
        (_name, declaration, registration) => {
            const block = handlerBlock(declaration, registration);
            // The send options object in these handlers contains no nested
            // braces, so [^}]* safely scopes the match to one call.
            expect(block).toMatch(
                /await send\(\{[^}]*conversationId:\s*retryConversationId/
            );
        }
    );

    it.each(RETRY_HANDLERS)(
        '%s does not hardcode isStreamingToCurrentConversation (computed at call time)',
        (_name, declaration, registration) => {
            const block = handlerBlock(declaration, registration);
            expect(block).not.toMatch(/isStreamingToCurrentConversation:\s*true/);
        }
    );

    it('handlePreservedContinue binds send() to the conversation it read messages from', () => {
        const block = handlerBlock(
            'const handlePreservedContinue',
            '}, [currentConversationId, isRetrying'
        );
        expect(block).toMatch(
            /await send\(\{[^}]*conversationId:\s*currentConversationId/
        );
    });

    it('positive control: the guarded handlers really do guard on retryConversationId', () => {
        // If a refactor renames the guard variable, the binding assertions
        // above could pass vacuously against the wrong identifier; pin the
        // guard so both halves of the invariant are visible.
        for (const [, declaration, registration] of RETRY_HANDLERS) {
            const block = handlerBlock(declaration, registration);
            expect(block).toMatch(/retryConversationId !== currentConversationId/);
        }
    });
});
