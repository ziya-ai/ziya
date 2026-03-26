/**
 * Tests for the stableTokenKey function used by renderTokens to generate
 * content-based React keys that survive token insertion during streaming.
 *
 * The function is module-private in MarkdownRenderer.tsx, so we replicate
 * its logic here to verify the contract independently.
 */

// Replicate the production implementation for unit testing.
// If the implementation changes, update this copy to match.
function stableTokenKey(
    token: { type?: string; text?: string; raw?: string },
    dupCount: number,
): string {
    const sample = (token.text || token.raw || '').slice(0, 120);
    let hash = 5381;
    for (let i = 0; i < sample.length; i++) {
        hash = ((hash << 5) + hash + sample.charCodeAt(i)) | 0;
    }
    const base = `${token.type || 'u'}-${(hash >>> 0).toString(36)}`;
    return dupCount > 0 ? `${base}-${dupCount}` : base;
}

describe('stableTokenKey', () => {
    it('produces the same key for the same token content regardless of position', () => {
        const token = { type: 'code', text: 'diff --git a/foo b/foo\n--- a/foo\n+++ b/foo' };
        const key1 = stableTokenKey(token, 0);
        const key2 = stableTokenKey(token, 0);
        expect(key1).toBe(key2);
    });

    it('produces different keys for different token content', () => {
        const tokenA = { type: 'code', text: 'diff --git a/foo b/foo' };
        const tokenB = { type: 'code', text: 'diff --git a/bar b/bar' };
        expect(stableTokenKey(tokenA, 0)).not.toBe(stableTokenKey(tokenB, 0));
    });

    it('produces different keys for different token types with same text', () => {
        const tokenA = { type: 'paragraph', text: 'hello world' };
        const tokenB = { type: 'heading', text: 'hello world' };
        expect(stableTokenKey(tokenA, 0)).not.toBe(stableTokenKey(tokenB, 0));
    });

    it('disambiguates duplicate tokens via dupCount', () => {
        const token = { type: 'paragraph', text: 'repeated content' };
        const key0 = stableTokenKey(token, 0);
        const key1 = stableTokenKey(token, 1);
        const key2 = stableTokenKey(token, 2);
        expect(key0).not.toBe(key1);
        expect(key1).not.toBe(key2);
        expect(key0).not.toBe(key2);
    });

    it('handles empty tokens gracefully', () => {
        const emptyToken = { type: 'space' };
        const key = stableTokenKey(emptyToken, 0);
        expect(key).toBeTruthy();
        expect(key).toMatch(/^space-/);
    });

    it('handles tokens without a type', () => {
        const token = { text: 'orphaned text' };
        const key = stableTokenKey(token, 0);
        expect(key).toMatch(/^u-/); // 'u' for undefined type
    });

    it('uses raw field when text is missing', () => {
        const token = { type: 'html', raw: '<div>hello</div>' };
        const key = stableTokenKey(token, 0);
        expect(key).toMatch(/^html-/);
        // Should differ from an empty html token
        const emptyHtml = { type: 'html' };
        expect(key).not.toBe(stableTokenKey(emptyHtml, 0));
    });

    it('caps sample at 120 chars so long diffs do not degrade performance', () => {
        const shortToken = { type: 'code', text: 'A'.repeat(120) };
        const longToken = { type: 'code', text: 'A'.repeat(120) + 'BBBBB' };
        // Both should produce the same key since only first 120 chars are hashed
        expect(stableTokenKey(shortToken, 0)).toBe(stableTokenKey(longToken, 0));
    });
});
