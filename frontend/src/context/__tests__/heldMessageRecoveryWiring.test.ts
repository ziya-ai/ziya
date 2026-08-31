/**
 * Static wiring guard for the two "parked hazard" fixes.
 *
 * WHY STATIC RATHER THAN A RENDER TEST
 *
 * Both fixes are call-site wiring around pure helpers that are unit-tested
 * directly (heldMessagePartition.test.ts, deletionPassGuard.test.ts).  Those
 * unit suites pass whether or not anything CALLS the helpers — which is
 * exactly how the original SHELL_GUARD defect survived: the decision was
 * correct in principle and computed inline, wrongly, at the call site.
 *
 * Driving these in jsdom would mean standing up ChatProvider, a fake
 * IndexedDB, a fake server, a real composer and a project switch, to assert
 * something whose actual content is "this call site exists and is ordered
 * correctly".  Pinning the call sites is cheaper and more direct.
 *
 * Every assertion runs against COMMENT-STRIPPED source, because both fixes
 * are documented in comments that quote the very identifiers being asserted
 * — a naive substring match would pass on prose alone.
 */
import * as fs from 'fs';
import * as path from 'path';

const read = (rel: string): string =>
    fs.readFileSync(path.resolve(__dirname, '..', '..', rel), 'utf8');

/** Strip `//` line comments so prose cannot satisfy a match. */
const stripComments = (src: string): string =>
    src.split('\n').filter(l => !l.trim().startsWith('//')).join('\n');

const CTX = read('context/ChatContext.tsx');
const CTX_CODE = stripComments(CTX);
const INJECT_CODE = stripComments(read('utils/composerInject.ts'));
const SENDER_CODE = stripComments(read('components/SendChatContainer.tsx'));

/**
 * Body of the SHELL_GUARD hold branch: from its condition to the start of
 * the success path.  Anchored on `const toApply`, which is the first
 * statement AFTER the branch returns — so a hold-branch assertion cannot
 * accidentally be satisfied by code belonging to the apply path.
 */
const holdBranch = (): string => {
    const start = CTX_CODE.indexOf("outcome.action === 'hold'");
    expect(start).toBeGreaterThan(-1);
    const end = CTX_CODE.indexOf('const toApply', start);
    expect(end).toBeGreaterThan(start);
    return CTX_CODE.slice(start, end);
};

describe('hold path returns the user their text', () => {
    it('imports the partition helpers', () => {
        expect(CTX_CODE).toMatch(/import\s*\{[^}]*partitionHeldMessages[^}]*\}\s*from\s*'\.\.\/utils\/shellRecovery'/s);
        expect(CTX_CODE).toContain('composerTextFromHeld');
    });

    it('imports the composer-injection dispatcher', () => {
        expect(CTX_CODE).toMatch(/import\s*\{[^}]*dispatchComposerInject[^}]*\}\s*from\s*'\.\.\/utils\/composerInject'/s);
    });

    it('partitions the held queue instead of discarding or keeping it wholesale', () => {
        expect(holdBranch()).toContain('partitionHeldMessages');
    });

    it('re-queues the non-composable remainder rather than dropping it', () => {
        // A streamed assistant turn exists nowhere else; the hold must not
        // clear the queue outright now that it also empties part of it.
        const branch = holdBranch();
        expect(branch).toContain('keepQueued');
        expect(branch).toMatch(/queue\.set\(/);
    });

    it('hands the recovered human text to the composer', () => {
        const branch = holdBranch();
        expect(branch).toContain('composerTextFromHeld');
        expect(branch).toContain('dispatchComposerInject');
    });

    it('injects without clobbering anything the user has since typed', () => {
        // The hold can land seconds later (bounded by the getChat deadline).
        // A plain replace would delete newer keystrokes.
        expect(holdBranch()).toContain('preserveExisting');
    });

    it('still tells the user, and still returns before the apply path', () => {
        // Positive controls: the branch must keep the behaviour it already had.
        const branch = holdBranch();
        expect(branch).toContain('uiMessage.error');
        expect(branch).toMatch(/\breturn\s*;/);
    });

    it('still refuses to append onto an unverified array (positive control)', () => {
        // The reason the hold exists at all.  If this regressed, the fix above
        // would be decorating a defect rather than completing one.
        const branch = holdBranch();
        expect(branch).not.toContain('setConversations');
    });
});

describe('composer injection supports non-destructive delivery', () => {
    it('accepts the preserveExisting option in the event contract', () => {
        expect(INJECT_CODE).toContain('preserveExisting');
    });

    it('keeps the option optional so existing dispatchers are unaffected', () => {
        // Backlog resume / seam ribbon / branch pickup all want replace.
        expect(INJECT_CODE).toMatch(/preserveExisting\?\s*:/);
    });

    it('is honoured by the consumer, not merely declared', () => {
        // The defect class this whole session has been about: a field added to
        // a type and never read by the code that mounts it.
        expect(SENDER_CODE).toContain('preserveExisting');
    });

    it('still replaces by default (positive control)', () => {
        // The existing contract must survive: a plain inject overwrites.
        expect(SENDER_CODE).toMatch(/textContent\s*=/);
    });
});

describe('deletion pass is gated on a trustworthy server list', () => {
    it('consults the guard helper', () => {
        expect(CTX_CODE).toContain('shouldRunDeletionPass');
    });

    it('feeds it the server count and the known-id count', () => {
        const idx = CTX_CODE.indexOf('shouldRunDeletionPass');
        expect(idx).toBeGreaterThan(-1);
        const call = CTX_CODE.slice(idx, idx + 260);
        expect(call).toContain('serverChats.length');
        expect(call).toContain('knownServerConversationIds.current.size');
    });

    it('actually gates the splice, not just computes a flag', () => {
        // The whole failure mode is a helper that is called and ignored.
        const start = CTX_CODE.indexOf('const deletedIds');
        expect(start).toBeGreaterThan(-1);
        const end = CTX_CODE.indexOf('4. Push local-only', start);
        const loop = CTX_CODE.slice(start, end > start ? end : start + 2600);
        expect(loop).toMatch(/runDeletionPass/);
        expect(loop).toContain('mergedProjectConvs.splice');
    });

    it('retains every pre-existing per-conversation guard (positive controls)', () => {
        // The list-level guard must ADD safety, never replace the narrower
        // protections that already exist.
        const start = CTX_CODE.indexOf('const deletedIds');
        const end = CTX_CODE.indexOf('4. Push local-only', start);
        const loop = CTX_CODE.slice(start, end > start ? end : start + 2600);
        expect(loop).toContain('currentConversationRef.current');
        expect(loop).toContain('SYNC_GRACE_PERIOD_MS');
        expect(loop).toContain('knownServerConversationIds.current.has');
    });
});
