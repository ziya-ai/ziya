/**
 * Regression test for ASR F-026 (DiffLine residual XSS).
 *
 * DiffLine renders each diff line's content via dangerouslySetInnerHTML.
 * For non-Prism (plain-text fallback) content — which is attacker-influenced
 * model output — the content MUST be HTML-escaped first, or a line like
 * `<img src=x onerror=alert(1)>` executes on every re-render. DiffLine routes
 * its plain-text branches through escapeHtml; this pins that escapeHtml
 * neutralizes the payload classes that reach that sink.
 */
import { escapeHtml } from '../../utils/htmlSanitize';

describe('escapeHtml (DiffLine plain-text sink, ASR F-026)', () => {
    it('neutralizes an onerror img payload', () => {
        const out = escapeHtml('<img src=x onerror=alert(1)>');
        expect(out).not.toContain('<img');
        expect(out).toContain('&lt;img');
    });

    it('neutralizes a script tag', () => {
        const out = escapeHtml('<script>alert(1)</script>');
        expect(out).not.toContain('<script>');
        expect(out).toContain('&lt;script&gt;');
    });

    it('escapes angle brackets and ampersands', () => {
        expect(escapeHtml('a < b && c > d')).toBe('a &lt; b &amp;&amp; c &gt; d');
    });

    it('escapes double quotes (attribute-breakout guard)', () => {
        expect(escapeHtml('x"onmouseover="alert(1)')).toContain('&quot;');
    });

    it('leaves benign code content readable', () => {
        // Real diff content with no HTML metacharacters round-trips unchanged.
        const code = 'const x = foo(bar, baz);';
        expect(escapeHtml(code)).toBe(code);
    });

    it('preserves whitespace (markers are added separately by DiffLine)', () => {
        // escapeHtml must not touch spaces/tabs — DiffLine appends its own
        // whitespace-marker spans after escaping.
        expect(escapeHtml('  \tindented')).toBe('  \tindented');
    });
});
