/**
 * Marker-encoding contract for inline math.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * `processInlineMath` runs BEFORE marked's lexer, and the marker it emits is
 * ordinary markdown text. Anything between the marker delimiters therefore
 * gets lexed as markdown before a renderer sees it, which silently destroys
 * markdown-active characters in the LaTeX payload:
 *
 *   $\#_{\mathrm{E}}$  ->  marked treats `\#` as a CommonMark backslash-escape,
 *                          eats the backslash, and KaTeX then fails with
 *                          "Expected 'EOF', got '#'".
 *   $a*b*c$            ->  `*b*` is emphasis; asterisks vanish -> `abc`.
 *
 * The second class cannot be fixed by escaping (there is no backslash to
 * double), which is why the payload is base64-encoded: its alphabet
 * (A-Za-z0-9+/=) has no markdown-active character.
 *
 * These tests drive the payload through the REAL marked lexer, because the bug
 * lives in the interaction between our marker and marked's inline tokenizer.
 * A test that only inspected `processInlineMath`'s output string would have
 * passed throughout the entire lifetime of the bug.
 */
// marked 16 ships ESM-only from its package "exports" (lib/marked.esm.js),
// which this jest setup does not transform. The package also ships a UMD
// build; requiring it directly keeps this test self-contained rather than
// adding a global transformIgnorePatterns entry that would change how every
// other suite is compiled.
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { marked } = require('marked/lib/marked.umd.js');

import {
    processInlineMath,
    encodeInlineMathMarker,
    decodeInlineMathMarker,
    isInlineMathMarker,
    MATH_INLINE_MARKER_PREFIX,
    MATH_INLINE_MARKER_RE,
    MATH_INLINE_MARKER_SPLIT_RE,
} from '../inlineMathClassifier';

/**
 * Walk the token tree marked produces and collect the LaTeX of every marker,
 * mirroring how the renderer partitions text runs.
 *
 * Only LEAF text is inspected. marked stores a parent's raw text on the parent
 * AND re-emits it through child tokens, so scanning both levels would report
 * each marker two or three times. Descending whenever children exist, and
 * reading `text` only when they do not, counts each marker exactly once.
 */
function extractMathThroughLexer(markdown: string): string[] {
    const found: string[] = [];
    const visit = (node: any): void => {
        if (!node) return;
        if (Array.isArray(node)) { node.forEach(visit); return; }

        // Container tokens: recurse into children instead of reading own text.
        let descended = false;
        if (node.items) { visit(node.items); descended = true; }
        if (node.header) {
            node.header.forEach((c: any) => visit(c.tokens ?? { text: c.text }));
            descended = true;
        }
        if (node.rows) {
            node.rows.forEach((r: any[]) =>
                r.forEach(c => visit(c.tokens ?? { text: c.text })));
            descended = true;
        }
        if (node.tokens && node.tokens.length) { visit(node.tokens); descended = true; }
        if (descended) return;

        if (typeof node.text === 'string') {
            for (const part of node.text.split(MATH_INLINE_MARKER_SPLIT_RE)) {
                if (part && isInlineMathMarker(part)) {
                    const latex = decodeInlineMathMarker(part);
                    if (latex !== null) found.push(latex);
                }
            }
        }
    };
    visit(marked.lexer(processInlineMath(markdown)));
    return found;
}

describe('inline-math marker: codec round-trip', () => {
    it('round-trips payloads that markdown would otherwise mangle', () => {
        for (const latex of [
            '\\#_{\\mathrm{E}}',   // backslash-escape: the reported bug
            '\\alpha\\%',
            '\\{x\\}',
            '\\|v\\|',
            '\\&\\#',
            '\\\\ next',           // LaTeX newline
            'a*b*c',               // emphasis: unfixable by escaping
            'a_b_c',
            '~',
            'a<b>c',
        ]) {
            expect(decodeInlineMathMarker(encodeInlineMathMarker(latex))).toBe(latex);
        }
    });

    it('round-trips non-Latin-1 codepoints (bare btoa would throw)', () => {
        // Greek letters and operators are the MOST likely math content, and
        // btoa() alone raises InvalidCharacterError above U+00FF.
        for (const latex of ['α+β', '∑ x_i', '\\text{café}', 'θ ≤ π']) {
            expect(decodeInlineMathMarker(encodeInlineMathMarker(latex))).toBe(latex);
        }
    });

    it('emits only markdown-inert characters', () => {
        // If the encoded marker ever contained a markdown-active character the
        // whole defence collapses. Assert the alphabet directly.
        for (const latex of ['\\#_{\\mathrm{E}}', 'a*b*c', 'α+β', '\\\\']) {
            const payload = encodeInlineMathMarker(latex)
                .slice(MATH_INLINE_MARKER_PREFIX.length, -1);
            expect(payload).toMatch(/^[A-Za-z0-9+/]*={0,2}$/);
        }
    });

    it('rejects malformed markers instead of decoding garbage', () => {
        expect(decodeInlineMathMarker('not a marker')).toBeNull();
        expect(decodeInlineMathMarker('⟨MATH_INLINE_B64:!!!⟩')).toBeNull();
        expect(isInlineMathMarker('⟨MATH_INLINE_B64:')).toBe(false);   // truncated
        expect(isInlineMathMarker('⟨MATH_INLINE:x⟩')).toBe(false);     // legacy shape
    });
});

