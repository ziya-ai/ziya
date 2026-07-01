/**
 * Regression tests for the Apply-button "still streaming" greyed-out bug.
 *
 * Symptom: the diff Apply button stayed disabled with the tooltip
 * "Waiting for the diff to finish streaming before it can be applied"
 * even after the stream had completed — in some cases persisting into
 * following turns.
 *
 * Root cause (mode 2): the button gated on the GLOBAL `isStreaming`
 * boolean from StreamingContext, which is an independently-mutated
 * mirror of `streamingConversations.size > 0`. That boolean could
 * desync and stay stuck `true` while the `streamingConversations` Set
 * was empty (confirmed live: empty Set + isStreaming===true, no sidebar
 * spinners). Because `isDiffComplete(diff, true)` then runs its
 * structural heuristic, a settled diff whose shape the heuristic judged
 * incomplete kept the button permanently greyed.
 *
 * Fix: gate the button on THIS conversation's streaming membership
 * (`streamingConversations.has(currentConversationId)`) — the Set is the
 * authoritative source of truth — instead of the global boolean.
 *
 * This file covers:
 *  1. `isDiffComplete` directly (the pure structural heuristic), with
 *     particular attention to the not-streaming short-circuit that the
 *     fix relies on.
 *  2. A pure model of the button's disable predicate, pinning the
 *     regression: a stale global `isStreaming===true` must NOT disable
 *     the button when the current conversation is not in the streaming
 *     Set.
 */

// ``marked`` is ESM-only and the CRA jest transform won't process it.
// Stub at module scope so importing the MarkdownRenderer module (which we
// only need for the pure ``isDiffComplete`` helper) doesn't fail when its
// top-level ``marked`` import resolves.
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
// ``uuid`` is also ESM-only and pulled in transitively via the
// FolderContext → ProjectContext → db.ts chain MarkdownRenderer imports.
jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));

import { isDiffComplete } from '../MarkdownRenderer';

const COMPLETE_DIFF = [
    'diff --git a/foo.ts b/foo.ts',
    '--- a/foo.ts',
    '+++ b/foo.ts',
    '@@ -1,3 +1,3 @@',
    ' context line',
    '-old line',
    '+new line',
    ' trailing context',
    '',
].join('\n');

describe('isDiffComplete', () => {
    describe('empty / blank input', () => {
        it('returns false for empty string regardless of streaming', () => {
            expect(isDiffComplete('', true)).toBe(false);
            expect(isDiffComplete('', false)).toBe(false);
        });

        it('returns false for whitespace-only string', () => {
            expect(isDiffComplete('   \n\t', false)).toBe(false);
        });
    });

    describe('not streaming — the short-circuit the fix depends on', () => {
        // This is the load-bearing branch: once the conversation is no
        // longer streaming, ANY non-empty diff is considered complete and
        // the button enables. The bug was never reaching this branch
        // because the global isStreaming boolean stayed stuck true.
        it('returns true for a complete diff when not streaming', () => {
            expect(isDiffComplete(COMPLETE_DIFF, false)).toBe(true);
        });

        it('returns true even for a structurally incomplete diff when not streaming', () => {
            // A diff that would FAIL the streaming heuristic (no headers,
            // ends abruptly) is still "complete" once streaming has ended —
            // there is nothing more coming.
            expect(isDiffComplete('@@ -1,1 +1,1 @@', false)).toBe(true);
            expect(isDiffComplete('+just an added line', false)).toBe(true);
        });
    });

    describe('streaming — structural completeness heuristic', () => {
        it('returns true for a fully-formed diff', () => {
            expect(isDiffComplete(COMPLETE_DIFF, true)).toBe(true);
        });

        it('returns true for a deletion diff (deleted file mode) as soon as the header appears', () => {
            const del = [
                'diff --git a/gone.ts b/gone.ts',
                'deleted file mode 100644',
                '--- a/gone.ts',
                '+++ /dev/null',
            ].join('\n');
            expect(isDiffComplete(del, true)).toBe(true);
        });

        it('returns true for a +++ /dev/null deletion target', () => {
            expect(isDiffComplete('+++ /dev/null', true)).toBe(true);
        });

        it('returns false when the git header has not arrived yet', () => {
            const partial = [
                '--- a/foo.ts',
                '+++ b/foo.ts',
                '@@ -1,1 +1,1 @@',
                '+new',
            ].join('\n');
            expect(isDiffComplete(partial, true)).toBe(false);
        });

        it('returns false when the diff ends abruptly on a hunk header', () => {
            const abrupt = [
                'diff --git a/foo.ts b/foo.ts',
                '--- a/foo.ts',
                '+++ b/foo.ts',
                '@@ -1,3 +1,3 @@',
            ].join('\n');
            expect(isDiffComplete(abrupt, true)).toBe(false);
        });

        it('returns false when the last line is a +/- change with no trailing blank (cut mid-hunk)', () => {
            const midHunk = [
                'diff --git a/foo.ts b/foo.ts',
                '--- a/foo.ts',
                '+++ b/foo.ts',
                '@@ -1,2 +1,2 @@',
                ' context',
                '+half a li', // streamed mid-token, no trailing blank line
            ].join('\n');
            expect(isDiffComplete(midHunk, true)).toBe(false);
        });

        it('accepts context-anchored / synthesized (ZIYA_NOPOS) hunk headers', () => {
            // Bare-header diffs must read as complete while streaming, else
            // their Apply button stays permanently greyed (the comment in
            // isDiffComplete documents this).
            const nopos = [
                'diff --git a/foo.ts b/foo.ts',
                '--- a/foo.ts',
                '+++ b/foo.ts',
                '@@ ... ZIYA_NOPOS def foo',
                ' context',
                '+added',
                ' trailing',
                '',
            ].join('\n');
            expect(isDiffComplete(nopos, true)).toBe(true);
        });
    });
});

