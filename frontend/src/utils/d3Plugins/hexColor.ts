/**
 * Robust hex-color -> RGB parser shared by the D3 diagram color utilities.
 *
 * Extracted as a standalone pure helper (no DOM) so it can be unit-tested in
 * isolation. The original inline implementation in colorUtils.ts only matched
 * 6-digit hex (`/^#?([a-f\d]{2}){3}$/i`), so common shorthand forms emitted by
 * renderers — notably Mermaid's `#000`/`#fff` — parsed as `null`. That null then
 * bubbled up through `calculateContrastRatio` as a `COLOR-PARSE-FAIL` and forced
 * the contrast ratio to a degenerate `1`, silently disabling the text-contrast
 * enhancement pass for any diagram that used shorthand hex.
 *
 * This helper accepts every well-formed CSS hex form and discards alpha:
 *   - 3-digit shorthand   `#abc`      -> `#aabbcc`
 *   - 4-digit shorthand   `#abcd`     -> `#aabbcc` (alpha `dd` dropped)
 *   - 6-digit             `#aabbcc`
 *   - 8-digit RGBA        `#aabbccdd` -> `#aabbcc` (alpha `dd` dropped)
 * A leading `#` is optional. Anything that is not a well-formed hex string
 * (named colors, `rgb(...)`, garbage) returns `null` — callers are expected to
 * fall back to their own named/rgb parsing, exactly as before. This keeps the
 * fix additive: it only WIDENS what parses successfully, and still REJECTS
 * everything the old regex rejected that was not valid hex.
 */
export function hexToRgbSafe(hex: string): { r: number; g: number; b: number } | null {
    if (!hex || typeof hex !== 'string') {
        return null;
    }
    let h = hex.trim().replace(/^#/, '');
    // Expand 3-digit (#abc -> #aabbcc) and 4-digit (#abcd -> #aabbccdd) shorthand
    // by doubling every nibble.
    if (/^[a-f\d]{3}$/i.test(h) || /^[a-f\d]{4}$/i.test(h)) {
        h = h.split('').map(ch => ch + ch).join('');
    }
    // Match 6 (RGB) or 8 (RGBA) hex digits; the optional trailing pair (alpha)
    // is matched but not captured, so it is parsed-and-discarded.
    const result = /^([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})(?:[a-f\d]{2})?$/i.exec(h);
    return result ? {
        r: parseInt(result[1], 16),
        g: parseInt(result[2], 16),
        b: parseInt(result[3], 16)
    } : null;
}
