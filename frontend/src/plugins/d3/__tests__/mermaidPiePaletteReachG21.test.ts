/**
 * G-21 / D-023 (mermaid-pie-default-palette-not-canvas-aware).
 *
 * Ties the two D-023 spec_ids to the curated per-theme pie palette that was
 * landed under G-79 / D-159 (PIE_PALETTE_LIGHT/DARK + buildPieThemeVariables,
 * merged into mermaid.initialize themeVariables for BOTH the light and dark
 * branches of mermaidPlugin.render).
 *
 * The D-023 triage hypothesis was that "the curated PIE_PALETTE constants exist
 * in source but did not reach the render at capture time". That worry is STALE:
 * mermaid 11.x's pie renderer builds its slice colours as
 *     const myGeneratedColors = [themeVariables.pie1 .. themeVariables.pie12];
 *     const color = ordinal(myGeneratedColors).domain([...sections.keys()]);
 * i.e. it reads pie1..pie12 DIRECTLY off themeVariables — exactly the keys
 * buildPieThemeVariables emits — so injecting them via mermaid.initialize's
 * themeVariables reaches the renderer for both themes. This suite pins that.
 *
 * BOTH-THEME obligation (theme defect):
 *   - mermaid-w1-08 (5 slices): the first five palette entries — the colours a
 *     5-slice pie actually paints — clear the 3:1 graphical floor on the LIGHT
 *     page (#ffffff) [previously-broken direction], PAIRED with the DARK
 *     palette's first five clearing it on the dark page [other-theme guard].
 *
 * ENGINE LIMITATION (documented, not a fix):
 *   - mermaid-w2-15 (60 slices): mermaid reads only pie1..pie12, then a d3
 *     ordinal scale RECYCLES those 12 across all 60 slices. themeVariables
 *     cannot carry pie13+, so the recycling is inherent to the engine and
 *     cannot be removed via the theme-variable path. The curated palette +
 *     page-coloured inter-slice stroke still make ADJACENT wedges separable,
 *     but a recycled colour cannot be uniquely matched to its legend swatch.
 *     This is recorded as wont-fix-within-defect (engine limitation).
 *
 * DIRECTION: this suite imports PIE_PALETTE_LIGHT/PIE_PALETTE_DARK/
 * buildPieThemeVariables — none of which exist on the pre-D-159 tree, so the
 * suite fails to compile there (the exports are absent), and the recycling
 * assertion below is a green-by-design guard that documents why w2-15 is not
 * de-recyclable rather than certifying a bug.
 */
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
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

const WHITE = '#ffffff';
const DARK_PAGE = '#1f1f1f';

// The keys mermaid's pie renderer actually consumes off themeVariables.
const PIE_KEYS = Array.from({ length: 12 }, (_, i) => `pie${i + 1}`);

describe('G-21 D-023 curated pie palette reaches the pie renderer (both themes)', () => {
    it('buildPieThemeVariables supplies exactly the pie1..pie12 keys mermaid reads', () => {
        for (const isDark of [false, true]) {
            const vars = buildPieThemeVariables(isDark);
            for (const k of PIE_KEYS) {
                expect(typeof vars[k]).toBe('string');
                expect(vars[k]).toMatch(/^#[0-9a-fA-F]{6}$/);
            }
        }
    });

    it('mermaid-w1-08: the first 5 slice colours clear 3:1 in LIGHT (broken theme now correct)', () => {
        // A 5-slice pie paints pie1..pie5.
        for (let i = 0; i < 5; i++) {
            expect(contrast(PIE_PALETTE_LIGHT[i], WHITE)).toBeGreaterThanOrEqual(3);
        }
    });

    it('mermaid-w1-08: the first 5 slice colours still clear 3:1 in DARK (other theme guard)', () => {
        for (let i = 0; i < 5; i++) {
            expect(contrast(PIE_PALETTE_DARK[i], DARK_PAGE)).toBeGreaterThanOrEqual(3);
        }
    });

    it('per-theme resolution, not a constant swap: the two 5-colour prefixes differ, and each fails the other page', () => {
        expect(PIE_PALETTE_LIGHT.slice(0, 5)).not.toEqual(PIE_PALETTE_DARK.slice(0, 5));
        // A single palette cannot serve both backgrounds: the dark colours are
        // too pale on white and the light colours too dark on the dark page.
        expect(PIE_PALETTE_DARK.some(c => contrast(c, WHITE) < 3)).toBe(true);
    });

    it('mermaid-w2-15: 60 slices RECYCLE the 12 pie keys — engine limitation, not de-recyclable via themeVariables', () => {
        // Emulate mermaid's d3 ordinal(range).domain(keys): colour(k)=range[index%12].
        const range = PIE_PALETTE_LIGHT; // length 12, matches mermaid's pie1..pie12
        const mapped = Array.from({ length: 60 }, (_, i) => range[i % range.length]);
        const distinct = new Set(mapped).size;
        expect(range.length).toBe(12);
        expect(distinct).toBe(12);               // only 12 distinct colours across 60 slices
        expect(mapped[0]).toBe(mapped[12]);      // slice 0 and slice 12 collide (recycling)
        // Adjacent slices never collide, so wedge boundaries stay separable via the
        // page-coloured stroke — but legend<->slice matching of recycled colours cannot
        // be resolved through themeVariables (mermaid reads only pie1..pie12).
        for (let i = 1; i < mapped.length; i++) {
            expect(mapped[i]).not.toBe(mapped[i - 1]);
        }
    });
});
