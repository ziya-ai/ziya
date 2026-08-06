/**
 * Regression tests for the title shown in a diff block's header bar.
 *
 * Symptom: a new-file diff with no ``diff --git`` line — the shape a model
 * emits most often — rendered its header as the literal string ``ev/null``,
 * losing the filename entirely.
 *
 * Root cause: the unified-header branch sliced the path with
 * ``line.substring(6)``, an offset calibrated for ``"--- a/"``.
 * ``"--- /dev/null"`` has only FIVE characters before its path, so the slice
 * ate two of them.  The resulting ``"ev/null"`` then failed the
 * ``filePath === '/dev/null'`` guard that existed to skip the null side and
 * read the ``+++`` line instead, so the mangled fragment was returned as the
 * title.  Because this diff shape carries no ``diff --git`` line, there was
 * no other branch to fall back on.
 *
 * The 'ev/null' assertions below are deliberately literal: they are the
 * observable symptom, and a future refactor that reintroduces a fixed-offset
 * slice would reproduce exactly that string.
 */

// ``marked`` is ESM-only and the CRA jest transform won't process it.  Stub at
// module scope so importing the MarkdownRenderer module (we only need the pure
// ``extractDiffFileTitle`` helper) doesn't fail on its top-level import.
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
// ``uuid`` is also ESM-only, pulled in transitively via the
// FolderContext → ProjectContext → db.ts chain MarkdownRenderer imports.
jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));

import { extractDiffFileTitle } from '../MarkdownRenderer';

const NEW_PATH = 'frontend/src/utils/__tests__/latexSvgTheme.test.ts';

/** The reported failure: new file, no git header. */
const HEADERLESS_NEW_FILE = [
    '--- /dev/null',
    `+++ b/${NEW_PATH}`,
    '@@ -0,0 +1,3 @@',
    '+/**',
    '+ * @jest-environment jsdom',
    '+ */',
].join('\n');

describe('extractDiffFileTitle — new file, no git header (the reported bug)', () => {
    it('names the created file rather than a fragment of /dev/null', () => {
        expect(extractDiffFileTitle(HEADERLESS_NEW_FILE))
            .toBe(`Create New File: ${NEW_PATH}`);
    });

    it('never emits the mangled "ev/null" fragment', () => {
        // The literal symptom.  Asserted separately from the positive case so
        // a regression that returns some OTHER wrong value is still
        // distinguishable from this specific off-by-two.
        expect(extractDiffFileTitle(HEADERLESS_NEW_FILE)).not.toContain('ev/null');
    });

    it('tolerates a bare /dev/null with no leading slash', () => {
        // Some producers normalize the marker to "dev/null"; it still names
        // no file, so the +++ line must supply the path.
        const diff = ['--- dev/null', `+++ b/${NEW_PATH}`, '@@ -0,0 +1,1 @@', '+x'].join('\n');
        expect(extractDiffFileTitle(diff)).toBe(`Create New File: ${NEW_PATH}`);
    });

    it('does not invent a filename from a partially-streamed header', () => {
        // Only the /dev/null side has arrived.  "Unknown file" is honest;
        // "ev/null" was not.
        expect(extractDiffFileTitle('--- /dev/null')).toBe('Unknown file');
        expect(extractDiffFileTitle('--- /dev/null\n')).not.toContain('ev/null');
    });

    it('handles a +++ target with no b/ prefix', () => {
        // Old code required a literal "+++ b/", so this shape fell through to
        // the /dev/null line and produced the mangled title.
        const diff = ['--- /dev/null', `+++ ${NEW_PATH}`, '@@ -0,0 +1,1 @@', '+x'].join('\n');
        expect(extractDiffFileTitle(diff)).toBe(`Create New File: ${NEW_PATH}`);
    });
});

describe('extractDiffFileTitle — git-header shapes (unchanged behavior)', () => {
    it('reports a new file declared by new file mode', () => {
        const diff = [
            `diff --git a/${NEW_PATH} b/${NEW_PATH}`,
            'new file mode 100644',
            '--- /dev/null',
            `+++ b/${NEW_PATH}`,
            '@@ -0,0 +1,1 @@',
            '+x',
        ].join('\n');
        expect(extractDiffFileTitle(diff)).toBe(`Create New File: ${NEW_PATH}`);
    });

    it('reports a deletion declared by deleted file mode', () => {
        const diff = [
            'diff --git a/gone.ts b/gone.ts',
            'deleted file mode 100644',
            '--- a/gone.ts',
            '+++ /dev/null',
            '@@ -1 +0,0 @@',
            '-x',
        ].join('\n');
        expect(extractDiffFileTitle(diff)).toBe('Delete: gone.ts');
    });

    it('reports a rename', () => {
        const diff = 'diff --git a/old.ts b/new.ts\n--- a/old.ts\n+++ b/new.ts';
        expect(extractDiffFileTitle(diff)).toBe('Rename: old.ts → new.ts');
    });

    it('reports a plain modification', () => {
        const diff = [
            'diff --git a/foo.ts b/foo.ts',
            '--- a/foo.ts',
            '+++ b/foo.ts',
            '@@ -1 +1 @@',
            '-a',
            '+b',
        ].join('\n');
        expect(extractDiffFileTitle(diff)).toBe('Modify: foo.ts');
    });
});

describe('extractDiffFileTitle — headerless modification and deletion', () => {
    it('returns the bare path for a modification', () => {
        const diff = '--- a/foo.ts\n+++ b/foo.ts\n@@ -1 +1 @@\n-a\n+b';
        expect(extractDiffFileTitle(diff)).toBe('foo.ts');
    });

    it('labels a deletion from the +++ /dev/null marker alone', () => {
        const diff = '--- a/gone.ts\n+++ /dev/null\n@@ -1 +0,0 @@\n-x';
        expect(extractDiffFileTitle(diff)).toBe('Delete File: gone.ts');
    });
});

describe('extractDiffFileTitle — header/body boundary', () => {
    it('prefers the real header over a body line that looks like one', () => {
        // The added line's content is "++ b/fake.md", which arrives on the
        // wire as "+++ b/fake.md" and is indistinguishable from a header in
        // isolation.  The real +++ header precedes it, so it wins.
        const diff = [
            '--- /dev/null',
            '+++ b/real.md',
            '@@ -0,0 +1,2 @@',
            '+++ b/fake.md',
            '+--- a/fake.md',
        ].join('\n');
        expect(extractDiffFileTitle(diff)).toBe('Create New File: real.md');
    });

    it('does not read a post-hunk header line as this block\'s header', () => {
        // A diff-of-a-diff: the body contains genuine-looking header lines.
        // Scanning stops at the first @@, so none of them can be picked up.
        const diff = [
            '@@ -1,3 +1,3 @@',
            ' --- a/embedded.ts',
            '-+++ b/embedded.ts',
            '+++ b/embedded.ts',
        ].join('\n');
        expect(extractDiffFileTitle(diff)).toBe('Unknown file');
    });
});

describe('extractDiffFileTitle — degenerate input', () => {
    it('returns an empty string for empty input', () => {
        expect(extractDiffFileTitle('')).toBe('');
    });

    it('returns Unknown file when no header is present at all', () => {
        expect(extractDiffFileTitle('just some prose\nand more prose')).toBe('Unknown file');
    });
});
