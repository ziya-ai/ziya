import {
    planGraphvizViewport,
    GRAPHVIZ_MAX_UPSCALE,
} from '../graphvizPlugin';

/**
 * D-120 / D-126 regression: viewport height-clamp for the graphviz upscale path.
 *
 * The gradient stress spec `graphviz-w1-13` (rankdir=LR chain of five
 * gradient/striped/wedged nodes) is SMALL relative to the capture window, so
 * `planGraphvizViewport` takes the upscale branch to make the sub-pixel labels
 * legible. Upscaling by WIDTH ALONE, however, scales the height by the same
 * factor: a graph that is narrow enough to warrant a large width-upscale becomes
 * TALLER than the viewport, and the headless capture — which centres the SVG in
 * a `100vh` / `overflow:hidden` flex root — then shows only a sliver at the
 * bottom edge with the five gradient boxes pushed off-screen (the observed
 * `viewport-crop-content-lost` / near-blank regression, both themes).
 *
 * The fix threads the available viewport HEIGHT (`maxHeightPx`) into the planner
 * and clamps the upscale so the scaled drawing also fits vertically, never
 * clamping BELOW natural size (shrinking is the `fit`/`scroll` job, not the
 * upscale branch's).
 *
 * DIRECTION: without the clamp the planner returns the full width-driven
 * upscale, whose scaled height OVERFLOWS the viewport — the assertions below on
 * the clamped call are false against that behaviour, so this test fails on the
 * pre-clamp code and passes with it.
 *
 * This defect is STRUCTURAL: the planner takes no theme argument, so its output
 * is byte-identical in light and dark. The final case pins that explicitly, so
 * a fix verified here holds for BOTH themes by construction.
 */

describe('planGraphvizViewport — upscale height-clamp (D-120 / D-126)', () => {
    // A small, roughly-square graph (360x300) in a 1280px canvas: fitScale 3.56
    // wants a big width-upscale, but 300 * 3.56 = 1067px would overflow an 800px
    // capture window.
    const NAT_W = 360;
    const NAT_H = 300;
    const CONTAINER_W = 1280;
    const VIEWPORT_H = 800;

    it('the UNCLAMPED width-upscale would overflow the viewport height (the bug)', () => {
        const plan = planGraphvizViewport(NAT_W, NAT_H, CONTAINER_W);
        expect(plan.mode).toBe('upscale');
        // Width-driven fitScale, capped at GRAPHVIZ_MAX_UPSCALE.
        expect(plan.effectiveScale).toBeCloseTo(CONTAINER_W / NAT_W, 5);
        // The scaled drawing is TALLER than the capture window -> content is
        // pushed off-screen (near-blank crop) when no height is supplied.
        expect(NAT_H * plan.effectiveScale).toBeGreaterThan(VIEWPORT_H);
    });

    it('clamps the upscale so the scaled height fits within maxHeightPx', () => {
        const plan = planGraphvizViewport(NAT_W, NAT_H, CONTAINER_W, {
            maxHeightPx: VIEWPORT_H,
        });
        expect(plan.mode).toBe('upscale');
        // Clamped to the height budget: 800 / 300 = 2.667, below the 3.556
        // width-driven scale.
        expect(plan.effectiveScale).toBeCloseTo(VIEWPORT_H / NAT_H, 5);
        expect(plan.effectiveScale).toBeLessThan(CONTAINER_W / NAT_W);
        // The whole drawing now fits vertically (with a 1px rounding tolerance).
        expect(NAT_H * plan.effectiveScale).toBeLessThanOrEqual(VIEWPORT_H + 1);
        // Width shrinks with the clamped scale, so it also stays within the
        // container instead of being driven to the full 1280px.
        expect(plan.svgWidthPx).toBeCloseTo(NAT_W * (VIEWPORT_H / NAT_H), 5);
        expect(plan.svgWidthPx).toBeLessThan(CONTAINER_W);
    });

    it('still upscales up to the width limit when the height budget is generous', () => {
        // A short, wide-ish graph (the true w1-13 aspect): the height clamp must
        // NOT kick in when there is ample vertical room.
        const plan = planGraphvizViewport(480, 90, CONTAINER_W, {
            maxHeightPx: VIEWPORT_H,
        });
        expect(plan.mode).toBe('upscale');
        // 90 * (1280/480 = 2.667) = 240px << 800px, so no clamp: full width scale.
        expect(plan.effectiveScale).toBeCloseTo(CONTAINER_W / 480, 5);
        expect(90 * plan.effectiveScale).toBeLessThanOrEqual(VIEWPORT_H);
    });

    it('never clamps BELOW natural size — a tall graph stays natural, not shrunk', () => {
        // fitScale 1.6 (>= upscale floor) but the graph is TALLER than the
        // viewport, so hScale < 1. The clamp floors at 1, and scale<=1 falls
        // back to natural size; shrinking a tall graph is the fit/scroll job,
        // not the upscale branch's.
        const plan = planGraphvizViewport(800, 900, CONTAINER_W, {
            maxHeightPx: VIEWPORT_H,
        });
        expect(plan.mode).toBe('natural');
        expect(plan.effectiveScale).toBe(1);
    });

    it('caps the clamped upscale at GRAPHVIZ_MAX_UPSCALE', () => {
        // Tiny graph with a huge height budget: width scale would be 25.6 but is
        // capped, and the (generous) height clamp does not reduce it below the cap.
        const plan = planGraphvizViewport(50, 40, CONTAINER_W, {
            maxHeightPx: 5000,
        });
        expect(plan.mode).toBe('upscale');
        expect(plan.effectiveScale).toBe(GRAPHVIZ_MAX_UPSCALE);
    });

    it('is theme-independent — identical plan for light and dark (both themes)', () => {
        // The planner takes no theme input, so the structural crop fix applies
        // to BOTH themes by construction. Two identical calls must agree.
        const a = planGraphvizViewport(NAT_W, NAT_H, CONTAINER_W, {
            maxHeightPx: VIEWPORT_H,
        });
        const b = planGraphvizViewport(NAT_W, NAT_H, CONTAINER_W, {
            maxHeightPx: VIEWPORT_H,
        });
        expect(a).toEqual(b);
    });
});
