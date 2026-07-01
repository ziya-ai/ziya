/**
 * Tests for chainHeaderlessContinuationDiffs() — the helper that associates
 * headerless continuation ```diff blocks with the file of the most recent
 * "headed" diff block.
 *
 * CONTRACT (as of commit 655eb72 "chain continuation diffs with valid hunk
 * headers unconditionally"): a chainable continuation is MERGED into the
 * preceding headed block, producing a single multi-hunk diff per file rather
 * than N standalone diffs. The merged continuation's own array slot is set to
 * '' so the render call site absorbs (skips) it. (The earlier 434a324 contract
 * synthesized standalone "diff --git" headers per continuation; that was
 * superseded by the merge approach — this suite pins the merge contract.)
 *
 * Reproduces the real-world failure: an assistant emits one diff with bare
 * "--- a/SKILL.md" / "+++ b/SKILL.md" headers (no "diff --git" line), followed
 * by several pure-add ```diff blocks that start directly with hunk-body "+"
 * lines (no "@@" marker). Before the fix, those follow-on blocks were never
 * associated with SKILL.md and rendered as inert raw text.
 *
 * Also pins:
 *  - the pure-add GATE: a continuation containing removals ("-") or substantive
 *    context (" ") lines asserts what the target file currently holds, which
 *    can't be verified in the synchronous render pass, so it is NOT chained.
 *  - the "@@"-hint placeholder fix: a continuation already starting with an
 *    "@@" line (numeric OR section-hint) is appended directly — NO bare "@@"
 *    placeholder is prepended (which would inject a spurious empty hunk).
 */

// ``marked`` is ESM-only and the CRA jest transform won't process it.
// Stub at module scope so importing the MarkdownRenderer module doesn't fail
// when its top-level ``marked`` import resolves. (Same shim other tests use.)
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

import { chainHeaderlessContinuationDiffs } from '../MarkdownRenderer';

// A headed diff carrying only bare ---/+++ unified headers (no "diff --git").
const headedBareUnified =
    '--- a/SKILL.md\n' +
    '+++ b/SKILL.md\n' +
    '@@ Key Rules\n' +
    ' - Sleep 1s between queries for rate limiting\n' +
    '+- New rule line\n';

// A pure-add continuation that starts directly with a hunk body (no "@@").
const pureAddBareBody =
    '+### Step 0: Discover the entity ID\n' +
    '+\n' +
    '+Some added documentation paragraph.\n';

// A pure-add continuation that starts with an "@@" hint line.
const pureAddWithAt =
    '@@ Value Types\n' +
    '+IMPORTANT: an incorrect cast returns EMPTY STRING silently.\n';

