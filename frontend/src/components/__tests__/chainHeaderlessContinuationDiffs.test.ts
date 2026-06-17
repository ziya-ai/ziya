/**
 * Tests for chainHeaderlessContinuationDiffs() — the helper that associates
 * headerless continuation ```diff blocks with the file of the most recent
 * "headed" diff block, so each parses standalone and gets its own Apply button.
 *
 * Reproduces the real-world failure: an assistant emits one diff with bare
 * "--- a/SKILL.md" / "+++ b/SKILL.md" headers (no "diff --git" line), followed
 * by several pure-add ```diff blocks that start directly with hunk-body "+"
 * lines (no "@@" marker). Before the fix, those follow-on blocks were never
 * associated with SKILL.md and rendered as inert raw text.
 *
 * Also pins the pure-add GATE: a continuation block that contains removals
 * ("-") or substantive context (" ") lines asserts what the target file
 * currently holds. That assertion can't be verified in the synchronous render
 * pass, so such blocks are intentionally NOT chained (left unchanged).
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
    it('anchors bare-body pure-add continuations to the preceding ---/+++ file', () => {
        const [head, cont] = chainHeaderlessContinuationDiffs([
            headedBareUnified,
            pureAddBareBody,
        ]);

        // The headed block is self-sufficient and must be returned unchanged.
        expect(head).toBe(headedBareUnified);

        // The continuation must gain synthesized headers pointing at SKILL.md.
        expect(cont).toContain('diff --git a/SKILL.md b/SKILL.md');
        expect(cont).toContain('--- a/SKILL.md');
        expect(cont).toContain('+++ b/SKILL.md');
        // A placeholder "@@" is prepended for bare bodies (real counts are
        // filled later by synthesizeMissingHunkHeaders()).
        expect(cont).toContain('\n@@\n');
        // Original body is preserved at the tail.
        expect(cont.endsWith(pureAddBareBody)).toBe(true);
    });

    it('anchors "@@"-led pure-add continuations without adding a placeholder @@', () => {
        const [, cont] = chainHeaderlessContinuationDiffs([
            headedBareUnified,
            pureAddWithAt,
        ]);
        expect(cont).toContain('diff --git a/SKILL.md b/SKILL.md');
        expect(cont).toContain('--- a/SKILL.md');
        expect(cont).toContain('+++ b/SKILL.md');
        // The block already had its own "@@" line; no extra placeholder added.
        expect(cont).toContain('@@ Value Types');
        expect(cont).not.toContain('\n@@\n@@ Value Types');
    });

    it('chains the full SKILL.md sequence: one headed block + four pure-add follow-ons', () => {
        const followOns = [
            '+- Follow-on rule one\n',
            '+### Step 0\n+\n+body\n',
            '@@ Dimensions\n+  WARNING: pin the merlin dimension.\n',
            '+- Always discover the EXACT metric name.\n',
        ];
        const result = chainHeaderlessContinuationDiffs([headedBareUnified, ...followOns]);

        // Head unchanged.
        expect(result[0]).toBe(headedBareUnified);
        // Every follow-on now resolves to SKILL.md.
        for (let i = 1; i < result.length; i++) {
            expect(result[i]).toContain('diff --git a/SKILL.md b/SKILL.md');
            expect(result[i]).toContain('+++ b/SKILL.md');
        }
    });

    it('seeds the path from a "diff --git" header too (prefers the b/ path)', () => {
        const headedGit =
            'diff --git a/foo/Bar.ts b/foo/Bar.ts\n' +
            '--- a/foo/Bar.ts\n' +
            '+++ b/foo/Bar.ts\n' +
            '@@ -1,1 +1,2 @@\n' +
            ' line\n' +
            '+added\n';
        const [, cont] = chainHeaderlessContinuationDiffs([
            headedGit,
            '+another added line\n',
        ]);
        expect(cont).toContain('diff --git a/foo/Bar.ts b/foo/Bar.ts');
    });

    describe('pure-add gate', () => {
        it('does NOT chain a continuation containing removal lines', () => {
            const withRemoval = '-old removed line\n+new line\n';
            const [, cont] = chainHeaderlessContinuationDiffs([
                headedBareUnified,
                withRemoval,
            ]);
            // Unverifiable file assumption → left unchanged, no synthesized headers.
            expect(cont).toBe(withRemoval);
            expect(cont).not.toContain('diff --git');
        });

        it('does NOT chain a continuation containing context lines', () => {
            const withContext =
                '@@ Some Section\n' +
                ' existing context line\n' +
                '+added line\n';
            const [, cont] = chainHeaderlessContinuationDiffs([
                headedBareUnified,
                withContext,
            ]);
            expect(cont).toBe(withContext);
            expect(cont).not.toContain('diff --git');
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
            headedBareUnified,      // anchors to SKILL.md
            '+add to skill\n',      // → SKILL.md
            headedSecond,           // re-anchors to Other.md
            '+add to other\n',      // → Other.md
        ]);
        expect(result[1]).toContain('+++ b/SKILL.md');
        expect(result[3]).toContain('+++ b/Other.md');
    });
});
