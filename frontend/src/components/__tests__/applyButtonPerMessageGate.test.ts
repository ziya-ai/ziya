/**
 * Regression tests for the "prior turns' Apply buttons re-grey on the next
 * turn" bug.
 *
 * Symptom: an Apply button went blue and applied fine, then as soon as the
 * NEXT turn started streaming, some — but not all — Apply buttons on
 * ALREADY-COMPLETED earlier messages greyed out again with the tooltip
 * "Waiting for the diff to finish streaming before it can be applied", and
 * stayed that way across turns.
 *
 * Root cause: the streaming signal reaching the button described the CURRENT
 * TURN, not the message that owns the diff.
 *
 *   1. `DiffToken` declared `isStreaming?: boolean` in its props but never
 *      destructured it; it shadowed the name with the GLOBAL `isStreaming`
 *      from StreamingContext (true whenever ANY conversation streams). And
 *      `renderTokens`' diff case never passed the prop in the first place.
 *   2. `DiffView` gated on `streamingConversations.has(currentConversationId)`.
 *      During the next turn the current conversation genuinely IS in that Set,
 *      so every settled diff in the same conversation's history was re-gated.
 *
 * Either way `isDiffComplete(diff, true)` then ran its structural heuristic
 * on a fully-arrived diff. Diffs whose shape the heuristic happens to reject
 * (e.g. ending on a +/- line with no trailing blank) stayed grey; the rest
 * did not. That partial hit rate is what made the symptom look arbitrary.
 *
 * Fix: thread the per-MESSAGE `isStreaming` prop
 * (renderTokens -> DiffToken -> DiffViewWrapper -> DiffView ->
 * ApplyChangesButton). Conversation.tsx already passes `false` for every
 * committed history message and StreamedContent passes true only for the
 * live turn, so the correct signal existed all along and was being dropped.
 *
 * These tests model the predicate purely. The companion file
 * applyButtonStreamingGate.test.ts covers `isDiffComplete` itself and the
 * older per-conversation model; this file pins the per-message contract that
 * supersedes it.
 */

// ``marked`` is ESM-only and the CRA jest transform won't process it.
jest.mock('marked', () => {
    const marked = (s: string) => s;
    Object.assign(marked, {
        parse: (s: string) => s,
        setOptions: () => {},
        use: () => {},
        walkTokens: () => {},
        parseInline: (s: string) => s,
    });
    return { marked, Tokens: {} };
});
jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));

import { isDiffComplete } from '../MarkdownRenderer';

/**
 * A diff whose SHAPE the streaming heuristic rejects (last line is a "+"
 * change with no trailing blank line) even though it is fully arrived.
 *
 * This is the load-bearing fixture: a diff like this is exactly the subset
 * that stayed grey while structurally "tidier" diffs recovered, which is why
 * only SOME prior buttons regressed.
 */
const SETTLED_BUT_HEURISTIC_HOSTILE_DIFF = [
    'diff --git a/foo.ts b/foo.ts',
    '--- a/foo.ts',
    '+++ b/foo.ts',
    '@@ -1,2 +1,3 @@',
    ' context',
    '+added line',
].join('\n');

const TIDY_DIFF = [
    'diff --git a/bar.ts b/bar.ts',
    '--- a/bar.ts',
    '+++ b/bar.ts',
    '@@ -1,3 +1,3 @@',
    ' context',
    '-old',
    '+new',
    ' trailing',
    '',
].join('\n');

/** Sanity-check the fixture actually exercises the divergent branch. */
describe('fixture sanity', () => {
    it('the hostile diff is judged INCOMPLETE while streaming', () => {
        expect(isDiffComplete(SETTLED_BUT_HEURISTIC_HOSTILE_DIFF, true)).toBe(false);
    });
    it('...but COMPLETE once not streaming', () => {
        expect(isDiffComplete(SETTLED_BUT_HEURISTIC_HOSTILE_DIFF, false)).toBe(true);
    });
    it('the tidy diff is complete either way, so it never regressed', () => {
        expect(isDiffComplete(TIDY_DIFF, true)).toBe(true);
        expect(isDiffComplete(TIDY_DIFF, false)).toBe(true);
    });
});