describe('inline-math marker: survives the real marked lexer', () => {
    it('preserves \\# — the reported rendering failure', () => {
        expect(extractMathThroughLexer('the $\\#_{\\mathrm{E}}$ value'))
            .toEqual(['\\#_{\\mathrm{E}}']);
    });

    it('preserves every backslash-escape class marked would consume', () => {
        expect(extractMathThroughLexer('$\\alpha\\%$')).toEqual(['\\alpha\\%']);
        expect(extractMathThroughLexer('$\\{x\\}$')).toEqual(['\\{x\\}']);
        expect(extractMathThroughLexer('$\\|v\\|$')).toEqual(['\\|v\\|']);
    });

    it('preserves emphasis-active characters that escaping could not save', () => {
        // Shielding backslashes cannot help here: there is no backslash.
        expect(extractMathThroughLexer('$a*b*c$')).toEqual(['a*b*c']);
        expect(extractMathThroughLexer('$x~y = z$')).toEqual(['x~y = z']);
    });

    it('preserves math that already worked (no regression)', () => {
        expect(extractMathThroughLexer('the value $x = 0$ holds')).toEqual(['x = 0']);
        expect(extractMathThroughLexer('$\\frac{1}{2}$ cup')).toEqual(['\\frac{1}{2}']);
        expect(extractMathThroughLexer('let $x$ vary')).toEqual(['x']);
        expect(extractMathThroughLexer('$\\sum_{i=1}^{n} x_i$')).toEqual(['\\sum_{i=1}^{n} x_i']);
        expect(extractMathThroughLexer('$a_{i} b_{j}$')).toEqual(['a_{i} b_{j}']);
        expect(extractMathThroughLexer('$P(A|B)$')).toEqual(['P(A|B)']);
        expect(extractMathThroughLexer('$E=mc^2$')).toEqual(['E=mc^2']);
        expect(extractMathThroughLexer('$α+β$')).toEqual(['α+β']);
    });

    it('handles several markers in one line independently', () => {
        expect(extractMathThroughLexer('$\\#_a$ and $\\#_b$ and $x$'))
            .toEqual(['\\#_a', '\\#_b', 'x']);
    });

    it('survives every block context, not just paragraphs', () => {
        const M = '\\#_{\\mathrm{E}}';
        expect(extractMathThroughLexer(`- item $${M}$ end`)).toEqual([M]);
        expect(extractMathThroughLexer(`## head $${M}$`)).toEqual([M]);
        expect(extractMathThroughLexer(`| a |\n|---|\n| $${M}$ |`)).toEqual([M]);
        expect(extractMathThroughLexer(`**bold $${M}$** tail`)).toEqual([M]);
        expect(extractMathThroughLexer(`> quote $${M}$ end`)).toEqual([M]);
        expect(extractMathThroughLexer('- a\n  - b $\\alpha\\%$ c')).toEqual(['\\alpha\\%']);
    });
});

describe('inline-math marker: guards in other layers keep working', () => {
    it('keeps the bare "MATH_INLINE" substring chatApi.ts depends on', () => {
        // chatApi.ts suppresses throttling-error detection when a chunk looks
        // like math, testing for 'MATH_INLINE' WITHOUT a colon. Renaming the
        // marker must not break that, or math chunks get misread as errors.
        expect(encodeInlineMathMarker('x')).toContain('MATH_INLINE');
    });

    it('no longer matches the legacy "MATH_INLINE:" guards', () => {
        // Legacy html-token guards match 'MATH_INLINE:' with a greedy [^<]*
        // capture that swallows the closing bracket. The _B64 rename is what
        // stops them intercepting the marker before proper extraction.
        expect(encodeInlineMathMarker('x')).not.toContain('MATH_INLINE:');
    });

    it('cannot collide with diff or offset-diff line parsing', () => {
        // cleanDiffContent() inspects line prefixes; a base64 payload may
        // contain '+' and '/', so confirm a marker line is inert there.
        const line = encodeInlineMathMarker('\\#_E');
        expect(/^(\s*)([+-]?)?\[(\d+)([+*,\s]*)\]\s(.*)$/.test(line)).toBe(false);
        for (const h of ['diff --git', 'index ', '--- ', '+++ ', '@@ ']) {
            expect(line.startsWith(h)).toBe(false);
        }
    });

    it('marker regex is anchored to the base64 alphabet', () => {
        // Guards against a future loosening to [\s\S]*?, which would let
        // arbitrary prose containing the prefix be treated as a payload.
        expect(MATH_INLINE_MARKER_RE.source).toContain('A-Za-z0-9');
    });
});

