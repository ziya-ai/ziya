import {
    planGraphvizViewport,
    readGraphvizNaturalSizePx,
    GRAPHVIZ_PT_TO_PX,
    GRAPHVIZ_MIN_FONT_SCALE,
    GRAPHVIZ_UPSCALE_MIN_FITSCALE,
    GRAPHVIZ_MAX_UPSCALE,
} from '../graphvizPlugin';

/**
 * G-56 regression tests: viewport fit for the graphviz renderer.
 *
 * Root cause (D-129 / D-130 / D-135): Viz.js emits an SVG sized in absolute
 * points with no responsive behaviour, and the plugin mounted it as-is. A graph
 * larger than the bounded capture window was CROPPED; a small / `size="1.5,1.5!"`
 * -forced graph drew as a sub-pixel island with NO upscale (D-129 + its mirror);
 * where it was shrunk to fit there was no minimum legible-font floor so labels
 * dissolved (D-130 / D-135).
 *
 * `planGraphvizViewport` is the single shared lever. These exports did not exist
 * before the fix, so this file cannot even import against the pre-fix module —
 * the tests fail against unpatched code by construction. Each assertion also
 * pins the DIRECTION: the old behaviour was "always natural size" (scale === 1,
 * never upscale, never scroll), so an assertion that the plan now upscales /
 * clamps / scrolls is false against that behaviour.
 *
 * These defects are STRUCTURAL and theme-independent: the planner takes no
 * theme input, so its output is identical in light and dark by construction
 * (asserted explicitly in the theme-independence case below).
 */

describe('planGraphvizViewport — small / size!-forced graph (D-129 mirror)', () => {
    // graphviz-w2-09: size="1.5,1.5!" squeezes an 80-node graph into ~1.5in
    // (~144px). Natural 144px in a 1280px canvas => an unreadable island with a
    // sea of blank canvas because the old renderer never upscaled.
    it('UPSCALES a tiny graph to fill instead of leaving a sub-pixel island', () => {
        const plan = planGraphvizViewport(144, 150, 1280);
        expect(plan.mode).toBe('upscale');
        // Old behaviour: scale === 1 (natural, no upscale). New: > 1.
        expect(plan.effectiveScale).toBeGreaterThan(1);
        // Blow-up is capped so a 2-node stub cannot become grotesque.
        expect(plan.effectiveScale).toBeLessThanOrEqual(GRAPHVIZ_MAX_UPSCALE);
        expect(plan.svgWidthPx).toBeCloseTo(144 * GRAPHVIZ_MAX_UPSCALE, 5);
        expect(plan.scroll).toBe(false);
    });

    it('caps the upscale at GRAPHVIZ_MAX_UPSCALE for an extremely tiny graph', () => {
        const plan = planGraphvizViewport(50, 50, 1280); // fitScale 25.6
        expect(plan.mode).toBe('upscale');
        expect(plan.effectiveScale).toBe(GRAPHVIZ_MAX_UPSCALE);
    });
});

describe('planGraphvizViewport — wide graph within the font floor (D-129 crop)', () => {
    it('shrinks a moderately-wide graph to fit (labels stay above the floor)', () => {
        const plan = planGraphvizViewport(2000, 1200, 1280); // fitScale 0.64
        expect(plan.mode).toBe('fit');
        expect(plan.svgWidthPx).toBe(1280);
        expect(plan.effectiveScale).toBeCloseTo(0.64, 5);
        expect(plan.scroll).toBe(false);
        // Above the min-font floor -> not clamped.
        expect(plan.effectiveScale).toBeGreaterThanOrEqual(GRAPHVIZ_MIN_FONT_SCALE);
    });
});

