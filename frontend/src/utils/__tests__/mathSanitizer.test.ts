/**
 * The shared math sanitizer: one implementation for the browser AND the
 * HTML exporter.
 *
 * WHY THIS MODULE EXISTS
 * ----------------------
 * Two code paths render the same math:
 *   1. the browser -- MarkdownRenderer's MathRenderer plus the HTML-string
 *      path used by reasoning blocks;
 *   2. app/utils/conversation_exporter.py, which has no browser and shells
 *      out to Node.
 *
 * They were separately implemented and had DRIFTED: the exporter applied none
 * of the corrections, so `\text{a_b}` and `\begin{multline}` exported with red
 * KaTeX error glyphs while rendering correctly on screen. The exporter now
 * requires THIS FILE, so a correction added here reaches both. That is the
 * property under test -- the parity half lives in
 * tests/test_math_sanitizer_parity.py, which asserts the Python side really
 * does load this file and nothing else.
 *
 * WHAT IS PINNED
 * --------------
 *   1. Each transform in isolation, including its no-op cases.
 *   2. sanitizeMathForKatex composes all four, and is IDEMPOTENT (it runs on
 *      already-corrected text whenever a document is re-rendered).
 *   3. Legitimate LaTeX escapes survive byte-unchanged -- the negative
 *      controls that stop a broad "strip backslashes" fix from passing.
 *   4. The environment alias is hasOwnProperty-guarded. Without it,
 *      `\begin{constructor}` inherited Object.prototype.constructor and became
 *      `\begin{function Object() { [native code] }}`.
 *   5. KATEX_RENDER_OPTIONS carries the interpretation options and NOT
 *      `output`, which is the one setting that legitimately differs per path.
 *
 * These are pure string assertions; the KaTeX consequence is proved separately
 * by unsupportedMathEnvKatexRender.test.ts and mathMarkdownEscapeLeak.test.ts,
 * which feed real KaTeX.
 */
import {
    KATEX_ERROR_COLOR,
    KATEX_RENDER_OPTIONS,
    UNSUPPORTED_MATH_ENV_ALIASES,
    escapeUnderscoresInTextCommands,
    normalizeSemicolonSpacing,
    normalizeMarkdownEscapesInMath,
    normalizeUnsupportedMathEnvironments,
    sanitizeMathForKatex,
} from '../mathSanitizer';

/** Backslash-punctuation pairs that are real LaTeX and must never be touched. */
const LEGITIMATE_ESCAPES = ['\\_', '\\#', '\\{', '\\}', '\\&', '\\%', '\\$', '\\|', '\\,'];

describe('normalizeMarkdownEscapesInMath', () => {
    it('rewrites a leaked markdown \\* to a bare asterisk', () => {
        expect(normalizeMarkdownEscapesInMath('s^\\* - c^\\*')).toBe('s^* - c^*');
        expect(normalizeMarkdownEscapesInMath('\\*')).toBe('*');
        expect(normalizeMarkdownEscapesInMath('a\\*b\\*c\\*')).toBe('a*b*c*');
    });

    it('leaves every legitimate LaTeX escape byte-unchanged', () => {
        for (const esc of LEGITIMATE_ESCAPES) {
            expect(normalizeMarkdownEscapesInMath(`x ${esc} y`)).toBe(`x ${esc} y`);
        }
    });

    it('preserves the \\\\* line break', () => {
        // A '\\' followed by '*' renders cleanly in KaTeX; the negative
        // lookbehind is what stops the fix from corrupting it.
        const src = '\\begin{gathered} a \\\\* b \\end{gathered}';
        expect(normalizeMarkdownEscapesInMath(src)).toBe(src);
    });
});

describe('escapeUnderscoresInTextCommands', () => {
    it('escapes bare underscores inside every text-mode command', () => {
        expect(escapeUnderscoresInTextCommands('\\text{ct_id_field}'))
            .toBe('\\text{ct\\_id\\_field}');
        for (const cmd of ['texttt', 'textbf', 'textit', 'textrm', 'textsf',
                           'textmd', 'mathrm', 'operatorname']) {
            expect(escapeUnderscoresInTextCommands(`\\${cmd}{a_b}`))
                .toBe(`\\${cmd}{a\\_b}`);
        }
    });

    it('does not double-escape an already-escaped underscore', () => {
        expect(escapeUnderscoresInTextCommands('\\text{a\\_b}')).toBe('\\text{a\\_b}');
    });

    it('leaves a subscript OUTSIDE a text command alone', () => {
        // t_{dead} is a real subscript, not prose; escaping it would break it.
        expect(escapeUnderscoresInTextCommands('t_{dead}')).toBe('t_{dead}');
    });
});

