/**
 * G-JOINT-THEME / D-116 — bogus-theme-token-overrides-render-theme:dark
 *
 * A definition-supplied theme token is lifted into spec.theme, but the render
 * theme that drives every `theme === 'dark' ? ... : ...` ternary (and the paper
 * background `theme === 'dark' ? '#1f1f1f' : '#ffffff'`) must only ever be the
 * literal 'light' or 'dark'. joint-w4-14 supplies 'nord-dark': not === 'dark',
 * so pre-fix every ternary fell to its LIGHT branch — a near-white #ffffff paper
 * slab rendered inside a genuinely dark page (~#212121 / #1a1a2e), a glaring
 * light island with a hard seam. In light the identical input was accidentally
 * correct.
 *
 * resolveJointRenderTheme() resolves a bogus/absent/'auto' token to the caller's
 * render theme (isDarkMode) instead. This test asserts BOTH themes:
 *   - the broken theme (dark) is now correct: 'nord-dark' -> 'dark' -> dark paper,
 *     seamless with the dark page rather than a 16:1 white slab;
 *   - the other theme (light) still resolves to 'light' -> white paper.
 * Direction guard: the pre-fix behaviour (bogus token passed through verbatim)
 * would have kept 'nord-dark', which is neither 'light' nor 'dark'.
 */
import { resolveJointRenderTheme, isValidJointTheme } from '../jointPlugin';

// Paper background exactly as the render site sets it:
//   background: { color: theme === 'dark' ? '#1f1f1f' : '#ffffff' }
const paperBg = (theme: 'light' | 'dark') => (theme === 'dark' ? '#1f1f1f' : '#ffffff');

// sRGB relative-luminance contrast ratio (WCAG).
const lin = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
};
const L = (hex: string) => {
    const h = hex.replace('#', '');
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
};
const cr = (a: string, b: string) => {
    const la = L(a);
    const lb = L(b);
    const hi = Math.max(la, lb);
    const lo = Math.min(la, lb);
    return (hi + 0.05) / (lo + 0.05);
};

const DARK_PAGE = '#212121'; // host container under dark mode

describe('D-116 — a bogus definition theme token never outranks the render theme', () => {
    it('the offending token is rejected by the theme validator', () => {
        expect(isValidJointTheme('nord-dark')).toBe(false);
        expect(isValidJointTheme('light')).toBe(true);
        expect(isValidJointTheme('dark')).toBe(true);
    });

    it('DARK (broken theme now correct): bogus token resolves to dark, paper is seamless with the dark page', () => {
        const theme = resolveJointRenderTheme('nord-dark', /* isDarkMode */ true);
        expect(theme).toBe('dark');
        const bg = paperBg(theme);
        expect(bg).toBe('#1f1f1f');
        // Seamless with the dark host page (no light slab): ratio ~ 1.
        expect(cr(bg, DARK_PAGE)).toBeLessThan(1.5);
        // Pre-fix the bogus token fell through, paper stayed white -> a glaring slab.
        expect(cr('#ffffff', DARK_PAGE)).toBeGreaterThan(10);
    });

    it('LIGHT (other theme still correct): same bogus token resolves to light, paper stays white', () => {
        const theme = resolveJointRenderTheme('nord-dark', /* isDarkMode */ false);
        expect(theme).toBe('light');
        expect(paperBg(theme)).toBe('#ffffff');
    });

    it('a real explicit token is honoured in both directions and outranks the render theme', () => {
        // Author asked for dark while the app is in light mode -> dark honoured.
        expect(resolveJointRenderTheme('dark', false)).toBe('dark');
        // Author asked for light while the app is in dark mode -> light honoured.
        expect(resolveJointRenderTheme('light', true)).toBe('light');
    });

    it("'auto' and undefined fall back to the caller render theme in both directions", () => {
        expect(resolveJointRenderTheme('auto', true)).toBe('dark');
        expect(resolveJointRenderTheme('auto', false)).toBe('light');
        expect(resolveJointRenderTheme(undefined, true)).toBe('dark');
        expect(resolveJointRenderTheme(undefined, false)).toBe('light');
    });
});
