/**
 * @jest-environment jsdom
 *
 * G-1cd9c9 / D-019 (author-hardcoded-color-not-theme-normalized) — inline-HTML
 * half. The mermaid `%%{init}%%` half is covered by mermaidInitThemeContrast;
 * this exercises `remediateInlineStyleContrast`, which repairs author-hardcoded
 * inline-style colours in chat-message HTML that fail the WCAG-AA text floor
 * against the active theme surface (light #ffffff, dark #262626).
 *
 * BOTH-THEME obligation: every "fixed" assertion is paired with the raw pair
 * being genuinely below the floor (so the unpatched renderer would leave it
 * illegible — the failing direction), and a "left unchanged" assertion in the
 * theme where the author colour already reads (so the fix is a theme resolution,
 * not a blind constant swap that would break the other background).
 */

import {
    remediateInlineStyleContrast,
    chatMessageSurface,
} from '../../../utils/inlineStyleContrast';
import { calculateContrastRatio, getOptimalTextColor } from '../../../utils/colorUtils';

const FLOOR = 4.5;

// Pull the inline `color`/`opacity`/`background` value off the first element
// carrying it in a fragment.
const styleVal = (html: string, prop: string): string | undefined => {
    const m = html.match(new RegExp(prop + '\\s*:\\s*([^;"\']+)', 'i'));
    return m ? m[1].trim() : undefined;
};

describe('D-019: hardcoded inline text colour is repaired against the theme it fails on', () => {
    it('w3-11 muted #aaaaaa: illegible on the LIGHT surface (2.32:1) -> repaired; legible on DARK -> untouched', () => {
        const surface = chatMessageSurface(false);
        // Direction: raw pair is below the floor on the light surface.
        expect(calculateContrastRatio('#aaaaaa', surface)).toBeLessThan(FLOOR);

        const light = remediateInlineStyleContrast('<p style="color:#aaaaaa">muted</p>', false);
        const repaired = styleVal(light, 'color')!;
        expect(repaired.toLowerCase()).not.toBe('#aaaaaa');
        expect(calculateContrastRatio(repaired, surface)).toBeGreaterThanOrEqual(FLOOR);

        // Dark surface: #aaaaaa already reads (6.51:1) — left byte-for-byte.
        const darkIn = '<p style="color:#aaaaaa">muted</p>';
        expect(calculateContrastRatio('#aaaaaa', chatMessageSurface(true))).toBeGreaterThanOrEqual(FLOOR);
        expect(remediateInlineStyleContrast(darkIn, true)).toBe(darkIn);
    });

    it('w3-10 muted #666666: illegible on the DARK surface (2.64:1) -> repaired; legible on LIGHT -> untouched', () => {
        const dsurface = chatMessageSurface(true);
        expect(calculateContrastRatio('#666666', dsurface)).toBeLessThan(FLOOR);

        const dark = remediateInlineStyleContrast('<p style="color:#666666">muted</p>', true);
        const repaired = styleVal(dark, 'color')!;
        expect(repaired.toLowerCase()).not.toBe('#666666');
        expect(calculateContrastRatio(repaired, dsurface)).toBeGreaterThanOrEqual(FLOOR);

        const lightIn = '<p style="color:#666666">muted</p>';
        expect(calculateContrastRatio('#666666', chatMessageSurface(false))).toBeGreaterThanOrEqual(FLOOR);
        expect(remediateInlineStyleContrast(lightIn, false)).toBe(lightIn);
    });
});

describe('D-019: alpha and opacity text that composites below the floor is repaired (both themes)', () => {
    it('w3-12 rgba(120,120,120,0.5) text is below floor over BOTH surfaces and gets a readable colour', () => {
        for (const dark of [false, true]) {
            const surface = chatMessageSurface(dark);
            const out = remediateInlineStyleContrast(
                '<span style="color:rgba(120,120,120,0.5)">semi</span>', dark);
            const repaired = styleVal(out, 'color')!;
            expect(repaired.toLowerCase()).not.toContain('rgba');
            expect(calculateContrastRatio(repaired, surface)).toBeGreaterThanOrEqual(FLOOR);
        }
    });

    it('w3-12 opacity:0.35 dimmed text is restored to full opacity in BOTH themes', () => {
        for (const dark of [false, true]) {
            const out = remediateInlineStyleContrast(
                '<p style="opacity:0.35">dim body</p>', dark);
            expect(styleVal(out, 'opacity')).toBe('1');
        }
    });
});

