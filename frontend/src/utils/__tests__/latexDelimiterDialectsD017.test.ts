/**
 * D-017 (w3-07) — the LaTeX bracket/paren math delimiter dialects.
 *
 * The chat renderer only recognised `$$...$$`, `$...$`, and ```` ```math ````/
 * ```` ```latex ```` fences, so a model that wrote `\( ... \)` (inline) or
 * `\[ ... \]` (display) — both standard TeX / KaTeX auto-render delimiters —
 * had its LaTeX source leak through verbatim instead of being typeset.
 *
 * `convertLatexDelimiterDialects` rewrites those pairs to the dollar dialects
 * the existing extraction passes already understand.  This suite asserts the
 * rewrite AND its guards.
 *
 * DIRECTION: this file lives beside the helper it exercises.  On the unpatched
 * tree the module `../latexDelimiterDialects` does not exist, so the require
 * below yields `undefined` and every assertion throws — the suite fails
 * without the fix and passes with it.  (Resolved via require rather than a
 * static import so the failure is the assertion, not a compile-time
 * module-resolution error.)
 */
// eslint-disable-next-line @typescript-eslint/no-var-requires
const mod = require('../latexDelimiterDialects');
const convert: (s: string) => string = mod.convertLatexDelimiterDialects;

describe('D-017 w3-07: LaTeX \\(..\\) / \\[..\\] delimiter dialects', () => {
    it('exposes the converter (absent on the unpatched tree)', () => {
        expect(typeof convert).toBe('function');
    });

    it('rewrites inline \\( ... \\) to $ ... $', () => {
        expect(convert('Pythagoras: \\(a^2 + b^2 = c^2\\).'))
            .toBe('Pythagoras: $a^2 + b^2 = c^2$.');
    });

    it('rewrites display \\[ ... \\] to a block-separated $$ ... $$', () => {
        const out = convert('Energy:\n\\[E = mc^2\\]\nis famous.');
        expect(out).toContain('$$E = mc^2$$');
        // Block-separated so marked lexes it as its own block, not inline text.
        expect(out).toContain('\n\n$$E = mc^2$$\n\n');
        // The literal LaTeX source must no longer leak.
        expect(out).not.toContain('\\[');
        expect(out).not.toContain('\\]');
    });

    it('handles a multi-line display span', () => {
        const out = convert('\\[\n\\begin{aligned} x &= 1 \\end{aligned}\n\\]');
        expect(out).toContain('$$');
        expect(out).toContain('\\begin{aligned}');
        expect(out).not.toContain('\\[');
    });

    it('leaves an escaped \\\\[ (TeX line break + bracket) untouched', () => {
        // "\\[2ex]" is a line break with vertical spacing, NOT a display opener.
        const src = 'row one \\\\[2ex] row two';
        expect(convert(src)).toBe(src);
        expect(convert(src)).not.toContain('$$');
    });

    it('leaves an escaped \\\\( untouched', () => {
        const src = 'break then paren \\\\(not math)';
        expect(convert(src)).toBe(src);
        expect(convert(src)).not.toContain('$');
    });

    it('is a no-op on content with no backslash delimiters', () => {
        const src = 'plain text with [brackets] and (parens) and $money$';
        expect(convert(src)).toBe(src);
    });

    it('converts both dialects present together', () => {
        const out = convert('inline \\(x\\) and display \\[y\\] here');
        expect(out).toContain('$x$');
        expect(out).toContain('$$y$$');
        expect(out).not.toContain('\\(');
        expect(out).not.toContain('\\[');
    });
});