/**
 * Pure model of the ApplyChangesButton disable predicate.
 *
 * Mirrors:
 *   const diffComplete = isDiffComplete(diff, isStreaming);
 *   const shouldDisableButton = isProcessing || (isStreaming && !diffComplete);
 *
 * where — post-fix — `isStreaming` is
 * `streamingConversations.has(currentConversationId)`, NOT the global
 * isStreaming boolean.
 */
function shouldDisableApplyButton(args: {
    diff: string;
    isProcessing: boolean;
    streamingConversations: Set<string>;
    currentConversationId: string;
}): boolean {
    const perConversationStreaming = args.streamingConversations.has(
        args.currentConversationId,
    );
    const diffComplete = isDiffComplete(args.diff, perConversationStreaming);
    return args.isProcessing || (perConversationStreaming && !diffComplete);
}

describe('Apply button disable predicate (per-conversation gating)', () => {
    const CONV = 'conv-123';

    it('REGRESSION: a settled conversation enables the button even with an empty streaming set', () => {
        // The exact reported state: streaming Set empty (conversation done),
        // a fully-formed diff. Must be enabled.
        expect(
            shouldDisableApplyButton({
                diff: COMPLETE_DIFF,
                isProcessing: false,
                streamingConversations: new Set<string>(),
                currentConversationId: CONV,
            }),
        ).toBe(false);
    });

    it('REGRESSION: another conversation streaming does NOT disable this conversation\'s button', () => {
        // The cross-conversation leak the global boolean caused: a
        // background/delegate conversation streaming kept every Apply
        // button greyed. Per-conversation gating must ignore other ids.
        expect(
            shouldDisableApplyButton({
                diff: COMPLETE_DIFF,
                isProcessing: false,
                streamingConversations: new Set<string>(['some-other-conv']),
                currentConversationId: CONV,
            }),
        ).toBe(false);
    });

    it('disables while the CURRENT conversation is actively streaming an incomplete diff', () => {
        const incomplete = [
            'diff --git a/foo.ts b/foo.ts',
            '--- a/foo.ts',
            '+++ b/foo.ts',
            '@@ -1,3 +1,3 @@',
        ].join('\n');
        expect(
            shouldDisableApplyButton({
                diff: incomplete,
                isProcessing: false,
                streamingConversations: new Set<string>([CONV]),
                currentConversationId: CONV,
            }),
        ).toBe(true);
    });

    it('enables once the current conversation\'s diff is structurally complete, even mid-stream', () => {
        expect(
            shouldDisableApplyButton({
                diff: COMPLETE_DIFF,
                isProcessing: false,
                streamingConversations: new Set<string>([CONV]),
                currentConversationId: CONV,
            }),
        ).toBe(false);
    });

    it('always disables while a diff application is in flight (isProcessing)', () => {
        expect(
            shouldDisableApplyButton({
                diff: COMPLETE_DIFF,
                isProcessing: true,
                streamingConversations: new Set<string>(),
                currentConversationId: CONV,
            }),
        ).toBe(true);
    });
});

