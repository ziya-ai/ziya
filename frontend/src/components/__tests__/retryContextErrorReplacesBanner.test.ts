/**
 * A context-error retry must REPLACE its banner in the history, not stack
 * a new one beneath it.
 *
 * Observed 2026-10-20: three "Content Too Large" banners stacked in one
 * conversation, each with its own Retry button.  handleRetryContextError
 * spliced the banner out of React state only.  bulk-sync merges by id and
 * never removes a message, so the server record kept the banner; the
 * stream-end sync (which runs right after the retry finishes, including
 * when it fails again) pulled it back, and the retry's own error landed
 * after it.  handleRetryAuthError had already been given the server-side
 * delete for the same reason; the context handler had not.
 *
 * Static assertions on the handler body, scoped between its declaration
 * and its registration (the pattern retrySendBinding.test.ts uses), so
 * the seam — "the splice is mirrored to the server" — is what is pinned.
 */
import * as fs from 'fs';
import * as path from 'path';

const SRC = fs.readFileSync(path.join(__dirname, '../StreamedContent.tsx'), 'utf8');

function handlerBlock(declaration: string, registration: string): string {
    const start = SRC.indexOf(declaration);
    const end = SRC.indexOf(registration, start);
    if (start === -1 || end === -1 || end <= start) {
        throw new Error(`Could not locate handler block between "${declaration}" and "${registration}"`);
    }
    return SRC.slice(start, end);
}

const CONTEXT = handlerBlock('const handleRetryContextError', "window.addEventListener('retryContextError'");
const AUTH = handlerBlock('const handleRetryAuthError', "window.addEventListener('retryAuthError'");

describe('context-error retry replaces its banner', () => {
    it('deletes the banner on the server, not only in React state', () => {
        expect(CONTEXT).toMatch(/deleteMessage\(pid,\s*retryConversationId,\s*loc\)/);
        expect(CONTEXT).toMatch(/messageLocator\(conv\.messages,\s*i\)/);
        expect(CONTEXT).toMatch(/ownerProjectId\(conv\)/);
    });

    it('drops every error-only turn since the last human message, not just the newest', () => {
        // The splice loop must stop at a human message and must not break
        // after the first banner it finds.
        const splice = CONTEXT.slice(CONTEXT.indexOf('setConversations('), CONTEXT.indexOf('return { ...conv, messages: msgs'));
        expect(splice).toMatch(/msgs\[i\]\.role === 'human'\)\s*break;/);
        expect(splice).toMatch(/context-error-retry-button/);
        expect(splice).not.toMatch(/msgs\.splice\(i,\s*1\);\s*break;/);
    });

    it('still bumps _version so a longer IDB copy is not adopted over the shortened list', () => {
        expect(CONTEXT).toMatch(/_version:\s*Date\.now\(\)/);
    });

    it('positive control: the auth handler it mirrors carries the same server delete', () => {
        expect(AUTH).toMatch(/deleteMessage\(pid,\s*retryConversationId,\s*loc\)/);
    });
});
