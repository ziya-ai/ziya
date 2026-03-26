/**
 * Tests for display math base64-encoding pipeline.
 *
 * Verifies that LaTeX characters like \\ (row separator) and & (column
 * separator) survive the extraction→encode→decode roundtrip used by
 * MarkdownRenderer to protect display math from markdown escape processing.
 *
 * Bug: marked.js inline parser converts \\ to \ (escape sequence),
 * destroying matrix row separators.  The fix base64-encodes the math
 * content so it's opaque to markdown processing.
 */

// Browser globals used by the encoding pipeline
// btoa/atob are available in jsdom (Jest default environment)

// Encode: the same logic used in MarkdownRenderer's extraction step
function encodeDisplayMath(content) {
    return btoa(unescape(encodeURIComponent(content.trim())));
}

// Decode: the same logic used in MarkdownRenderer's HTML handler
function decodeDisplayMath(encoded) {
    return decodeURIComponent(escape(atob(encoded)));
}

describe('display math base64 encoding', function () {

    describe('roundtrip preserves LaTeX content', function () {

        it('preserves double backslash (row separator)', function () {
            var input = 'a & b \\\\ c & d';
            var encoded = encodeDisplayMath(input);
            var decoded = decodeDisplayMath(encoded);
            expect(decoded).toBe(input);
        });

        it('preserves pmatrix environment with row breaks', function () {
            var input = '\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}';
            var encoded = encodeDisplayMath(input);
            var decoded = decodeDisplayMath(encoded);
            expect(decoded).toBe(input);
        });

        it('preserves ampersand column separators', function () {
            var input = 'x & y & z';
            var encoded = encodeDisplayMath(input);
            var decoded = decodeDisplayMath(encoded);
            expect(decoded).toBe(input);
        });

        it('preserves aligned environment', function () {
            var input = '\\begin{aligned} a &= b + c \\\\ d &= e + f \\end{aligned}';
            var encoded = encodeDisplayMath(input);
            var decoded = decodeDisplayMath(encoded);
            expect(decoded).toBe(input);
        });

        it('preserves backslash commands', function () {
            var input = '\\frac{\\partial f}{\\partial x} = \\nabla f \\cdot \\hat{x}';
            var encoded = encodeDisplayMath(input);
            var decoded = decodeDisplayMath(encoded);
            expect(decoded).toBe(input);
        });

        it('preserves curly braces', function () {
            var input = '{a}^{2} + {b}^{2} = {c}^{2}';
            var encoded = encodeDisplayMath(input);
            var decoded = decodeDisplayMath(encoded);
            expect(decoded).toBe(input);
        });

        it('preserves subscripts and superscripts', function () {
            var input = 'x_{i}^{2} + y_{j}^{3}';
            var encoded = encodeDisplayMath(input);
            var decoded = decodeDisplayMath(encoded);
            expect(decoded).toBe(input);
        });

        it('handles empty content', function () {
            var encoded = encodeDisplayMath('');
            var decoded = decodeDisplayMath(encoded);
            expect(decoded).toBe('');
        });

        it('trims surrounding whitespace', function () {
            var input = '  a + b  ';
            var encoded = encodeDisplayMath(input);
            var decoded = decodeDisplayMath(encoded);
            expect(decoded).toBe('a + b');
        });
    });

    describe('encoded output is markdown-safe', function () {

        it('contains only base64 characters', function () {
            var input = '\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}';
            var encoded = encodeDisplayMath(input);
            expect(encoded).toMatch(/^[A-Za-z0-9+/=]*$/);
        });

        it('contains no backslashes', function () {
            var input = '\\frac{a}{b} \\\\ \\sqrt{c}';
            var encoded = encodeDisplayMath(input);
            expect(encoded).not.toContain('\\');
        });

        it('contains no ampersands', function () {
            var input = 'a & b & c';
            var encoded = encodeDisplayMath(input);
            expect(encoded).not.toContain('&');
        });

        it('contains no angle brackets', function () {
            var input = 'a < b > c';
            var encoded = encodeDisplayMath(input);
            expect(encoded).not.toContain('<');
            expect(encoded).not.toContain('>');
        });
    });

    describe('unicode support', function () {

        it('preserves Greek letters', function () {
            var input = 'α + β = γ';
            var encoded = encodeDisplayMath(input);
            var decoded = decodeDisplayMath(encoded);
            expect(decoded).toBe(input);
        });

        it('preserves mathematical symbols', function () {
            var input = '∫₀^∞ e^{-x} dx = 1';
            var encoded = encodeDisplayMath(input);
            var decoded = decodeDisplayMath(encoded);
            expect(decoded).toBe(input);
        });

        it('preserves CJK characters in text commands', function () {
            var input = '\\text{面積} = \\pi r^2';
            var encoded = encodeDisplayMath(input);
            var decoded = decodeDisplayMath(encoded);
            expect(decoded).toBe(input);
        });
    });

    describe('data attribute extraction regex', function () {

        it('extracts encoded content from data-math attribute', function () {
            var math = '\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}';
            var encoded = encodeDisplayMath(math);
            var html = '<div class="math-display-encoded" data-math="' + encoded + '"></div>';

            var match = html.match(/data-math="([^"]*)"/);
            expect(match).not.toBeNull();
            expect(match[1]).toBe(encoded);

            var decoded = decodeDisplayMath(match[1]);
            expect(decoded).toBe(math);
        });

        it('class name check identifies encoded math divs', function () {
            var html = '<div class="math-display-encoded" data-math="YSAmIGI="></div>';
            expect(html.includes('math-display-encoded')).toBe(true);
        });

        it('does not false-positive on non-encoded math divs', function () {
            var html = '<div class="math-display-block">MATH_DISPLAY:a + b</div>';
            expect(html.includes('math-display-encoded')).toBe(false);
        });
    });

    describe('regression: matrix row separators', function () {

        it('2x2 matrix preserves both \\\\ separators', function () {
            var input = '\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}';
            var encoded = encodeDisplayMath(input);
            var decoded = decodeDisplayMath(encoded);
            // Count occurrences of \\
            var count = (decoded.match(/\\\\/g) || []).length;
            expect(count).toBe(1);
        });

        it('3x3 matrix preserves all \\\\ separators', function () {
            var input = '1 & 0 & 0 \\\\ 0 & 1 & 0 \\\\ 0 & 0 & 1';
            var encoded = encodeDisplayMath(input);
            var decoded = decodeDisplayMath(encoded);
            var count = (decoded.match(/\\\\/g) || []).length;
            expect(count).toBe(2);
        });

        it('multi-line aligned equations preserve all \\\\ separators', function () {
            var input = '\\begin{aligned}\n  a &= 1 \\\\\n  b &= 2 \\\\\n  c &= 3\n\\end{aligned}';
            var encoded = encodeDisplayMath(input);
            var decoded = decodeDisplayMath(encoded);
            var count = (decoded.match(/\\\\/g) || []).length;
            expect(count).toBe(2);
        });
    });
});