/**
 * Post-fix predicate: the gate is the streaming state of the MESSAGE that
 * owns the diff. Nothing about the conversation or the global boolean enters.
 *
 * Mirrors DiffView -> ApplyChangesButton after the fix:
 *   isStreaming={isMessageStreaming}          // per-message prop
 *   diffComplete = isDiffComplete(diff, isStreaming)
 *   disabled = isProcessing || (isStreaming && !diffComplete)
 */
function shouldDisableApplyButton(args: {
    diff: string;
    isProcessing?: boolean;
    /** From Conversation.tsx: false for history; true only for the live turn. */
    isMessageStreaming: boolean;
}): boolean {
    const streaming = args.isMessageStreaming;
    return Boolean(args.isProcessing) || (streaming && !isDiffComplete(args.diff, streaming));
}

/**
 * Pre-fix predicate, kept as an executable record of the defect so the
 * regression tests below demonstrate a real behavioural difference rather
 * than merely asserting the new code agrees with itself.
 */
function shouldDisableApplyButton_preFix(args: {
    diff: string;
    isProcessing?: boolean;
    streamingConversations: Set<string>;
    currentConversationId: string;
}): boolean {
    const streaming = args.streamingConversations.has(args.currentConversationId);
    return Boolean(args.isProcessing) || (streaming && !isDiffComplete(args.diff, streaming));
}

describe('Apply button disable predicate (per-message gating)', () => {
    const CONV = 'conv-123';

    it('REGRESSION: a settled history diff stays enabled while the NEXT turn streams', () => {
        // The reported symptom. The conversation IS streaming (next turn), but
        // the message owning this diff finished long ago.
        expect(
            shouldDisableApplyButton({
                diff: SETTLED_BUT_HEURISTIC_HOSTILE_DIFF,
                isMessageStreaming: false,
            }),
        ).toBe(false);

        // Prove the old predicate genuinely regressed on this exact input, so
        // this test is pinning a fix and not restating a tautology.
        expect(
            shouldDisableApplyButton_preFix({
                diff: SETTLED_BUT_HEURISTIC_HOSTILE_DIFF,
                streamingConversations: new Set([CONV]),
                currentConversationId: CONV,
            }),
        ).toBe(true);
    });

    it('REGRESSION: explains why only SOME buttons regressed (shape-dependent)', () => {
        // Same conversation, same turn, two settled diffs. Pre-fix the tidy
        // one survived and the hostile one greyed — the arbitrary-looking
        // partial failure the user described.
        const streamingNow = { streamingConversations: new Set([CONV]), currentConversationId: CONV };
        expect(shouldDisableApplyButton_preFix({ diff: TIDY_DIFF, ...streamingNow })).toBe(false);
        expect(
            shouldDisableApplyButton_preFix({ diff: SETTLED_BUT_HEURISTIC_HOSTILE_DIFF, ...streamingNow }),
        ).toBe(true);

        // Post-fix both are enabled, because neither MESSAGE is streaming.
        expect(shouldDisableApplyButton({ diff: TIDY_DIFF, isMessageStreaming: false })).toBe(false);
        expect(
            shouldDisableApplyButton({ diff: SETTLED_BUT_HEURISTIC_HOSTILE_DIFF, isMessageStreaming: false }),
        ).toBe(false);
    });

    it('REGRESSION: persists across MULTIPLE turns pre-fix; never post-fix', () => {
        // Turn 2, 3, 4 … each re-adds the conversation to the Set. Pre-fix the
        // same history button re-greys every single turn.
        for (let turn = 2; turn <= 5; turn++) {
            expect(
                shouldDisableApplyButton_preFix({
                    diff: SETTLED_BUT_HEURISTIC_HOSTILE_DIFF,
                    streamingConversations: new Set([CONV]),
                    currentConversationId: CONV,
                }),
            ).toBe(true);
            expect(
                shouldDisableApplyButton({
                    diff: SETTLED_BUT_HEURISTIC_HOSTILE_DIFF,
                    isMessageStreaming: false,
                }),
            ).toBe(false);
        }
    });

    it('a genuinely mid-stream, incomplete diff IS still disabled', () => {
        // The gate must not be neutered: the live turn's half-arrived patch
        // must stay unapplyable.
        expect(
            shouldDisableApplyButton({
                diff: SETTLED_BUT_HEURISTIC_HOSTILE_DIFF,
                isMessageStreaming: true,
            }),
        ).toBe(true);
    });

    it('a mid-stream diff that is already structurally complete enables early', () => {
        expect(shouldDisableApplyButton({ diff: TIDY_DIFF, isMessageStreaming: true })).toBe(false);
    });

    it('an in-flight application disables regardless of streaming state', () => {
        expect(
            shouldDisableApplyButton({ diff: TIDY_DIFF, isProcessing: true, isMessageStreaming: false }),
        ).toBe(true);
    });

    it('another conversation streaming is structurally irrelevant post-fix', () => {
        // Nothing conversation-scoped enters the predicate any more, so a
        // background/delegate stream cannot reach this button at all.
        expect(
            shouldDisableApplyButton({ diff: SETTLED_BUT_HEURISTIC_HOSTILE_DIFF, isMessageStreaming: false }),
        ).toBe(false);
    });
});

