/**
 * Pinning test for the unsupported-amsmath-environment alias (spec6-d1).
 *
 * CLASS UNDER TEST
 * ----------------
 * The bundled KaTeX implements gather/gathered/align/aligned/split/cases/
 * array/matrix... but NOT the amsmath `multline`/`multlined` environments.
 * A legitimate `$$\begin{multline}...\end{multline}$$` therefore reached KaTeX
 * and rendered as a RED throwOnError:false error glyph (a defect per the run's
 * ground rules — red KaTeX errors are failures).
 *
 * THE FIX
 * -------
 * MathRenderer's sanitize step now runs `normalizeUnsupportedMathEnvironments`,
 * which rewrites the unsupported environment NAME to its closest
 * KaTeX-supported equivalent (`gathered`) and strips the multline-only
 * \shoveright/\shoveleft hints. It keys off the environment name — a fixed
 * capability gap in the bundled KaTeX — never off prose or any spec's literal
 * text, and it leaves natively-supported environments byte-unchanged.
 *
 * GENERAL RULE PINNED
 * -------------------
 *   1. multline / multline* / multlined  ->  gathered (both \begin and \end).
 *   2. \shoveright{X}/\shoveleft{X}  ->  {X} (command dropped, content kept).
 *   3. Every natively-supported environment (gather/gathered/align/aligned/
 *      split/cases/array/pmatrix/bmatrix/vmatrix/...) is returned VERBATIM —
 *      the transform must never touch what KaTeX already renders.
 *   4. Prose that merely contains the substring "multline" is untouched
 *      because the rewrite only matches inside \begin{...}/\end{...}.
 *
 * marked and uuid are ESM-only; MarkdownRenderer imports them transitively, so
 * they are stubbed at module scope (matching MusicInlineRenderer.test.tsx and
 * the other suites that import from this module) to keep the suite on the
 * DEFAULT jest runner — no ESM transform override required.
 */
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

import { normalizeUnsupportedMathEnvironments } from '../MarkdownRenderer';

describe('normalizeUnsupportedMathEnvironments (spec6-d1)', () => {
    it('aliases \\begin/\\end{multline} to gathered', () => {
        const input = '\\begin{multline} a + b + c + d + e \\\\ + f + g + h + i + j \\end{multline}';
        const out = normalizeUnsupportedMathEnvironments(input);
        expect(out).toBe('\\begin{gathered} a + b + c + d + e \\\\ + f + g + h + i + j \\end{gathered}');
        expect(out).not.toMatch(/multline/);
    });

    it('aliases the starred multline* form', () => {
        expect(normalizeUnsupportedMathEnvironments('\\begin{multline*} x \\end{multline*}'))
            .toBe('\\begin{gathered} x \\end{gathered}');
    });

    it('aliases multlined', () => {
        expect(normalizeUnsupportedMathEnvironments('\\begin{multlined} x \\\\ y \\end{multlined}'))
            .toBe('\\begin{gathered} x \\\\ y \\end{gathered}');
    });

    it('drops \\shoveright/\\shoveleft but keeps their braced content', () => {
        const input = '\\begin{multline} a \\\\ \\shoveright{b + c} \\\\ \\shoveleft{d} \\end{multline}';
        const out = normalizeUnsupportedMathEnvironments(input);
        expect(out).toBe('\\begin{gathered} a \\\\ {b + c} \\\\ {d} \\end{gathered}');
        expect(out).not.toMatch(/shove/);
    });

    // --- regression guards: natively-supported environments must be untouched ---
    it.each([
        '\\begin{gather} a \\\\ b \\end{gather}',
        '\\begin{gathered} a \\\\ b \\end{gathered}',
        '\\begin{align} a &= b \\\\ c &= d \\end{align}',
        '\\begin{aligned} a &= b \\end{aligned}',
        '\\begin{split} a &= b \\end{split}',
        '\\begin{cases} 1 & x>0 \\\\ 0 & x\\le 0 \\end{cases}',
        '\\begin{array}{c|c} a & b \\\\ c & d \\end{array}',
        '\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}',
        '\\begin{bmatrix} 1 & 2 \\\\ 3 & 4 \\end{bmatrix}',
        '\\begin{vmatrix} a & b \\\\ c & d \\end{vmatrix}',
    ])('leaves supported environment %s byte-identical', (input) => {
        expect(normalizeUnsupportedMathEnvironments(input)).toBe(input);
    });

    it('does not touch a "multline" substring that is not an environment name', () => {
        // \text{...} content and prose must be immune: only \begin/\end match.
        const input = '\\text{see the multline docs} + x^2';
        expect(normalizeUnsupportedMathEnvironments(input)).toBe(input);
    });

    it('leaves ordinary math with no environment unchanged', () => {
        expect(normalizeUnsupportedMathEnvironments('E = mc^2')).toBe('E = mc^2');
    });
});
