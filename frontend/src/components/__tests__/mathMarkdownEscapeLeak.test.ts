/**
 * Markdown `\*` escaping leaking into a math span.
 *
 * CLASS UNDER TEST
 * ----------------
 * Inside $...$ / $$...$$ markdown emphasis parsing is already suppressed, so a
 * `\*` written out of habit for a literal asterisk is not an escape — it is an
 * undefined control sequence. With throwOnError:false KaTeX paints it in red
 * error text, so `s^\* - c^\*` renders as two red `\*` glyphs instead of the
 * intended superscript stars.
 *
 * THE FIX
 * -------
 * sanitizeMathForKatex now runs normalizeMarkdownEscapesInMath, which rewrites
 * `\*` to `*` before the LaTeX reaches KaTeX.
 *
 * GENERAL RULE PINNED
 * -------------------
 *   1. `\*` -> `*`, anywhere in the span, however many times.
 *   2. Every OTHER backslash-punctuation pair markdown escapes (`\_`, `\#`,
 *      `\{`, `\}`, `\&`, `\%`, `\$`) is a legitimate LaTeX escape and is
 *      returned byte-unchanged.
 *   3. `\\*` (LaTeX line break followed by an asterisk / starred form) is NOT
 *      touched — the backslash before the `*` is part of `\\`, not an escape.
 *   4. The transform is reached through sanitizeMathForKatex (the seam every
 *      render path shares), not only when called directly.
 *   5. Consequence at the KaTeX layer: the raw form errors, the normalized
 *      form renders clean.
 *
 * marked and uuid are ESM-only and are imported transitively by
 * MarkdownRenderer, so they are stubbed at module scope to keep this suite on
 * the default jest runner (same boilerplate as unsupportedMathEnvAlias.test).
 */
jest.mock('marked', () => {
    const marked = (s: string) => s;
    Object.assign(marked, {
        parse: (s: string) => s, setOptions: () => {}, use: () => {},
        walkTokens: () => {}, parseInline: (s: string) => s,
    });
    return { marked, Tokens: {} };
});
jest.mock('uuid', () => ({ v4: () => 'test-uuid' }));

import katex from 'katex';
import { normalizeMarkdownEscapesInMath, sanitizeMathForKatex } from '../MarkdownRenderer';

/** The reported case: starred symbols in a display equation. */
const LEAKED = 's^\\* - c^\\* = \\underbrace{t_{\\text{dead}}}_{\\sim 100\\,\\mu s} + (d_A - d_B)';
const CLEAN = 's^* - c^* = \\underbrace{t_{\\text{dead}}}_{\\sim 100\\,\\mu s} + (d_A - d_B)';

const render = (latex: string): string =>
    katex.renderToString(latex, {
        displayMode: true, throwOnError: false, strict: false, errorColor: '#cc0000',
    });

describe('normalizeMarkdownEscapesInMath', () => {
    it('rewrites every \\* in the reported equation', () => {
        expect(normalizeMarkdownEscapesInMath(LEAKED)).toBe(CLEAN);
    });

    it('handles a bare \\* and repeated occurrences', () => {
        expect(normalizeMarkdownEscapesInMath('\\*')).toBe('*');
        expect(normalizeMarkdownEscapesInMath('a\\*b\\*c\\*')).toBe('a*b*c*');
    });

    it('leaves legitimate LaTeX escapes byte-unchanged', () => {
        for (const legit of ['\\_', '\\#', '\\{', '\\}', '\\&', '\\%', '\\$', '\\|', '\\,']) {
            expect(normalizeMarkdownEscapesInMath(`x ${legit} y`)).toBe(`x ${legit} y`);
        }
    });

    it('does not corrupt the \\\\* line break', () => {
        const lineBreak = '\\begin{gathered} a \\\\* b \\end{gathered}';
        expect(normalizeMarkdownEscapesInMath(lineBreak)).toBe(lineBreak);
    });

    it('leaves math with no escapes at all unchanged', () => {
        expect(normalizeMarkdownEscapesInMath(CLEAN)).toBe(CLEAN);
        expect(normalizeMarkdownEscapesInMath('\\frac{a}{b} \\cdot c^2')).toBe('\\frac{a}{b} \\cdot c^2');
    });
});

describe('sanitizeMathForKatex reaches the normalization (seam)', () => {
    it('strips \\* through the shared sanitize entry point', () => {
        expect(sanitizeMathForKatex(LEAKED)).not.toMatch(/\\\*/);
        expect(sanitizeMathForKatex(LEAKED)).toContain('s^* - c^*');
    });

    it('still applies the pre-existing corrections alongside it', () => {
        // \text{} underscore escaping must survive the added step.
        expect(sanitizeMathForKatex('x^\\* + \\text{a_b}')).toBe('x^* + \\text{a\\_b}');
    });
});

/**
 * KaTeX 0.16.x recovers from an undefined control sequence PER TOKEN rather
 * than failing the whole expression: with throwOnError:false it emits no
 * <span class="katex-error">, it paints just the offending token in
 * errorColor. So the marker for this defect is the errorColor itself, which is
 * exactly the red `\*` glyph seen on screen.
 */
describe('KaTeX render before vs after normalization', () => {
    it('the leaked form genuinely paints \\* in errorColor (defect is real)', () => {
        const html = render(LEAKED);
        expect(html).toContain('#cc0000');
        // The red glyph is the literal escape, not a typeset star.
        expect(html).toMatch(/color:#cc0000[^>]*>\\\*/);
    });

    it('the sanitized form renders with NO errorColor anywhere', () => {
        const html = render(sanitizeMathForKatex(LEAKED));
        expect(html).not.toContain('#cc0000');
        expect(html).toContain('katex');
    });

    it('the \\\\* line break KaTeX supports is left renderable', () => {
        // Proves rule 3 matters: \\* is valid, so corrupting it would be a
        // regression, not a fix.
        const lineBreak = '\\begin{gathered} a \\\\* b \\end{gathered}';
        expect(render(sanitizeMathForKatex(lineBreak))).not.toContain('#cc0000');
    });
});
