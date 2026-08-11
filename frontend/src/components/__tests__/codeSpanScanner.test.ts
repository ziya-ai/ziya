/**
 * CommonMark inline code-span scanning, and the math-preprocessing ordering
 * bug it exists to fix.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * MarkdownRenderer's math preprocessing runs BEFORE marked's lexer and splits
 * its input on FENCED code blocks only (``` / ~~~). Inline code spans were
 * never excluded, so a `$...$` written inside backticks was rewritten into a
 * math marker, and the code span then rendered the marker text verbatim
 * instead of the literal `$x$` the user typed:
 *
 *   "use `$\#_E$` here"  ->  "use `⟨MATH_INLINE_B64:XCNfRQ==⟩` here"
 *
 * The same flaw affected the DISPLAY pass, which turned `` `$$x$$` `` into a
 * <div class="math-display-encoded"> nested inside a code span.
 *
 * fenceScanner's header explicitly scoped inline code spans OUT of the
 * block-level fence model; findCodeSpans closes that gap, and is the inline
 * analogue of applyOutsideFences.
 *
 * The correctness bar here is agreement with marked's own inline tokenizer.
 * A hand-rolled /`[^`]*`/ regex looks adequate and is wrong on the cases at
 * the bottom of this file (multi-backtick spans, unmatched runs), so the
 * ground-truth comparison is the point of the first describe block, not
 * incidental thoroughness.
 */
// marked 16 ships ESM-only via its package "exports"; this jest setup does not
// transform it. The package also ships a UMD build, so requiring that directly
// keeps this test self-contained instead of forcing a global
// transformIgnorePatterns change that would alter compilation for every suite.
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { marked } = require('marked/lib/marked.umd.js');

import { findCodeSpans, applyOutsideCodeSpans } from '../fenceScanner';

/** Every code span marked finds, anywhere in the token tree. */
function markedCodeSpans(src: string): string[] {
    const out: string[] = [];
    const walk = (t: any): void => {
        if (Array.isArray(t)) { t.forEach(walk); return; }
        if (!t || typeof t !== 'object') return;
        if (t.type === 'codespan') out.push(t.raw);
        if (t.tokens) walk(t.tokens);
        if (t.items) walk(t.items);
        if (t.rows) t.rows.forEach((r: any[]) => r.forEach((c: any) => walk(c.tokens)));
        if (t.header) t.header.forEach((c: any) => walk(c.tokens));
    };
    walk(marked.lexer(src));
    return out;
}

const spansOf = (src: string): string[] =>
    findCodeSpans(src).map(([a, b]) => src.slice(a, b));

describe('findCodeSpans agrees with marked’s inline tokenizer', () => {
    // Each case is a shape where a naive /`[^`]*`/ scan and CommonMark differ,
    // or where the math passes would otherwise corrupt user content.
    it.each([
        'use `$x$` here',
        'a ``code with ` tick`` b',
        'unmatched ` tick $x$ here',
        'two `a` and `b` spans',
        '`$x$` at start',
        'nested ```triple``` run',
        'plain text no ticks',
        'trailing tick at end `',
        'a `b` c `d` e `f` g',
        '``outer `inner` outer``',
        'math $x$ and code `$y$`',
        'code `$y$` then math $x$',
        '`` ` `` degenerate',
        'mismatch ``a` b',
        'three `a` `b` `c`',
        'adjacent ``a``b`` runs',
        // Code spans may contain a newline (CommonMark), and the math passes
        // operate on multi-line segments, so this is not a hypothetical.
        'a `span\nacross lines` b',
        'a `no close\n\nnew para $x$',
        'list:\n- `$x$` item\n- $y$ item',
        'table:\n| `$x$` | $y$ |\n|---|---|\n| a | b |',
    ])('agrees on %j', (src) => {
        expect(spansOf(src)).toEqual(markedCodeSpans(src));
    });

    it('treats an unmatched backtick run as literal text, not an open span', () => {
        // The failure mode this prevents: swallowing the rest of the document
        // into a phantom span and suppressing all later math.
        expect(spansOf('unmatched ` tick $x$ here')).toEqual([]);
    });

    it('requires the closing run to match the opening run length exactly', () => {
        expect(spansOf('a ``code with ` tick`` b')).toEqual(['``code with ` tick``']);
    });
});

describe('applyOutsideCodeSpans', () => {
    it('transforms only the regions outside code spans', () => {
        const out = applyOutsideCodeSpans('a $x$ b `$y$` c', s => s.replace(/\$/g, '@'));
        expect(out).toBe('a @x@ b `$y$` c');
    });

    it('is a no-op when the transform is identity', () => {
        for (const src of ['a `b` c', 'no ticks', '``x`` y', 'unmatched ` here']) {
            expect(applyOutsideCodeSpans(src, s => s)).toBe(src);
        }
    });

    it('still transforms text when there are no code spans at all', () => {
        expect(applyOutsideCodeSpans('a $x$ b', s => s.replace(/\$/g, '@')))
            .toBe('a @x@ b');
    });

    it('handles a code span at the very start and very end', () => {
        const f = (s: string) => s.replace(/\$/g, '@');
        expect(applyOutsideCodeSpans('`$a$` mid $b$', f)).toBe('`$a$` mid @b@');
        expect(applyOutsideCodeSpans('$b$ mid `$a$`', f)).toBe('@b@ mid `$a$`');
    });
});
