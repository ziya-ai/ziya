/**
 * D-130 (G-bd5683) — viewbox-fit-clip, the RESIZE-response sub-case.
 *
 * The label-geometry sub-cases (w2-05 horizontal overrun, w2-13 vertical stroke
 * bisect) were resolved earlier by fitJointLabel/jointLabelFontSize. The residual
 * failure was joint-w2-06: 40 auto-laid-out nodes -> a DirectedGraph 4x10 grid of
 * ~700x1800px. Its labels are ellipsized so they no longer overprint, yet the
 * render clipped column 4 at the right edge and row 10 at the bottom (~28 of 40
 * nodes in frame) in BOTH themes — signature `viewbox-fit-clip`.
 *
 * fitContentToPaper frames the FULL content extent via the viewBox and bounds the
 * paper with computeJointFitPlan. The bug was that the container ResizeObserver
 * re-clobbered that good viewBox with the naive `0 0 containerWidth contentHeight`
 * and translated the paper at 1:1, pinning the frame to the CONTAINER width — so
 * every column past the container width was cropped and the graph carried no
 * downscale. computeJointResizeFit reproduces the correct framing as a pure unit;
 * the observer now uses it.
 *
 * Each assertion first pins the shipped-bug behaviour ("frame = container width",
 * which clips), so the test would FAIL against unpatched code and passes only with
 * the fix.
 */

import { computeJointResizeFit, JOINT_MAX_RENDER_HEIGHT } from '../jointPlugin';

describe('D-130 computeJointResizeFit — viewBox frames full content, never the container box', () => {
    // joint-w2-06: DirectedGraph 4x10 grid, content ~700x1800 at a small offset.
    const bbox = { x: 30, y: 30, width: 700, height: 1800 };
    const padding = 40;
    const containerWidth = 400; // narrower than the content: the clip trigger

    it('frames the FULL content width, not the container width (no column-4 clip)', () => {
        const fit = computeJointResizeFit(bbox, containerWidth, JOINT_MAX_RENDER_HEIGHT, padding);
        const contentWidth = bbox.width + padding * 2; // 780

        // The viewBox must span the whole content extent...
        expect(fit.viewBox.width).toBe(contentWidth);
        expect(fit.viewBox.x).toBe(bbox.x - padding); // -10, origin included

        // ...and crucially it must be at least as wide as the content bbox, so no
        // column falls outside the frame. The pre-fix handler set the frame width
        // to the container width (400 < 780), which is exactly the clip.
        expect(fit.viewBox.width).toBeGreaterThanOrEqual(bbox.width);
        expect(fit.viewBox.width).toBeGreaterThan(containerWidth);
    });

    it('frames the FULL content height, not a container-derived box (no row-10 clip)', () => {
        const fit = computeJointResizeFit(bbox, containerWidth, JOINT_MAX_RENDER_HEIGHT, padding);
        const contentHeight = bbox.height + padding * 2; // 1880
        expect(fit.viewBox.height).toBe(contentHeight);
        expect(fit.viewBox.y).toBe(bbox.y - padding);
        expect(fit.viewBox.height).toBeGreaterThanOrEqual(bbox.height);
    });

    it('bounds the paper to the capture box (downscales the oversized grid instead of cropping)', () => {
        const fit = computeJointResizeFit(bbox, containerWidth, JOINT_MAX_RENDER_HEIGHT, padding);
        // Oversized-in-width content is scaled to fit the container width; the paper
        // never exceeds the capture box, and preserveAspectRatio 'meet' then scales
        // the full viewBox in. (Pre-fix: paper width was pinned to newWidth with a
        // 1:1 translate and no scale, so content beyond newWidth was clipped.)
        expect(fit.paperWidth).toBeLessThanOrEqual(containerWidth);
        // Aspect ratio of the paper matches the framed content (so 'meet' fills it
        // exactly with no letterbox band and no edge clip).
        const contentAspect = (bbox.width + padding * 2) / (bbox.height + padding * 2);
        const paperAspect = fit.paperWidth / fit.paperHeight;
        expect(Math.abs(paperAspect - contentAspect)).toBeLessThan(0.02);
    });

    it('leaves already-fitting content at natural size (small graphs unchanged)', () => {
        const small = { x: 0, y: 0, width: 300, height: 200 };
        const fit = computeJointResizeFit(small, 800, JOINT_MAX_RENDER_HEIGHT, padding);
        // 300+80=380 < 800 and 200+80=280 < 2000 -> natural size, scale 1.
        expect(fit.paperWidth).toBe(380);
        expect(fit.paperHeight).toBe(280);
        expect(fit.viewBox.width).toBe(380);
        expect(fit.viewBox.height).toBe(280);
    });
});