describe('D-019: author palette on its OWN fill is theme-independent', () => {
    it('w3-15 pale #9fc7e0 on pale #eef6fb fill (1.64:1) is repaired in BOTH themes; the fill is kept', () => {
        expect(calculateContrastRatio('#9fc7e0', '#eef6fb')).toBeLessThan(FLOOR);
        for (const dark of [false, true]) {
            const out = remediateInlineStyleContrast(
                '<span style="background:#eef6fb;color:#9fc7e0">pale on pale</span>', dark);
            // Fill untouched, text repaired against the (author) fill.
            expect(out.toLowerCase()).toContain('#eef6fb');
            const repaired = styleVal(out, 'color')!;
            expect(repaired.toLowerCase()).not.toBe('#9fc7e0');
            expect(calculateContrastRatio(repaired, '#eef6fb')).toBeGreaterThanOrEqual(FLOOR);
        }
    });
});

describe('D-019: deliberate legible author styling is left byte-for-byte (both themes)', () => {
    it('a #333 body on an author-set white callout reads internally -> unchanged in both themes', () => {
        const callout = '<div style="background:#ffffff; color:#333333">notice</div>';
        expect(calculateContrastRatio('#333333', '#ffffff')).toBeGreaterThanOrEqual(FLOOR);
        expect(remediateInlineStyleContrast(callout, false)).toBe(callout);
        expect(remediateInlineStyleContrast(callout, true)).toBe(callout);
    });

    it('declines colours it cannot resolve (var()/currentColor) and content with no style', () => {
        const tok = '<span style="color:var(--muted)">x</span>';
        expect(remediateInlineStyleContrast(tok, false)).toBe(tok);
        expect(remediateInlineStyleContrast(tok, true)).toBe(tok);
        const plain = '<p>plain themed prose</p>';
        expect(remediateInlineStyleContrast(plain, false)).toBe(plain);
    });
});

describe('D-326: white-on-amber HTML status badge is repaired on its own fill (both themes)', () => {
    // The mermaid-label half of this spec (w3-14) was already exemplary; the
    // HTML-badge half left the WARN badge white-on-amber at 1.97:1 because
    // getOptimalTextColor(#f9a825) fell through its luminance>0.5 threshold and
    // returned WHITE (amber luminance 0.483 is just under 0.5), so the D-019
    // remediation "repaired" white -> white. Root cause is in getOptimalTextColor.
    it('getOptimalTextColor(#f9a825) resolves to black (10.66:1), not white (1.97:1)', () => {
        // The failing direction: white on amber is far below the floor.
        expect(calculateContrastRatio('#ffffff', '#f9a825')).toBeLessThan(FLOOR);
        const chosen = getOptimalTextColor('#f9a825');
        expect(chosen.toLowerCase()).toBe('#000000');
        expect(calculateContrastRatio(chosen, '#f9a825')).toBeGreaterThanOrEqual(FLOOR);
    });

    it('w3-14 WARN badge white #ffffff on amber #f9a825 (1.97:1) -> repaired legible, theme-independent', () => {
        // The badge carries its own opaque fill, so the surface it fails on is
        // the amber fill in BOTH themes — repair must fire regardless of theme.
        expect(calculateContrastRatio('#ffffff', '#f9a825')).toBeLessThan(FLOOR);
        for (const dark of [false, true]) {
            const out = remediateInlineStyleContrast(
                '<span style="background:#f9a825;color:#ffffff;padding:2px 8px;border-radius:3px;">WARN</span>',
                dark,
            );
            // Author fill is preserved; only the illegible text colour changes.
            expect(out.toLowerCase()).toContain('#f9a825');
            const repaired = styleVal(out, 'color')!;
            expect(repaired.toLowerCase()).not.toBe('#ffffff');
            expect(calculateContrastRatio(repaired, '#f9a825')).toBeGreaterThanOrEqual(FLOOR);
        }
    });
});

describe('D-019: repair is idempotent', () => {
    it('a second pass over repaired output changes nothing (both themes)', () => {
        for (const dark of [false, true]) {
            const once = remediateInlineStyleContrast('<p style="color:#aaaaaa">m</p>', dark);
            const twice = remediateInlineStyleContrast(once, dark);
            expect(twice).toBe(once);
        }
    });
});