describe('inline-math marker: classifier decisions unchanged', () => {
    const LEASE = [
        'Deposit = $900 refundable security deposit + $300 non-refundable cleaning fee (= $1,200 total).',
        "The current draft ($200 after the 5th, +$100 after the 8th, +$75/day after) is far over Seattle's limit.",
    ].join('\n');

    it('still leaves currency-laden prose byte-identical', () => {
        // Re-asserted here because the marker change rewrites the emit path;
        // the currency defence must not be collateral damage.
        expect(processInlineMath(LEASE)).toBe(LEASE);
        expect(processInlineMath(LEASE)).not.toContain('MATH_INLINE');
    });

    it('still rejects spans the classifier never considered math', () => {
        expect(processInlineMath('pay $5 + $5 today')).not.toContain('MATH_INLINE');
        expect(processInlineMath('$ x = 0 $')).not.toContain('MATH_INLINE');
    });

    it('leaves adjacent currency literal while encoding real math', () => {
        const out = processInlineMath('cost $5 but $x$ is unknown');
        expect(out).toContain('cost $5 but');
        expect(out).toContain(MATH_INLINE_MARKER_PREFIX);
    });
});

/**
 * The split contract, asserted on VISIBLE TEXT.
 *
 * MarkdownRenderer's `renderWithInlineMath` partitions a text run with
 * `text.split(MATH_INLINE_MARKER_SPLIT_RE)`, renders any segment that IS a
 * marker as KaTeX, and emits every other segment as literal text. That makes
 * the number of capture groups in the split pattern load-bearing:
 * `String.prototype.split` appends EVERY capture to the result, so a nested
 * group meant the base64 payload was emitted a second time, on its own, and
 * fell through to the literal branch. Rendered output read
 *
 *   λ → μ⁻XGxhbWJkYSBcdG8gXG11XnstfQ==
 *
 * i.e. correct math immediately followed by its own encoding.
 *
 * The suites above could not see this: `extractMathThroughLexer` keeps only
 * segments for which `isInlineMathMarker` is true and silently drops the rest,
 * so the duplicate payload segment was never examined. These tests instead
 * reconstruct what the user actually reads.
 */
describe('inline-math marker: split emits no payload residue', () => {
    /**
     * Mirror `renderWithInlineMath`: markers become a sentinel, everything
     * else is literal text. The joined result is what reaches the screen.
     */
    const renderToVisibleText = (text: string, sentinel = '⟦MATH⟧'): string =>
        text.split(MATH_INLINE_MARKER_SPLIT_RE)
            .map(part => (part && isInlineMathMarker(part) ? sentinel : (part || '')))
            .join('');

    it('splits into strictly alternating literal / marker segments', () => {
        const parts = processInlineMath('a $x$ b')
            .split(MATH_INLINE_MARKER_SPLIT_RE)
            .filter(p => p !== '' && p !== undefined);
        // 'a ', marker, ' b' — a third "payload" segment means a nested group.
        expect(parts).toHaveLength(3);
        expect(parts.map(isInlineMathMarker)).toEqual([false, true, false]);
    });

    it('leaves no base64 payload in the visible text', () => {
        // The exact reported case: two spans, one of them ending in '=='.
        const src = 'which diverges as $\\lambda \\to \\mu^{-}$ — '
            + 'the familiar knee at $\\rho \\approx 1$.';
        const visible = renderToVisibleText(processInlineMath(src));

        expect(visible).toBe('which diverges as ⟦MATH⟧ — the familiar knee at ⟦MATH⟧.');
        // Payload residue would show up as the encoding of the LaTeX itself.
        // Asserted per-span rather than with a generic base64-shaped regex:
        // that alphabet also matches ordinary English words ("diverges"),
        // so a broad pattern would fail on correct output.
        for (const latex of ['\\lambda \\to \\mu^{-}', '\\rho \\approx 1']) {
            expect(visible).not.toContain(encodeInlineMathMarker(latex)
                .slice(MATH_INLINE_MARKER_PREFIX.length, -1));
        }
    });

    it('drops nothing: non-math text round-trips byte-identically', () => {
        // Pairs the negative assertion above with a positive one — the split
        // must not lose literal text either.
        const src = 'let $x$ vary while $y = 2$ holds, but $5 is money.';
        expect(renderToVisibleText(processInlineMath(src), '$MATH$'))
            .toBe('let $MATH$ vary while $MATH$ holds, but $5 is money.');
    });

    it('payload alphabets that end in padding do not leak', () => {
        // '=' padding sits outside [A-Za-z0-9+/] and is the boundary most
        // likely to be mis-grouped, so exercise all three padding lengths.
        for (const latex of ['\\mu^{-}', '\\mu^{--}', '\\mu^{---}']) {
            const visible = renderToVisibleText(processInlineMath(`v $${latex}$ w`));
            expect(visible).toBe('v ⟦MATH⟧ w');
        }
    });

    it('the split pattern has exactly one capture group', () => {
        // Direct structural assertion, so a future refactor that re-wraps
        // MATH_INLINE_MARKER_RE.source fails here with an obvious message
        // rather than as mysterious base64 in the rendered page.
        const probe = new RegExp(MATH_INLINE_MARKER_SPLIT_RE.source);
        const m = encodeInlineMathMarker('x').match(probe);
        expect(m).not.toBeNull();
        expect(m!.length - 1).toBe(1);
    });
});

