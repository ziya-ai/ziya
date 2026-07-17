/**
 * Regression test for PenPal #137 [CWE-400]: DiffLine skips syntax highlighting
 * for pathologically long lines.
 *
 * DiffLine highlights each line via window.Prism.highlight(), whose tokenization
 * cost grows super-linearly on very long input. A single giant line (minified
 * JS/CSS, an embedded data URI, a one-line JSON blob) in a diff could block the
 * main thread for seconds. `shouldSkipHighlight` gates the Prism path so an
 * over-long line falls through to the plain whitespace-visualized render
 * (still correct and HTML-escaped, just uncolored) instead of freezing the UI.
 */
import { shouldSkipHighlight, MAX_HIGHLIGHT_LINE_LENGTH } from '../DiffLine';

describe('DiffLine shouldSkipHighlight (PenPal #137)', () => {
    it('does NOT skip a normal-length line', () => {
        expect(shouldSkipHighlight('const x = foo(bar, baz);')).toBe(false);
    });

    it('does NOT skip a line exactly at the threshold', () => {
        const atLimit = 'a'.repeat(MAX_HIGHLIGHT_LINE_LENGTH);
        expect(shouldSkipHighlight(atLimit)).toBe(false);
    });

    it('skips a line just over the threshold', () => {
        const overLimit = 'a'.repeat(MAX_HIGHLIGHT_LINE_LENGTH + 1);
        expect(shouldSkipHighlight(overLimit)).toBe(true);
    });

    it('skips a pathologically long minified line', () => {
        // e.g. a minified bundle diff line — tens of thousands of chars.
        const minified = 'x'.repeat(200000);
        expect(shouldSkipHighlight(minified)).toBe(true);
    });

    it('handles an empty line without skipping', () => {
        expect(shouldSkipHighlight('')).toBe(false);
    });

    it('threshold is a positive bound (sanity)', () => {
        // Guards against a future edit zeroing the constant, which would skip
        // highlighting for every line (silently disabling syntax coloring).
        expect(MAX_HIGHLIGHT_LINE_LENGTH).toBeGreaterThan(100);
    });
});
