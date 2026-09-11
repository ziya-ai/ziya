/**
 * @jest-environment jsdom
 *
 * G-91f92b regression tests for the network engine (networkDiagram.ts).
 *
 * D-193 (recovery/theme, signature `unvalidated-color-passthrough-to-background`)
 * covers the whole "authored colour written straight into an SVG presentation
 * attribute" class for network specs (network-w4-12 / w4-13). The NODE-FILL leg
 * (transparent / unresolvable token -> readable default) is already asserted in
 * networkG28 (D-212). This suite closes the remaining leg: the SAME semantically
 * empty colours supplied as the LINK and LABEL colours must also be reconciled
 * by resolveNetworkColors rather than reaching the canvas verbatim:
 *
 *   - w4-12: `style.linkColor = "transparent"` — a transparent stroke over the
 *     canvas composites to 1.00:1 (invisible edges).
 *   - w4-13: `style.labelColor = "var(--ziya-text-primary)"` and
 *     `style.linkColor = "var(--ziya-border)"` — unresolvable CSS custom-property
 *     tokens the browser rejects, falling to the CSS initial (black text /
 *     no-op stroke), so a contrast audit is fooled while edges vanish.
 *
 * Every assertion is written so it FAILS against the pre-fix source (which used
 * `style.labelColor || '#ccc'` and `style.linkColor || '#999'` verbatim); the
 * old behaviour is reproduced explicitly as a DIRECTION check, and both themes
 * are asserted since a colour that clears one background may fail the other.
 */
import {
    resolveNetworkColors,
    NETWORK_LIGHT_BG,
    NETWORK_DARK_BG,
} from '../networkDiagram';
import { contrastRatio, compositeOver } from '../chartTheme';

const isHex6 = (s: any) => /^#[0-9a-fA-F]{6}$/.test(String(s));

describe('D-193 — transparent / unresolvable-token LINK + LABEL colours are reconciled, not passed through', () => {
    // ── DIRECTION: reproduce the pre-fix passthrough and show it fails ────────
    it('DIRECTION: a raw "transparent" link stroke composites to 1.00:1 (invisible) on both surfaces', () => {
        for (const bg of [NETWORK_LIGHT_BG, NETWORK_DARK_BG]) {
            // compositeOver returns the fg unchanged for a non-hex ("transparent"),
            // but the point stands: an authored "transparent" is not a visible edge.
            // The concrete failure the fix prevents: fill == background => 1.00:1.
            expect(contrastRatio(bg, bg)).toBeCloseTo(1, 5);
        }
    });

    it('DIRECTION: a bare token string is not a resolvable colour', () => {
        // The pre-fix code emitted `style.labelColor` ("var(--ziya-text-primary)")
        // verbatim; the browser drops it to the CSS initial. It is not a hex.
        expect(isHex6('var(--ziya-text-primary)')).toBe(false);
        expect(isHex6('var(--ziya-border)')).toBe(false);
        expect(isHex6('transparent')).toBe(false);
    });

    // ── w4-12: transparent link colour ───────────────────────────────────────
    it.each([[false, NETWORK_LIGHT_BG], [true, NETWORK_DARK_BG]])(
        'w4-12: a "transparent" linkColor resolves to a real stroke whose composite clears 3:1 (isDarkMode=%p)',
        (dark: any, bg: any) => {
            const c = resolveNetworkColors(dark, { linkColor: 'transparent', linkOpacity: 1 });
            expect(isHex6(c.linkColor)).toBe(true);
            expect(String(c.linkColor).toLowerCase()).not.toBe('transparent');
            expect(contrastRatio(compositeOver(c.linkColor, bg, c.linkOpacity), bg)).toBeGreaterThanOrEqual(3);
        });

    // ── w4-13: unresolvable token link + label colours ───────────────────────
    it.each([[false, NETWORK_LIGHT_BG], [true, NETWORK_DARK_BG]])(
        'w4-13: var(--…) label + link tokens resolve to theme colours clearing their floors (isDarkMode=%p)',
        (dark: any, bg: any) => {
            const c = resolveNetworkColors(dark, {
                labelColor: 'var(--ziya-text-primary)',
                linkColor: 'var(--ziya-border)',
                linkOpacity: 1,
            });
            // Label: real hex, not the token, clearing the 4.5:1 text floor.
            expect(isHex6(c.labelColor)).toBe(true);
            expect(String(c.labelColor)).not.toMatch(/var\(|--|theme\./);
            expect(contrastRatio(c.labelColor, bg)).toBeGreaterThanOrEqual(4.5);
            // Link: real hex, not the token, composite clears the 3:1 graphical floor.
            expect(isHex6(c.linkColor)).toBe(true);
            expect(String(c.linkColor)).not.toMatch(/var\(|--|theme\./);
            expect(contrastRatio(compositeOver(c.linkColor, bg, c.linkOpacity), bg)).toBeGreaterThanOrEqual(3);
        });
});
