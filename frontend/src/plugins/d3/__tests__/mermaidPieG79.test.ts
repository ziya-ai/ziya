/**
 * G-79 / D-159 (pie-palette-slices-and-swatches-indistinguishable).
 *
 * Mermaid's default pie palette fails the 3:1 graphical-object floor in BOTH
 * themes: the light "default" theme recycles near-white ivory (#ffffe0,
 * 1.02:1 on white) and pure yellow (#ffff00, 1.07:1); the "dark" theme
 * collapses to near-black/maroon variants (1.19-1.31:1 on the dark page), and
 * adjacent wedges are two near-identical lavenders so a slice cannot be matched
 * to its legend swatch. The plugin previously set NO pie* theme variables
 * (light branch `{}`, dark branch base colours only), so mermaid's own broken
 * defaults were used.
 *
 * buildPieThemeVariables(isDarkMode) now resolves a curated categorical palette
 * FROM the given theme (light-tuned on the light page, dark-tuned on the dark
 * page — not a single constant swap) and pins pieOpacity:1 so the verified
 * opaque ratios hold.
 *
 * BOTH-THEME obligation:
 *   - "broken theme now correct": every LIGHT palette entry clears 3:1 on
 *     #ffffff, and every DARK palette entry clears 3:1 on the dark page.
 *   - "other theme still correct": the same assertion is made for the opposite
 *     theme's palette on its own background, and we assert the two palettes are
 *     genuinely different (per-theme resolution, not a constant swap).
 *
 * DIRECTION: the "now correct" assertions are paired with a demonstration that
 * mermaid's OLD default pie colours (#ffffe0/#ffff00 in light) are genuinely
 * below floor, so the palette is doing work; and a source-wiring assertion that
 * buildPieThemeVariables is merged into the plugin's themeVariables for BOTH
 * branches — absent the fix, the export and the wiring do not exist and the
 * suite fails to compile / match.
 */
import * as fs from 'fs';
import * as path from 'path';
import {
    PIE_PALETTE_LIGHT,
    PIE_PALETTE_DARK,
    buildPieThemeVariables,
} from '../mermaidPlugin';

// WCAG relative luminance + contrast ratio (same formula the sweep uses).
function srgbToLin(c: number): number {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}
function luminance(hex: string): number {
    const h = hex.replace('#', '');
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    return 0.2126 * srgbToLin(r) + 0.7152 * srgbToLin(g) + 0.0722 * srgbToLin(b);
}
function contrast(a: string, b: string): number {
    const la = luminance(a);
    const lb = luminance(b);
    const hi = Math.max(la, lb);
    const lo = Math.min(la, lb);
    return (hi + 0.05) / (lo + 0.05);
}

const WHITE = '#ffffff';
const DARK_BGS = ['#1f1f1f', '#262626'];

describe('G-79 D-159 pie palette contrast', () => {
    it('every LIGHT palette entry clears 3:1 on the light page (#ffffff)', () => {
        expect(PIE_PALETTE_LIGHT).toHaveLength(12);
        for (const c of PIE_PALETTE_LIGHT) {
            expect(contrast(c, WHITE)).toBeGreaterThanOrEqual(3);
        }
    });

    it('every DARK palette entry clears 3:1 on the dark page (both surfaces)', () => {
        expect(PIE_PALETTE_DARK).toHaveLength(12);
        for (const c of PIE_PALETTE_DARK) {
            for (const bg of DARK_BGS) {
                expect(contrast(c, bg)).toBeGreaterThanOrEqual(3);
            }
        }
    });

    it('DIRECTION: mermaid old default pie colours were below floor on white', () => {
        // The values the "default" theme actually shipped for pie slices.
        expect(contrast('#ffffe0', WHITE)).toBeLessThan(3); // ivory ~1.02:1
        expect(contrast('#ffff00', WHITE)).toBeLessThan(3); // pure yellow ~1.07:1
    });

    it('is per-theme resolution, not a constant swap (palettes differ)', () => {
        expect(PIE_PALETTE_LIGHT).not.toEqual(PIE_PALETTE_DARK);
        // and the light palette would be too weak if reused on light? no —
        // the point is the DARK palette (light colours) fails on white, proving
        // a single palette cannot serve both backgrounds.
        const darkOnWhiteFails = PIE_PALETTE_DARK.some(c => contrast(c, WHITE) < 3);
        expect(darkOnWhiteFails).toBe(true);
    });

    it('buildPieThemeVariables emits pie1..pie12 opaque, per theme', () => {
        for (const isDark of [false, true]) {
            const vars = buildPieThemeVariables(isDark);
            const palette = isDark ? PIE_PALETTE_DARK : PIE_PALETTE_LIGHT;
            for (let i = 0; i < 12; i++) {
                expect(vars[`pie${i + 1}`]).toBe(palette[i]);
            }
            // Opaque fills so the verified contrast ratios actually hold.
            expect(vars.pieOpacity).toBe('1');
            // Inter-slice gap is the page colour so neighbours are separable.
            expect(vars.pieStrokeColor).toBe(isDark ? '#1f1f1f' : '#ffffff');
        }
    });

    it('WIRING: buildPieThemeVariables is merged for BOTH theme branches', () => {
        const source = fs.readFileSync(
            path.join(__dirname, '..', 'mermaidPlugin.ts'), 'utf-8');
        // dark branch merges the dark palette, light branch supplies the light one.
        expect(source).toMatch(/buildPieThemeVariables\(true\)\) : buildPieThemeVariables\(false\)/);
    });
});