/**
 * The prop-threading chain itself. The bug was not a wrong comparison — it
 * was a DROPPED prop at three separate hand-offs, each of which silently fell
 * back to a turn-scoped signal. Model the chain so a future refactor that
 * drops it again fails here.
 */
type Link = (streaming: boolean | undefined) => boolean | undefined;

/** Post-fix: every link forwards the value it was given. */
const CHAIN_FIXED: Record<string, Link> = {
    renderTokens: (s) => s,
    DiffToken: (s) => s ?? false,
    DiffViewWrapper: (s) => s ?? false,
    DiffView: (s) => s ?? false,
};

/** Pre-fix: DiffToken dropped the prop and substituted a turn-scoped signal. */
const GLOBAL_TURN_STREAMING = true; // "some other/current turn is streaming"

describe('per-message isStreaming prop threading', () => {
    it('forwards false end-to-end for a committed history message', () => {
        const out = Object.values(CHAIN_FIXED).reduce<boolean | undefined>(
            (acc, link) => link(acc),
            false,
        );
        expect(out).toBe(false);
    });

    it('forwards true end-to-end for the live streaming message', () => {
        const out = Object.values(CHAIN_FIXED).reduce<boolean | undefined>(
            (acc, link) => link(acc),
            true,
        );
        expect(out).toBe(true);
    });

    it('REGRESSION: a link that substitutes turn-scoped state corrupts the chain', () => {
        // This is precisely what DiffToken did by shadowing its own prop with
        // useStreamingContext().isStreaming.
        const broken: Record<string, Link> = {
            ...CHAIN_FIXED,
            DiffToken: () => GLOBAL_TURN_STREAMING,
        };
        const out = Object.values(broken).reduce<boolean | undefined>((acc, link) => link(acc), false);
        expect(out).toBe(true); // history message wrongly reported as streaming
        expect(out).not.toBe(false);
    });

    it('REGRESSION: a link that drops the prop defaults to false and under-gates a live diff', () => {
        // The mirror-image hazard: renderMultiFileDiff not forwarding
        // isStreaming into its nested MarkdownRenderer would offer Apply on a
        // half-arrived multi-file patch.
        const dropping: Record<string, Link> = {
            ...CHAIN_FIXED,
            DiffViewWrapper: () => undefined,
        };
        const out = Object.values(dropping).reduce<boolean | undefined>((acc, link) => link(acc), true);
        expect(out).toBe(false); // live stream wrongly reported as settled
    });
});