describe('planGraphvizViewport — over-wide graph past the font floor (D-130 / D-135)', () => {
    // A 150-node chain / 120-node dense ring at natural size is far wider than
    // the container; shrinking to fit would push the ~16px label under 8px.
    it('clamps the downscale at the min-font floor and SCROLLS instead of dissolving labels', () => {
        const plan = planGraphvizViewport(5000, 4000, 1280); // fitScale 0.256
        expect(plan.mode).toBe('scroll');
        // Old behaviour: would have shrunk to fitScale 0.256 (illegible) OR
        // cropped. New: clamp at the floor, no lower.
        expect(plan.effectiveScale).toBe(GRAPHVIZ_MIN_FONT_SCALE);
        expect(plan.effectiveScale).toBeGreaterThan(0.256); // NOT the raw fit scale
        expect(plan.svgWidthPx).toBeCloseTo(5000 * GRAPHVIZ_MIN_FONT_SCALE, 5);
        expect(plan.scroll).toBe(true);
    });

    it('never lets the effective scale fall below the legible-font floor', () => {
        for (const nat of [1300, 2000, 3000, 8000, 20000]) {
            const plan = planGraphvizViewport(nat, nat, 1280);
            expect(plan.effectiveScale).toBeGreaterThanOrEqual(GRAPHVIZ_MIN_FONT_SCALE);
        }
    });
});

describe('planGraphvizViewport — comfortable middle range is untouched', () => {
    it('leaves a graph between ~2/3 and full container width at NATURAL size (no unrelated change)', () => {
        // fitScale 1.28 (< upscale trigger 1.5, and >= 1): comfortable.
        const plan = planGraphvizViewport(1000, 800, 1280);
        expect(plan.mode).toBe('natural');
        expect(plan.effectiveScale).toBe(1);
        expect(plan.svgWidthPx).toBe(1000);
        expect(plan.scroll).toBe(false);
    });

    it('leaves a graph exactly at the upscale threshold as natural just below it', () => {
        // fitScale just under 1.5 -> natural; at/over 1.5 -> upscale.
        const belowW = 1280 / (GRAPHVIZ_UPSCALE_MIN_FITSCALE - 0.01);
        expect(planGraphvizViewport(belowW, belowW, 1280).mode).toBe('natural');
        const atW = 1280 / GRAPHVIZ_UPSCALE_MIN_FITSCALE;
        expect(planGraphvizViewport(atW, atW, 1280).mode).toBe('upscale');
    });
});

describe('planGraphvizViewport — defensive & theme independence', () => {
    it('returns natural (no-op) for unusable measurements', () => {
        expect(planGraphvizViewport(0, 0, 1280).mode).toBe('natural');
        expect(planGraphvizViewport(-5, 100, 1280).mode).toBe('natural');
        expect(planGraphvizViewport(100, 100, 0).mode).toBe('natural');
        expect(planGraphvizViewport(NaN, 100, 1280).mode).toBe('natural');
    });

    it('is deterministic — same size in "light" and "dark" gives an identical plan', () => {
        // Structural defect: the planner has no theme input, so the same natural
        // size must yield byte-identical output regardless of the render theme.
        const a = planGraphvizViewport(144, 150, 1280); // e.g. dark render
        const b = planGraphvizViewport(144, 150, 1280); // e.g. light render
        expect(a).toEqual(b);
    });

    it('honours custom thresholds', () => {
        // Raise the floor so a mild shrink now trips the scroll clamp.
        const plan = planGraphvizViewport(2000, 1000, 1280, { minFontScale: 0.8 });
        expect(plan.mode).toBe('scroll');
        expect(plan.effectiveScale).toBe(0.8);
    });
});

describe('readGraphvizNaturalSizePx', () => {
    const attrs = (m: Record<string, string>) => (n: string) => (n in m ? m[n] : null);

    it('reads pt width/height attributes into CSS px', () => {
        const { w, h } = readGraphvizNaturalSizePx(attrs({ width: '148pt', height: '200pt' }));
        expect(w).toBeCloseTo(148 * GRAPHVIZ_PT_TO_PX, 5);
        expect(h).toBeCloseTo(200 * GRAPHVIZ_PT_TO_PX, 5);
    });

    it('falls back to the viewBox when width/height are absent', () => {
        const { w, h } = readGraphvizNaturalSizePx(attrs({ viewBox: '0.00 0.00 148.00 200.00' }));
        expect(w).toBeCloseTo(148 * GRAPHVIZ_PT_TO_PX, 5);
        expect(h).toBeCloseTo(200 * GRAPHVIZ_PT_TO_PX, 5);
    });

    it('returns {0,0} when nothing parseable is present (caller leaves the SVG alone)', () => {
        expect(readGraphvizNaturalSizePx(attrs({}))).toEqual({ w: 0, h: 0 });
        expect(readGraphvizNaturalSizePx(attrs({ width: 'auto' }))).toEqual({ w: 0, h: 0 });
    });
});
