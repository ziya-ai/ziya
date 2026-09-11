/**
 * chat-message engine — blockquote visual affordance (consolidated-backlog
 * D-011, group G-CHAT-STRUCT).
 *
 * The renderer previously emitted a bare <blockquote> with no styling, so the
 * 3:1 graphical-boundary floor was unmeetable (no boundary drawn) and quoted
 * text was indistinguishable from an indented paragraph in BOTH themes.
 *
 * The fix resolves the border/text colours from the active theme
 * (frontend/src/utils/blockquoteTheme.ts), wired into the MarkdownRenderer
 * blockquote case. This suite asserts BOTH themes:
 *   - the broken theme is now correct (a boundary colour clearing 3:1), AND
 *   - the other theme is still correct (its own boundary clears 3:1 too),
 * and that the two themes resolve to DIFFERENT border colours — proving this
 * is a theme resolution, not a single hardcoded constant that would satisfy
 * one background and fail the other.
 *
 * DIRECTION: the import is of a module that does not exist in unpatched source,
 * so this whole suite fails to load against pre-fix code and passes with it.
 */
import { blockquoteTheme } from '../../../utils/blockquoteTheme';

// sRGB relative luminance + WCAG contrast ratio (same maths the backlog uses).
function luminance(hex: string): number {
    const h = hex.replace('#', '');
    const ch = [0, 2, 4].map((i) => {
        const c = parseInt(h.slice(i, i + 2), 16) / 255;
        return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
}
function contrast(a: string, b: string): number {
    const la = luminance(a);
    const lb = luminance(b);
    const hi = Math.max(la, lb);
    const lo = Math.min(la, lb);
    return (hi + 0.05) / (lo + 0.05);
}

const LIGHT_BG = '#ffffff';
const DARK_BG = '#262626';
const BOUNDARY_FLOOR = 3.0; // graphical boundary
const TEXT_FLOOR = 4.5; // body text

describe('D-011: blockquote boundary is drawn and theme-resolved', () => {
    it('light theme: left rule clears 3:1 and muted text clears 4.5:1 on white', () => {
        const t = blockquoteTheme(false);
        expect(contrast(t.borderColor, LIGHT_BG)).toBeGreaterThanOrEqual(BOUNDARY_FLOOR);
        expect(contrast(t.textColor, LIGHT_BG)).toBeGreaterThanOrEqual(TEXT_FLOOR);
    });

    it('dark theme: left rule clears 3:1 and muted text clears 4.5:1 on the dark surface', () => {
        const t = blockquoteTheme(true);
        expect(contrast(t.borderColor, DARK_BG)).toBeGreaterThanOrEqual(BOUNDARY_FLOOR);
        expect(contrast(t.textColor, DARK_BG)).toBeGreaterThanOrEqual(TEXT_FLOOR);
    });

    it('is a theme resolution, not a constant swap: the two themes differ', () => {
        expect(blockquoteTheme(false).borderColor).not.toBe(blockquoteTheme(true).borderColor);
        expect(blockquoteTheme(false).textColor).not.toBe(blockquoteTheme(true).textColor);
    });

    it('a non-empty boundary colour and fill are always produced (affordance exists)', () => {
        for (const dark of [false, true]) {
            const t = blockquoteTheme(dark);
            expect(t.borderColor).toMatch(/^#[0-9a-f]{6}$/i);
            expect(t.background.length).toBeGreaterThan(0);
        }
    });
});
