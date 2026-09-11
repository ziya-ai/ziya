/**
 * G-47 / D-052 — KaTeX error colour must resolve from the active theme.
 *
 * KaTeX paints an unresolvable token with the `errorColor` render option.
 * The shared render options baked ONE colour, #cc0000, which is legible on the
 * light chat surface (5.89:1 on #ffffff) but only ~2.8:1 on the app's dark chat
 * surfaces (#1f1f1f / #141414) — below the WCAG 3:1 graphical floor, so a
 * failed math token is effectively invisible in dark mode.
 *
 * There is NO single red that clears the 4.5:1 text floor on BOTH a white and a
 * near-black background (they demand opposite lightness), so the correct fix is
 * to RESOLVE the colour from the theme rather than swap one constant for
 * another.  This suite therefore asserts BOTH themes:
 *   - DARK (previously broken) is now legible, AND
 *   - LIGHT (previously correct) still is.
 *
 * This FAILS on the unpatched tree: `katexRenderOptions` and
 * `KATEX_ERROR_COLOR_DARK` do not exist there, so the import is `undefined` and
 * the assertions throw.  A test written only against the pre-fix single
 * constant would pass on unpatched code and certify the bug instead of the fix.
 */
import {
    KATEX_ERROR_COLOR,
    // eslint-disable-next-line @typescript-eslint/no-var-requires
} from '../mathSanitizer';

// Import the (post-fix) symbols via require so the module still loads and the
// assertions — not the import — are what fail on the unpatched tree.
// eslint-disable-next-line @typescript-eslint/no-var-requires
const sanitizer = require('../mathSanitizer');
const katexRenderOptions: (isDark?: boolean) => { errorColor: string; throwOnError: boolean; strict: boolean; macros: Record<string, string> } =
    sanitizer.katexRenderOptions;
const KATEX_ERROR_COLOR_DARK: string = sanitizer.KATEX_ERROR_COLOR_DARK;

// --- WCAG 2.x relative-luminance contrast, computed here (no source dep) -----
function srgbToLinear(c: number): number {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}
function luminance(hex: string): number {
    const h = hex.replace('#', '');
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    return 0.2126 * srgbToLinear(r) + 0.7152 * srgbToLinear(g) + 0.0722 * srgbToLinear(b);
}
function contrast(a: string, b: string): number {
    const la = luminance(a);
    const lb = luminance(b);
    const hi = Math.max(la, lb);
    const lo = Math.min(la, lb);
    return (hi + 0.05) / (lo + 0.05);
}

const LIGHT_BG = '#ffffff';
const DARK_BG_BUBBLE = '#1f1f1f';
const DARK_BG_PAGE = '#141414';

describe('D-052 KaTeX error colour resolves from theme (both themes)', () => {
    it('exposes a theme-resolving factory and a dark error-colour constant (absent pre-fix)', () => {
        expect(typeof katexRenderOptions).toBe('function');
        expect(typeof KATEX_ERROR_COLOR_DARK).toBe('string');
        expect(KATEX_ERROR_COLOR_DARK).toMatch(/^#[0-9a-f]{6}$/i);
    });

    it('DARK (previously broken): dark errorColor clears 4.5:1 on both dark surfaces', () => {
        const dark = katexRenderOptions(true).errorColor;
        expect(dark).toBe(KATEX_ERROR_COLOR_DARK);
        expect(contrast(dark, DARK_BG_BUBBLE)).toBeGreaterThanOrEqual(4.5);
        expect(contrast(dark, DARK_BG_PAGE)).toBeGreaterThanOrEqual(4.5);
    });

    it('LIGHT (still correct): light errorColor is #cc0000 and clears 4.5:1 on white', () => {
        const light = katexRenderOptions(false).errorColor;
        expect(light).toBe(KATEX_ERROR_COLOR);
        expect(light.toLowerCase()).toBe('#cc0000');
        expect(contrast(light, LIGHT_BG)).toBeGreaterThanOrEqual(4.5);
    });

    it('DIRECTION GUARD: the pre-fix single constant was itself illegible on dark (< 3:1 floor)', () => {
        // This is why a single-constant swap is not a valid fix and the colour
        // must be theme-resolved: the old value fails even the graphical floor
        // on dark, and no single red satisfies 4.5:1 on both surfaces.
        expect(contrast(KATEX_ERROR_COLOR, DARK_BG_BUBBLE)).toBeLessThan(3.0);
        // ...while the new dark value would itself be too weak on white,
        // confirming the two backgrounds have opposite requirements.
        expect(contrast(KATEX_ERROR_COLOR_DARK, LIGHT_BG)).toBeLessThan(4.5);
    });

    it('REGRESSION: default (no arg) keeps the light configuration and all other options intact', () => {
        const def = katexRenderOptions();
        expect(def.errorColor).toBe(KATEX_ERROR_COLOR);
        expect(def.throwOnError).toBe(false);
        expect(def.strict).toBe(false);
        expect(def.macros).toEqual({ '\\f': '#1f(#2)' });
    });
});