/**
 * Derivation-invariant regression for the mode-2 desync root cause.
 *
 * The original bug: ChatContext held `isStreaming` / `isStreamingAny` as
 * independent useState booleans, written directly by external callers
 * (chatApi, StreamedContent, EditSection, StopStreamButton) via
 * `setIsStreaming`. A `setIsStreaming(true)` not matched by a later
 * `setIsStreaming(false)` — or a `setIsStreaming(false)` that fired without
 * the boolean ever being recomputed from the Set — left the boolean stuck
 * `true` while `streamingConversations` was empty. The diff Apply button
 * keyed off that stuck boolean and stayed greyed across turns.
 *
 * The fix derives both booleans from `streamingConversations.size > 0` and
 * makes `setIsStreaming` a no-op shim. This block pins that contract with a
 * pure model of the derivation: no sequence of `setIsStreaming` calls can
 * make the derived value disagree with the Set.
 */

// Pure model of ChatContext's streaming-state derivation post-fix.
function makeStreamingModel() {
    const streamingConversations = new Set<string>();
    return {
        add(id: string) { streamingConversations.add(id); },
        remove(id: string) { streamingConversations.delete(id); },
        // The no-op shim: external callers may invoke this, but it must
        // never affect the derived state.
        setIsStreaming(_v: boolean) { /* intentional no-op */ },
        get isStreaming() { return streamingConversations.size > 0; },
        get isStreamingAny() { return streamingConversations.size > 0; },
        get setSize() { return streamingConversations.size; },
    };
}

describe('streaming-state derivation invariant (mode-2 desync fix)', () => {
    it('isStreaming is true exactly when the Set is non-empty', () => {
        const m = makeStreamingModel();
        expect(m.isStreaming).toBe(false);
        m.add('conv-1');
        expect(m.isStreaming).toBe(true);
        m.remove('conv-1');
        expect(m.isStreaming).toBe(false);
    });

    it('isStreaming and isStreamingAny always agree (same source of truth)', () => {
        const m = makeStreamingModel();
        expect(m.isStreaming).toBe(m.isStreamingAny);
        m.add('a');
        expect(m.isStreaming).toBe(m.isStreamingAny);
        m.add('b');
        m.remove('a');
        expect(m.isStreaming).toBe(m.isStreamingAny);
        m.remove('b');
        expect(m.isStreaming).toBe(m.isStreamingAny);
    });

    it('REGRESSION: setIsStreaming(true) on an empty Set cannot make isStreaming true', () => {
        // The exact reported failure: a stray setter write that, pre-fix,
        // flipped the standalone boolean and stranded it. The no-op shim
        // means the derived value still reflects the (empty) Set.
        const m = makeStreamingModel();
        m.setIsStreaming(true);
        m.setIsStreaming(true);
        expect(m.isStreaming).toBe(false);
        expect(m.isStreamingAny).toBe(false);
    });

    it('REGRESSION: setIsStreaming(false) cannot strand isStreaming while a real stream is active', () => {
        // The inverse desync: a setter write must not be able to report
        // "not streaming" while a conversation is genuinely in the Set.
        const m = makeStreamingModel();
        m.add('conv-1');
        m.setIsStreaming(false);
        expect(m.isStreaming).toBe(true);
    });

    it('a remove that empties the Set deterministically clears isStreaming, no setter needed', () => {
        // Pre-fix this depended on removeStreamingConversation also calling
        // setIsStreaming(next.size > 0); a missed call left it stuck. Now the
        // derivation makes the Set mutation alone sufficient.
        const m = makeStreamingModel();
        m.add('only');
        expect(m.isStreaming).toBe(true);
        m.remove('only');
        expect(m.isStreaming).toBe(false);
        expect(m.setSize).toBe(0);
    });

    it('concurrent streams: isStreaming stays true until the LAST one is removed', () => {
        const m = makeStreamingModel();
        m.add('x');
        m.add('y');
        m.remove('x');
        expect(m.isStreaming).toBe(true); // y still streaming
        m.remove('y');
        expect(m.isStreaming).toBe(false);
    });
});