describe('chainHeaderlessContinuationDiffs', () => {
    it('merges a bare-body pure-add continuation into the preceding ---/+++ block', () => {
        const [head, cont] = chainHeaderlessContinuationDiffs([
            headedBareUnified,
            pureAddBareBody,
        ]);

        // The continuation is absorbed: its own slot becomes ''.
        expect(cont).toBe('');

        // The headed block now carries the original content...
        expect(head).toContain('--- a/SKILL.md');
        expect(head).toContain('+++ b/SKILL.md');
        expect(head).toContain('@@ Key Rules');
        expect(head).toContain('+- New rule line');
        // ...plus the merged continuation body, separated by a placeholder "@@"
        // (the bare body had no @@; synthesizeMissingHunkHeaders fills it later).
        expect(head).toContain('\n@@\n');
        expect(head.endsWith(pureAddBareBody)).toBe(true);
    });

    it('merges an "@@"-hint continuation WITHOUT prepending a bare "@@" placeholder', () => {
        const [head, cont] = chainHeaderlessContinuationDiffs([
            headedBareUnified,
            pureAddWithAt,
        ]);

        expect(cont).toBe('');
        expect(head).toContain('@@ Value Types');
        expect(head).toContain('+IMPORTANT: an incorrect cast returns EMPTY STRING silently.');
        // The block already had its own "@@" line; no spurious empty-hunk
        // placeholder is injected before it.
        expect(head).not.toContain('\n@@\n@@ Value Types');
    });

    it('chains the full SKILL.md sequence: one headed block + four pure-add follow-ons', () => {
        const followOns = [
            '+- Follow-on rule one\n',
            '+### Step 0\n+\n+body\n',
            '@@ Dimensions\n+  WARNING: pin the merlin dimension.\n',
            '+- Always discover the EXACT metric name.\n',
        ];
        const result = chainHeaderlessContinuationDiffs([headedBareUnified, ...followOns]);

        // Every follow-on is absorbed into the headed block (its slot is '').
        for (let i = 1; i < result.length; i++) {
            expect(result[i]).toBe('');
        }
        // The single merged head block contains the headers once and every
        // follow-on body.
        const head = result[0];
        expect(head).toContain('+++ b/SKILL.md');
        expect(head).toContain('+- Follow-on rule one');
        expect(head).toContain('+### Step 0');
        expect(head).toContain('@@ Dimensions');
        expect(head).toContain('+  WARNING: pin the merlin dimension.');
        expect(head).toContain('+- Always discover the EXACT metric name.');
    });

    it('merges into a "diff --git"-headed block (path seeded from the header)', () => {
        const headedGit =
            'diff --git a/foo/Bar.ts b/foo/Bar.ts\n' +
            '--- a/foo/Bar.ts\n' +
            '+++ b/foo/Bar.ts\n' +
            '@@ -1,1 +1,2 @@\n' +
            ' line\n' +
            '+added\n';
        const [head, cont] = chainHeaderlessContinuationDiffs([
            headedGit,
            '+another added line\n',
        ]);
        expect(cont).toBe('');
        expect(head).toContain('diff --git a/foo/Bar.ts b/foo/Bar.ts');
        expect(head).toContain('+another added line');
    });

    it('merges a continuation carrying a VALID numeric @@ header directly (no placeholder)', () => {
        // A continuation with a real numeric range is chained unconditionally
        // (apply-time validation covers correctness), appended without a
        // placeholder "@@".
        const headedGit =
            'diff --git a/foo/Bar.ts b/foo/Bar.ts\n' +
            '--- a/foo/Bar.ts\n' +
            '+++ b/foo/Bar.ts\n' +
            '@@ -1,1 +1,2 @@\n' +
            ' line\n' +
            '+added\n';
        const numericCont = '@@ -10,2 +11,3 @@\n ctx\n-old\n+new\n';
        const [head, cont] = chainHeaderlessContinuationDiffs([headedGit, numericCont]);
        expect(cont).toBe('');
        expect(head).toContain('@@ -10,2 +11,3 @@');
        // Appended directly — no bare "@@" placeholder before the numeric header.
        expect(head).not.toContain('\n@@\n@@ -10,2 +11,3 @@');
    });

    describe('pure-add gate', () => {
        it('does NOT chain a continuation containing removal lines', () => {
            const withRemoval = '-old removed line\n+new line\n';
            const [head, cont] = chainHeaderlessContinuationDiffs([
                headedBareUnified,
                withRemoval,
            ]);
            // Unverifiable file assumption → left unchanged, not merged.
            expect(cont).toBe(withRemoval);
            expect(head).toBe(headedBareUnified);
        });

        it('does NOT chain a continuation containing context lines', () => {
            const withContext =
                '@@ Some Section\n' +
                ' existing context line\n' +
                '+added line\n';
            const [head, cont] = chainHeaderlessContinuationDiffs([
                headedBareUnified,
                withContext,
            ]);
            expect(cont).toBe(withContext);
            expect(head).toBe(headedBareUnified);
        });
    });

    it('leaves a leading headerless block unchanged when there is no prior headed diff', () => {
        const orphan = '+orphan added line with no preceding headed diff\n';
        const [cont] = chainHeaderlessContinuationDiffs([orphan]);
        expect(cont).toBe(orphan);
    });

    it('does not mutate the input array entries (returns new strings)', () => {
        const input = [headedBareUnified, pureAddBareBody];
        const snapshot = [...input];
        chainHeaderlessContinuationDiffs(input);
        expect(input).toEqual(snapshot);
    });

    it('handles empty input', () => {
        expect(chainHeaderlessContinuationDiffs([])).toEqual([]);
    });

    it('preserves empty-string entries untouched', () => {
        const [a, b] = chainHeaderlessContinuationDiffs(['', headedBareUnified]);
        expect(a).toBe('');
        expect(b).toBe(headedBareUnified);
    });

    it('re-anchors to a new file when a second headed diff appears', () => {
        const headedSecond =
            'diff --git a/Other.md b/Other.md\n' +
            '--- a/Other.md\n' +
            '+++ b/Other.md\n' +
            '@@ -1 +1,2 @@\n' +
            ' x\n' +
            '+y\n';
        const result = chainHeaderlessContinuationDiffs([
            headedBareUnified,      // anchors to SKILL.md   (idx 0)
            '+add to skill\n',      // → merged into idx 0
            headedSecond,           // re-anchors to Other.md (idx 2)
            '+add to other\n',      // → merged into idx 2
        ]);
        // Continuations are absorbed into their respective headed blocks.
        expect(result[1]).toBe('');
        expect(result[3]).toBe('');
        // First headed block owns SKILL.md + its continuation.
        expect(result[0]).toContain('+++ b/SKILL.md');
        expect(result[0]).toContain('+add to skill');
        // Second headed block owns Other.md + its continuation.
        expect(result[2]).toContain('+++ b/Other.md');
        expect(result[2]).toContain('+add to other');
    });
});
