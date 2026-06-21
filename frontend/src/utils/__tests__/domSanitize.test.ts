/**
 * @jest-environment jsdom
 *
 * Regression tests for ASR F-026: DOMPurify-backed HTML sanitization.
 *
 * These pin the bypass classes that defeat regex sanitizers — HTML entities,
 * unquoted attributes, SVG/MathML namespace tricks, and mutation XSS — which
 * the parser-based DOMPurify pass must neutralize.
 */
import { sanitizeModelHtml, sanitizeMockupHtml, sanitizeMathMl } from '../domSanitize';

describe('sanitizeModelHtml', () => {
    it('strips <script> tags', () => {
        const out = sanitizeModelHtml('<p>hi</p><script>alert(1)</script>');
        expect(out).not.toContain('<script');
        expect(out).toContain('hi');
    });

    it('strips event-handler attributes (quoted)', () => {
        const out = sanitizeModelHtml('<img src=x onerror="alert(1)">');
        expect(out.toLowerCase()).not.toContain('onerror');
    });

    it('strips event handlers written WITHOUT quotes (regex bypass class)', () => {
        const out = sanitizeModelHtml('<img src=x onerror=alert(1)>');
        expect(out.toLowerCase()).not.toContain('onerror');
    });

    it('strips HTML-entity-encoded handler (regex bypass class)', () => {
        // <img src=x onerror&#61;alert(1)> — entity-encoded "=" defeats naive regex
        const out = sanitizeModelHtml('<img src=x onerror&#61;alert(1)>');
        expect(out.toLowerCase()).not.toContain('alert(1)');
    });

    it('neutralizes SVG-wrapped script (namespace trick)', () => {
        const out = sanitizeModelHtml('<svg><script>alert(1)</script></svg>');
        expect(out).not.toContain('alert(1)');
    });

    it('removes javascript: hrefs', () => {
        const out = sanitizeModelHtml('<a href="javascript:alert(1)">x</a>');
        expect(out.toLowerCase()).not.toContain('javascript:');
    });

    it('preserves legitimate rich text', () => {
        const out = sanitizeModelHtml(
            '<p>See <a href="https://example.com">link</a> and <strong>bold</strong></p>'
        );
        expect(out).toContain('<strong>');
        expect(out).toContain('href="https://example.com"');
        expect(out).toContain('link');
    });

    it('forces rel/target on surviving anchors', () => {
        const out = sanitizeModelHtml('<a href="https://example.com">x</a>');
        expect(out).toContain('rel="noopener noreferrer"');
        expect(out).toContain('target="_blank"');
    });

    it('preserves tables (markdown output)', () => {
        const out = sanitizeModelHtml(
            '<table><thead><tr><th>A</th></tr></thead><tbody><tr><td>1</td></tr></tbody></table>'
        );
        expect(out).toContain('<table>');
        expect(out).toContain('<td>');
    });

    it('returns empty string for non-string / empty input', () => {
        expect(sanitizeModelHtml('')).toBe('');
        // @ts-expect-error — exercising runtime guard
        expect(sanitizeModelHtml(null)).toBe('');
        // @ts-expect-error
        expect(sanitizeModelHtml(undefined)).toBe('');
    });
});

describe('sanitizeMockupHtml', () => {
    it('removes scripts from a full document', () => {
        const out = sanitizeMockupHtml(
            '<html><head><style>body{color:red}</style></head>' +
            '<body><h1>Mockup</h1><script>fetch("/api/chat")</script></body></html>'
        );
        expect(out).not.toContain('<script');
        expect(out).not.toContain('fetch(');
    });

    it('preserves layout + style markup', () => {
        const out = sanitizeMockupHtml(
            '<div style="padding:20px"><h2>Title</h2><button>Go</button></div>'
        );
        expect(out).toContain('<button>');
        expect(out).toContain('padding');
    });

    it('strips onerror handlers', () => {
        const out = sanitizeMockupHtml('<img src=x onerror=alert(1)>');
        expect(out.toLowerCase()).not.toContain('onerror');
    });

    it('returns empty string for empty input', () => {
        expect(sanitizeMockupHtml('')).toBe('');
    });
});

describe('sanitizeMathMl', () => {
    it('preserves legitimate MathML structure', () => {
        const out = sanitizeMathMl(
            '<math xmlns="http://www.w3.org/1998/Math/MathML">' +
            '<mrow><msup><mi>x</mi><mn>2</mn></msup></mrow></math>'
        );
        expect(out).toContain('<math');
        expect(out).toContain('msup');
        expect(out).toContain('<mi>x</mi>');
    });

    it('strips a script smuggled inside MathML (namespace XSS)', () => {
        const out = sanitizeMathMl(
            '<math><mtext><script>alert(1)</script></mtext></math>'
        );
        expect(out).not.toContain('<script');
        expect(out).not.toContain('alert(1)');
    });

    it('strips event handlers on MathML elements', () => {
        const out = sanitizeMathMl('<math><mi onerror="alert(1)">x</mi></math>');
        expect(out.toLowerCase()).not.toContain('onerror');
    });

    it('strips javascript: hrefs on MathML elements', () => {
        const out = sanitizeMathMl(
            '<math><mi href="javascript:alert(1)">x</mi></math>'
        );
        expect(out.toLowerCase()).not.toContain('javascript:');
    });

    it('returns empty string for empty input', () => {
        expect(sanitizeMathMl('')).toBe('');
    });
});
