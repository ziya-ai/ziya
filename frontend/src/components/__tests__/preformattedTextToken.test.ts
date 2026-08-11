/**
 * Guard for the multi-line `text` token → <pre> decision in MarkdownRenderer.
 *
 * REGRESSION UNDER TEST
 * ---------------------
 * When a numbered group is not preceded by a blank line and does not start at
 * "1.", CommonMark will not let the list interrupt the paragraph, so the whole
 * group lexes as ONE paragraph with soft line breaks. Interleaved codespans
 * then split that paragraph into inline `text` tokens which each BEGIN with
 * "\n" and have no child tokens — e.g. "\n4. #2 — collapse packaging to ".
 *
 * The old predicate (`includes('\n') && trim().includes('\n')`) matched those
 * fragments and wrapped mid-sentence prose in a monospace <pre>, which also
 * nests a block element inside <p> and tears the surrounding layout.
 *
 * isPreformattedTextToken must accept genuinely indented block content and
 * reject soft-wrapped prose.
 */
import { isPreformattedTextToken } from '../fenceScanner';

describe('isPreformattedTextToken: rejects soft-wrapped prose', () => {
    // Exact fragments produced by marked for the reported input.
    const proseFragments = [
        '\n4. #2 — collapse packaging to ',
        ' only.\n5. #6 — ',
        '.\n6. #5 — introduce ',
        '\n7. #1 — slice god files (do ',
        ' first — highest churn).\n8. #3 — Vite migration (biggest dev-ex win).\n9. #4 — ',
        ' 3-layer consolidation (riskiest, needs the unit test re-tree #8 first).\n10. #10 — dependency slimming + ',
    ];

    test.each(proseFragments)('does not preformat %j', (fragment) => {
        expect(isPreformattedTextToken(fragment)).toBe(false);
    });

    test('plain soft-wrapped paragraph is not preformatted', () => {
        expect(isPreformattedTextToken('line one\nline two\nline three')).toBe(false);
    });

    test('leading newline before unindented prose is not preformatted', () => {
        expect(isPreformattedTextToken('\nline two\nline three')).toBe(false);
    });

    test('mixed indentation is not preformatted (one bare line disqualifies)', () => {
        expect(isPreformattedTextToken('  indented\nnot indented')).toBe(false);
    });
});

describe('isPreformattedTextToken: accepts indented block content', () => {
    test('indented continuation lines under a list item', () => {
        expect(
            isPreformattedTextToken('outer\n  raw block line a\n  raw block line b'),
        ).toBe(true);
    });

    test('two-line indented command block', () => {
        expect(isPreformattedTextToken('cmd --one\n  cmd --two')).toBe(true);
    });

    test('tab-indented continuation counts as indented', () => {
        expect(isPreformattedTextToken('header\n\tbody line')).toBe(true);
    });

    test('blank lines between indented lines do not disqualify', () => {
        expect(isPreformattedTextToken('a\n  b\n\n  c')).toBe(true);
    });
});

describe('isPreformattedTextToken: degenerate inputs', () => {
    test('single line is never preformatted', () => {
        expect(isPreformattedTextToken('single line')).toBe(false);
    });

    test('empty string is never preformatted', () => {
        expect(isPreformattedTextToken('')).toBe(false);
    });

    test('lone newline separator is not preformatted', () => {
        // The renderer converts a lone "\n" token to <br/> earlier; this must
        // not be reclassified as a code block if that ordering ever changes.
        expect(isPreformattedTextToken('\n')).toBe(false);
    });

    test('trailing newline alone is not preformatted', () => {
        expect(isPreformattedTextToken('a\n')).toBe(false);
    });

    test('whitespace-only multi-line content is not preformatted', () => {
        expect(isPreformattedTextToken('   \n   ')).toBe(false);
    });
});

describe('renderer wires the predicate into the text case', () => {
    // Structural check: the old newline-only test must not come back, and the
    // <pre> branch must be gated on the shared predicate. The renderer cannot
    // be imported in isolation (it pulls KaTeX, Prism, mermaid, the whole
    // component tree), so the invariant is pinned against the source.
    // eslint-disable-next-line @typescript-eslint/no-var-requires, global-require
    const fs = require('fs');
    // eslint-disable-next-line @typescript-eslint/no-var-requires, global-require
    const path = require('path');
    const source: string = fs.readFileSync(
        path.resolve(__dirname, '../MarkdownRenderer.tsx'),
        'utf8',
    );

    test('the <pre> branch is gated on isPreformattedTextToken', () => {
        expect(source).toContain('if (isPreformattedTextToken(decodedText))');
    });

    test('the predicate is imported from fenceScanner', () => {
        expect(source).toMatch(
            /import\s*\{[^}]*isPreformattedTextToken[^}]*\}\s*from\s*'\.\/fenceScanner'/,
        );
    });

    test('the newline-only heuristic is gone', () => {
        expect(source).not.toContain(
            "decodedText.includes('\\n') && decodedText.trim().includes('\\n')",
        );
    });
});
