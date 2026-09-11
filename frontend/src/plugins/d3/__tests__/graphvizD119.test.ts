import {
    planGraphvizViewport,
    readGraphvizMinFontPx,
    GRAPHVIZ_MIN_FONT_SCALE,
    GRAPHVIZ_MIN_LEGIBLE_FONT_PX,
} from '../graphvizPlugin';

/**
 * D-119 (gfx-sweep G-ff1987): node-label-subpixel-at-high-density.
 *
 * graphviz-w2-14 is a 120-node `circo` ring authored with `fontsize=8`
 * (~10.7px). The ring is wider than the bounded capture window, so the plugin's
 * viewport planner shrinks/scrolls it. The shrink floor was a FIXED scale of
 * 0.5, which presumes the plugin's ~16px injected base font (0.5 * 16 = 8px =
 * the legibility floor). For an authored 10.7px font, clamping at 0.5 leaves
 * labels at ~5.3px — well below 8px — so they antialias to sub-pixel mush
 * (worst in dark: white glyphs on a mid-tone fill vanish entirely).
 *
 * The fix makes the floor font-aware: when the natural min font size is known,
 * the shrink scale is clamped so the SMALLEST label stays >= 8px. This is
 * theme-independent (the planner takes no theme input), so the same plan holds
 * in light and dark — but the underlying defect manifested in BOTH themes and
 * the fix must therefore hold for both, asserted explicitly below.
 */

// A 120-node circo ring: naturally far wider than the container, authored font
// ~10.7px (graphviz `fontsize=8` * 96/72).
const RING_NATURAL_W = 4000;
const RING_NATURAL_H = 4000;
const CONTAINER_W = 1280;
const AUTHORED_FONT_PX = 8 * (96 / 72); // ~10.67

describe('readGraphvizMinFontPx', () => {
    it('returns the smallest positive font size among text attributes', () => {
        expect(readGraphvizMinFontPx(['14', '10.67', '12'])).toBeCloseTo(10.67, 5);
    });
    it('parses px-suffixed computed styles', () => {
        expect(readGraphvizMinFontPx(['16px', '8px', '13px'])).toBe(8);
    });
    it('ignores empty / unparseable / non-positive entries, 0 when none valid', () => {
        expect(readGraphvizMinFontPx([null, undefined, '', 'auto', '0'])).toBe(0);
        expect(readGraphvizMinFontPx(['none', '9'])).toBe(9);
    });
});

describe('planGraphvizViewport — small-authored-font dense ring (D-119)', () => {
    it('OLD fixed floor leaves the label below the 8px legibility floor', () => {
        // Reproduce the pre-fix behaviour: no font awareness -> clamp at 0.5.
        const plan = planGraphvizViewport(RING_NATURAL_W, RING_NATURAL_H, CONTAINER_W);
        expect(plan.mode).toBe('scroll');
        expect(plan.effectiveScale).toBeCloseTo(GRAPHVIZ_MIN_FONT_SCALE, 5);
        // This is the bug: the smallest label ends up sub-8px.
        expect(plan.effectiveScale * AUTHORED_FONT_PX).toBeLessThan(
            GRAPHVIZ_MIN_LEGIBLE_FONT_PX
        );
    });

    it('font-aware floor keeps the smallest label >= 8px (both themes)', () => {
        const plan = planGraphvizViewport(RING_NATURAL_W, RING_NATURAL_H, CONTAINER_W, {
            naturalMinFontPx: AUTHORED_FONT_PX,
        });
        // Still too wide to fit at a legible scale -> scroll rather than dissolve.
        expect(plan.mode).toBe('scroll');
        // The raised floor: 8 / 10.67 ~= 0.75, strictly above the old 0.5.
        expect(plan.effectiveScale).toBeGreaterThan(GRAPHVIZ_MIN_FONT_SCALE);
        // The whole point: the smallest label is now legible.
        expect(plan.effectiveScale * AUTHORED_FONT_PX).toBeGreaterThanOrEqual(
            GRAPHVIZ_MIN_LEGIBLE_FONT_PX - 1e-9
        );
        // Planner is theme-independent, so this identical plan protects both
        // light and dark (D-119 failed in both).
        const planAgain = planGraphvizViewport(
            RING_NATURAL_W,
            RING_NATURAL_H,
            CONTAINER_W,
            { naturalMinFontPx: AUTHORED_FONT_PX }
        );
        expect(planAgain).toEqual(plan);
    });

    it('does not LOWER the floor for a large-authored-font graph (no regression)', () => {
        // A 24px font: 8/24 = 0.33 < 0.5, so the safe default 0.5 still governs.
        const plan = planGraphvizViewport(RING_NATURAL_W, RING_NATURAL_H, CONTAINER_W, {
            naturalMinFontPx: 24,
        });
        expect(plan.effectiveScale).toBeCloseTo(GRAPHVIZ_MIN_FONT_SCALE, 5);
    });

    it('never shrinks a graph whose font is already at/under the floor', () => {
        // 8px authored -> floor scale 8/8 = 1 -> scroll at natural size.
        const plan = planGraphvizViewport(RING_NATURAL_W, RING_NATURAL_H, CONTAINER_W, {
            naturalMinFontPx: 8,
        });
        expect(plan.effectiveScale).toBe(1);
    });

    it('leaves a comfortably-sized small-font graph untouched (byte-equivalent)', () => {
        // Fits within the container: no shrink regime, so the font floor never
        // fires and the render is identical to before.
        const plan = planGraphvizViewport(1000, 800, CONTAINER_W, {
            naturalMinFontPx: AUTHORED_FONT_PX,
        });
        expect(plan.mode).toBe('natural');
        expect(plan.effectiveScale).toBe(1);
    });
});