describe('normalizeSemicolonSpacing', () => {
    it('promotes a bare semicolon before a command to a thick space', () => {
        expect(normalizeSemicolonSpacing('a ; \\cdot b')).toBe('a \\; \\cdot b');
    });

    it('leaves an already-correct \\; alone and ignores other semicolons', () => {
        expect(normalizeSemicolonSpacing('a \\; \\cdot b')).toBe('a \\; \\cdot b');
        expect(normalizeSemicolonSpacing('f(x; y)')).toBe('f(x; y)');
    });
});

describe('normalizeUnsupportedMathEnvironments', () => {
    it('aliases each unsupported amsmath environment to gathered', () => {
        for (const env of Object.keys(UNSUPPORTED_MATH_ENV_ALIASES)) {
            const out = normalizeUnsupportedMathEnvironments(
                `\\begin{${env}} x \\end{${env}}`);
            expect(out).toBe('\\begin{gathered} x \\end{gathered}');
        }
    });

    it('drops \\shoveright/\\shoveleft but keeps the argument', () => {
        expect(normalizeUnsupportedMathEnvironments('\\shoveright{x + y}')).toBe('{x + y}');
        expect(normalizeUnsupportedMathEnvironments('\\shoveleft{x}')).toBe('{x}');
    });

    it('returns natively-supported environments byte-unchanged', () => {
        for (const env of ['gather', 'gathered', 'align', 'aligned', 'split',
                           'cases', 'array', 'pmatrix', 'bmatrix', 'vmatrix']) {
            const src = `\\begin{${env}} x \\end{${env}}`;
            expect(normalizeUnsupportedMathEnvironments(src)).toBe(src);
        }
    });

    it('does not resolve an environment name off Object.prototype', () => {
        // Regression: an unguarded ALIASES[name] lookup turned
        // \begin{constructor} into \begin{function Object() { [native code] }}.
        for (const name of ['constructor', 'toString', 'valueOf', 'hasOwnProperty']) {
            const src = `\\begin{${name}} x \\end{${name}}`;
            expect(normalizeUnsupportedMathEnvironments(src)).toBe(src);
        }
        // __proto__ is not an own property of the literal either.
        expect(normalizeUnsupportedMathEnvironments('\\begin{__proto__} x \\end{__proto__}'))
            .toBe('\\begin{__proto__} x \\end{__proto__}');
    });

    it('leaves prose containing the substring "multline" alone', () => {
        const prose = 'the multline environment is unsupported';
        expect(normalizeUnsupportedMathEnvironments(prose)).toBe(prose);
    });
});

describe('sanitizeMathForKatex', () => {
    it('applies every correction in one pass', () => {
        const out = sanitizeMathForKatex(
            '\\begin{multline} s^\\* = \\text{a_b} ; \\cdot 1 \\end{multline}');
        expect(out).toContain('\\begin{gathered}');
        expect(out).toContain('s^* =');
        expect(out).toContain('\\text{a\\_b}');
        expect(out).toContain('\\; \\cdot');
        expect(out).not.toContain('multline');
        expect(out).not.toContain('\\*');
    });

    it('is idempotent', () => {
        // A document is re-sanitized on every re-render; a second pass must
        // not double-escape or re-rewrite anything.
        const inputs = [
            's^\\* - c^\\*',
            '\\text{ct_id_field}',
            '\\begin{multline} a \\\\ b \\end{multline}',
            'a ; \\cdot b',
            '\\underbrace{t_{\\text{dead}}}_{\\sim 100\\,\\mu s}',
            '',
        ];
        for (const src of inputs) {
            const once = sanitizeMathForKatex(src);
            expect(sanitizeMathForKatex(once)).toBe(once);
        }
    });

    it('leaves ordinary math untouched', () => {
        for (const src of ['E = mc^2', '\\frac{a}{b} \\cdot c^2', 'x \\\\ y',
                           '\\int_0^1 x^2 \\, dx = \\frac{1}{3}']) {
            expect(sanitizeMathForKatex(src)).toBe(src);
        }
    });
});

describe('KATEX_RENDER_OPTIONS', () => {
    it('carries the interpretation options', () => {
        expect(KATEX_RENDER_OPTIONS.throwOnError).toBe(false);
        expect(KATEX_RENDER_OPTIONS.strict).toBe(false);
        expect(KATEX_RENDER_OPTIONS.errorColor).toBe(KATEX_ERROR_COLOR);
        expect(KATEX_RENDER_OPTIONS.macros).toHaveProperty('\\f');
    });

    it('does NOT pin output, which legitimately differs per path', () => {
        // The browser wants the default htmlAndMathml; the exporter forces
        // "mathml" for a self-contained document.  Pinning it here would
        // silently break one of the two.
        expect(KATEX_RENDER_OPTIONS).not.toHaveProperty('output');
    });

    it('exposes the error colour KaTeX paints a bad token in', () => {
        expect(KATEX_ERROR_COLOR).toMatch(/^#[0-9a-f]{6}$/i);
    });
});
